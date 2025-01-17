#  Copyright (c) ZenML GmbH 2022. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at:
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
#  or implied. See the License for the specific language governing
#  permissions and limitations under the License.
"""Util functions for artifact handling."""

import base64
import os
import tempfile
from typing import TYPE_CHECKING, Any, Optional, Union, cast

from zenml.client import Client
from zenml.constants import MODEL_METADATA_YAML_FILE_NAME
from zenml.enums import StackComponentType, VisualizationType
from zenml.exceptions import DoesNotExistException
from zenml.io import fileio
from zenml.logger import get_logger
from zenml.models.visualization_models import LoadedVisualizationModel
from zenml.stack import StackComponent
from zenml.utils import source_utils
from zenml.utils.yaml_utils import read_yaml, write_yaml

if TYPE_CHECKING:
    from zenml.artifact_stores.base_artifact_store import BaseArtifactStore
    from zenml.config.source import Source
    from zenml.materializers.base_materializer import BaseMaterializer
    from zenml.models import ArtifactResponseModel
    from zenml.zen_stores.base_zen_store import BaseZenStore


logger = get_logger(__name__)

METADATA_DATATYPE = "datatype"
METADATA_MATERIALIZER = "materializer"


def save_model_metadata(model_artifact: "ArtifactResponseModel") -> str:
    """Save a zenml model artifact metadata to a YAML file.

    This function is used to extract and save information from a zenml model artifact
    such as the model type and materializer. The extracted information will be
    the key to loading the model into memory in the inference environment.

    datatype: the model type. This is the path to the model class.
    materializer: the materializer class. This is the path to the materializer class.

    Args:
        model_artifact: the artifact to extract the metadata from.

    Returns:
        The path to the temporary file where the model metadata is saved
    """
    metadata = dict()
    metadata[METADATA_DATATYPE] = model_artifact.data_type
    metadata[METADATA_MATERIALIZER] = model_artifact.materializer

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        write_yaml(f.name, metadata)
    return f.name


def load_model_from_metadata(model_uri: str) -> Any:
    """Load a zenml model artifact from a json file.

    This function is used to load information from a Yaml file that was created
    by the save_model_metadata function. The information in the Yaml file is
    used to load the model into memory in the inference environment.

    Args:
        model_uri: the artifact to extract the metadata from.

    Returns:
        The ML model object loaded into memory.
    """
    # Load the model from its metadata
    with fileio.open(
        os.path.join(model_uri, MODEL_METADATA_YAML_FILE_NAME), "r"
    ) as f:
        metadata = read_yaml(f.name)
    data_type = metadata[METADATA_DATATYPE]
    materializer = metadata[METADATA_MATERIALIZER]
    model = _load_artifact(
        materializer=materializer, data_type=data_type, uri=model_uri
    )

    # Switch to eval mode if the model is a torch model
    try:
        import torch.nn as nn

        if isinstance(model, nn.Module):
            model.eval()
    except ImportError:
        pass

    return model


def load_artifact(artifact: "ArtifactResponseModel") -> Any:
    """Load the given artifact into memory.

    Args:
        artifact: The artifact to load.

    Returns:
        The artifact loaded into memory.
    """
    artifact_store_loaded = False
    if artifact.artifact_store_id:
        try:
            artifact_store_model = Client().get_stack_component(
                component_type=StackComponentType.ARTIFACT_STORE,
                name_id_or_prefix=artifact.artifact_store_id,
            )
            _ = StackComponent.from_model(artifact_store_model)
            artifact_store_loaded = True
        except KeyError:
            pass

    if not artifact_store_loaded:
        logger.warning(
            "Unable to restore artifact store while trying to load artifact "
            "`%s`. If this artifact is stored in a remote artifact store, "
            "this might lead to issues when trying to load the artifact.",
            artifact.id,
        )

    return _load_artifact(
        materializer=artifact.materializer,
        data_type=artifact.data_type,
        uri=artifact.uri,
    )


def _load_artifact(
    materializer: Union["Source", str],
    data_type: Union["Source", str],
    uri: str,
) -> Any:
    """Load an artifact using the given materializer.

    Args:
        materializer: The source of the materializer class to use.
        data_type: The source of the artifact data type.
        uri: The uri of the artifact.

    Returns:
        The artifact loaded into memory.

    Raises:
        ModuleNotFoundError: If the materializer or data type cannot be found.
    """
    # Resolve the materializer class
    try:
        materializer_class = source_utils.load(materializer)
    except (ModuleNotFoundError, AttributeError) as e:
        logger.error(
            f"ZenML cannot locate and import the materializer module "
            f"'{materializer}' which was used to write this artifact."
        )
        raise ModuleNotFoundError(e) from e

    # Resolve the artifact class
    try:
        artifact_class = source_utils.load(data_type)
    except (ModuleNotFoundError, AttributeError) as e:
        logger.error(
            f"ZenML cannot locate and import the data type of this "
            f"artifact '{data_type}'."
        )
        raise ModuleNotFoundError(e) from e

    # Load the artifact
    logger.debug(
        "Using '%s' to load artifact of type '%s' from '%s'.",
        materializer_class.__qualname__,
        artifact_class.__qualname__,
        uri,
    )
    materializer_object: BaseMaterializer = materializer_class(uri)
    artifact = materializer_object.load(artifact_class)
    logger.debug("Artifact loaded successfully.")

    return artifact


def load_artifact_visualization(
    artifact: "ArtifactResponseModel",
    index: int = 0,
    zen_store: Optional["BaseZenStore"] = None,
    encode_image: bool = False,
) -> LoadedVisualizationModel:
    """Load a visualization of the given artifact.

    Args:
        artifact: The artifact to visualize.
        index: The index of the visualization to load.
        zen_store: The ZenStore to use for finding the artifact store. If not
            provided, the ZenStore of the client will be used.
        encode_image: Whether to base64 encode image visualizations.

    Returns:
        The loaded visualization.

    Raises:
        DoesNotExistException: If the artifact does not have the requested
            visualization or if the visualization was not found in the artifact
            store.
    """
    # Get the visualization to load
    if not artifact.visualizations:
        raise DoesNotExistException(
            f"Artifact '{artifact.id}' has no visualizations."
        )
    if index < 0 or index >= len(artifact.visualizations):
        raise DoesNotExistException(
            f"Artifact '{artifact.id}' only has {len(artifact.visualizations)} "
            f"visualizations, but index {index} was requested."
        )
    visualization = artifact.visualizations[index]

    # Load the visualization from the artifact's artifact store
    artifact_store = _load_artifact_store_of_artifact(
        artifact=artifact, zen_store=zen_store
    )
    mode = "rb" if visualization.type == VisualizationType.IMAGE else "r"
    value = _load_file_from_artifact_store(
        uri=visualization.uri,
        artifact_store=artifact_store,
        mode=mode,
    )

    # Encode image visualizations if requested
    if visualization.type == VisualizationType.IMAGE and encode_image:
        value = base64.b64encode(bytes(value))

    return LoadedVisualizationModel(type=visualization.type, value=value)


def _load_artifact_store_of_artifact(
    artifact: "ArtifactResponseModel",
    zen_store: Optional["BaseZenStore"] = None,
) -> "BaseArtifactStore":
    """Load the artifact store of the given artifact.

    Args:
        artifact: The artifact for which to load the artifact store.
        zen_store: The ZenStore to use for finding the artifact store. If not
            provided, the client's ZenStore will be used.

    Returns:
        The artifact store of the given artifact.

    Raises:
        DoesNotExistException: If the artifact does not have an artifact store.
        NotImplementedError: If the artifact store could not be loaded.
    """
    if not artifact.artifact_store_id:
        raise DoesNotExistException(
            f"Artifact '{artifact.id}' cannot be loaded because the underlying "
            "artifact store was deleted."
        )

    if zen_store is None:
        zen_store = Client().zen_store

    artifact_store_model = zen_store.get_stack_component(
        artifact.artifact_store_id
    )

    try:
        artifact_store = cast(
            "BaseArtifactStore",
            StackComponent.from_model(artifact_store_model),
        )
    except ImportError:
        link = "https://docs.zenml.io/component-gallery/artifact-stores/custom#enabling-artifact-visualizations-with-custom-artifact-stores"
        raise NotImplementedError(
            f"Artifact '{artifact.id}' could not be loaded because the "
            f"underlying artifact store '{artifact_store_model.name}' "
            f"could not be instantiated. This is likely because the "
            f"artifact store's dependencies are not installed. For more "
            f"information, see {link}."
        )
    return artifact_store


def _load_file_from_artifact_store(
    uri: str,
    artifact_store: "BaseArtifactStore",
    mode: str = "rb",
) -> Any:
    """Load the given uri from the given artifact store.

    Args:
        uri: The uri of the file to load.
        artifact_store: The artifact store from which to load the file.
        mode: The mode in which to open the file.

    Returns:
        The loaded file.

    Raises:
        DoesNotExistException: If the file does not exist in the artifact store.
        NotImplementedError: If the artifact store cannot open the file.
    """
    try:
        with artifact_store.open(uri, mode) as text_file:
            return text_file.read()
    except FileNotFoundError:
        raise DoesNotExistException(
            f"File '{uri}' does not exist in artifact store "
            f"'{artifact_store.name}'."
        )
    except Exception as e:
        logger.exception(e)
        link = "https://docs.zenml.io/component-gallery/artifact-stores/custom#enabling-artifact-visualizations-with-custom-artifact-stores"
        raise NotImplementedError(
            f"File '{uri}' could not be loaded because the underlying artifact "
            f"store '{artifact_store.name}' could not open the file. This is "
            f"likely because the authentication credentials are not configured "
            f"in the artifact store itself. For more information, see {link}."
        )
