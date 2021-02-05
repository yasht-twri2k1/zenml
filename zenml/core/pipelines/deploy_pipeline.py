#  Copyright (c) maiot GmbH 2020. All Rights Reserved.
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

from typing import Dict, Text, Any, List
from typing import Optional

from tfx.components.common_nodes.importer_node import ImporterNode
from tfx.components.pusher.component import Pusher
from tfx.types import standard_artifacts

from zenml.core.backends.orchestrator.base.orchestrator_base_backend import \
    OrchestratorBaseBackend
from zenml.core.datasources.base_datasource import BaseDatasource
from zenml.core.metadata.metadata_wrapper import ZenMLMetadataStore
from zenml.core.pipelines.base_pipeline import BasePipeline
from zenml.core.repo.artifact_store import ArtifactStore
from zenml.core.standards import standard_keys as keys
from zenml.core.steps.base_step import BaseStep
from zenml.core.steps.deployer.base_deployer import BaseDeployerStep
from zenml.utils.enums import GDPComponent


class DeploymentPipeline(BasePipeline):
    """BatchInferencePipeline definition to run batch inference pipelines.

    A BatchInferencePipeline is used to run an inference based on a
    TrainingPipeline.
    """
    PIPELINE_TYPE = 'deploy'

    def __init__(self,
                 model_uri: Text,
                 name: Text = None,
                 enable_cache: Optional[bool] = True,
                 steps_dict: Dict[Text, BaseStep] = None,
                 backend: OrchestratorBaseBackend = None,
                 metadata_store: Optional[ZenMLMetadataStore] = None,
                 artifact_store: Optional[ArtifactStore] = None,
                 datasource: Optional[BaseDatasource] = None,
                 pipeline_name: Optional[Text] = None):
        """
        Construct a deployment pipeline. This is a pipeline that deploys a
        model to a target. Can be controlled via a DeployerStep.

        Args:
            name: Outward-facing name of the pipeline.
            model_uri: URI for a model, usually generated by
            TrainingPipeline and retrieved by
            `training_pipeline.get_model_uri()`.
            pipeline_name: A unique name that identifies the pipeline after
             it is run.
            enable_cache: Boolean, indicates whether or not caching
             should be used.
            steps_dict: Optional dict of steps.
            backend: Orchestrator backend.
            metadata_store: Configured metadata store. If None,
             the default metadata store is used.
            artifact_store: Configured artifact store. If None,
             the default artifact store is used.
        """
        if model_uri is None:
            raise AssertionError('model_uri cannot be None.')
        self.model_uri = model_uri
        super(DeploymentPipeline, self).__init__(
            name=name,
            enable_cache=enable_cache,
            steps_dict=steps_dict,
            backend=backend,
            metadata_store=metadata_store,
            artifact_store=artifact_store,
            datasource=datasource,
            pipeline_name=pipeline_name,
            model_uri=model_uri,
        )

    def get_tfx_component_list(self, config: Dict[Text, Any]) -> List:
        """
        Creates an inference pipeline out of TFX components.

        A inference pipeline is used to run a batch of data through a
        ML model via the BulkInferrer TFX component.

        Args:
            config: Dict. Contains a ZenML configuration used to build the
             data pipeline.

        Returns:
            A list of TFX components making up the data pipeline.
        """
        component_list = []

        # Load from model_uri
        model = ImporterNode(
            instance_name=GDPComponent.Trainer.name,
            source_uri=self.model_uri,
            artifact_type=standard_artifacts.Model)
        model_result = model.outputs.result

        deployer: BaseDeployerStep = \
            self.steps_dict[keys.TrainingSteps.DEPLOYER]
        pusher_config = deployer._build_pusher_args()
        pusher_executor_spec = deployer._get_executor_spec()
        pusher = Pusher(model_export=model_result,
                        custom_executor_spec=pusher_executor_spec,
                        **pusher_config).with_id(
            GDPComponent.Deployer.name)

        component_list.extend([model, pusher])
        return component_list

    def add_deployment(self, deployment_step: BaseDeployerStep):
        self.steps_dict[keys.TrainingSteps.DEPLOYER] = deployment_step

    def steps_completed(self) -> bool:
        mandatory_steps = [keys.TrainingSteps.DEPLOYER]
        for step_name in mandatory_steps:
            if step_name not in self.steps_dict.keys():
                raise AssertionError(
                    f'Mandatory step {step_name} not added.')
        return True