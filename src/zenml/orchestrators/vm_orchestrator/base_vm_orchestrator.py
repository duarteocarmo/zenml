# Copyright 2019 Google LLC. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#  Copyright (c) ZenML GmbH 2022. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at:
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
#  or implied. See the License for the specific language governing
#  permissions and limitations under the License.

import os
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional, Tuple, cast

from pydantic import BaseModel

from zenml.constants import ORCHESTRATOR_DOCKER_IMAGE_KEY
from zenml.enums import StackComponentType, VMState
from zenml.environment import Environment
from zenml.logger import get_logger
from zenml.orchestrators import BaseOrchestrator
from zenml.orchestrators.vm_orchestrator.base_entrypoint_configuration import (
    VMEntrypointConfiguration,
)
from zenml.repository import Repository
from zenml.stack import Stack, StackComponentConfig, StackValidator
from zenml.utils.docker_utils import get_image_digest
from zenml.utils.pipeline_docker_image_builder import PipelineDockerImageBuilder
from zenml.utils.source_utils import get_source_root_path

if TYPE_CHECKING:
    from zenml.config.pipeline_deployment import PipelineDeployment
    from zenml.pipelines.base_pipeline import BasePipeline
    from zenml.stack import Stack

logger = get_logger(__name__)

STREAM_LOGS_POLLING_INTERVAL_SECS = 10


class VMInstanceView(BaseModel):
    id: str
    name: str
    status: VMState


class BaseVMOrchestratorConfig(StackComponentConfig):
    """Base VM orchestrator config."""

    pass


class BaseVMOrchestrator(BaseOrchestrator):
    """Base VM orchestrator interface for all VM-based orchestrators."""

    @property
    def config(self) -> VMEntrypointConfiguration:
        """Returns the `GCPVMEntrypointConfiguration` config.

        Returns:
            The configuration.
        """
        return cast(VMEntrypointConfiguration, self._config)

    @property
    def validator(self) -> Optional[StackValidator]:
        """Ensures a stack with only remote components and a container registry.

        Returns:
            A `StackValidator` instance.
        """

        def _validate(stack: "Stack") -> Tuple[bool, str]:
            container_registry = stack.container_registry

            # should not happen, because the stack validation takes care of
            # this, but just in case
            assert container_registry is not None

            # go through all stack components and identify those that
            # advertise a local path where they persist information that
            # they need to be available when running pipelines.
            for stack_component in stack.components.values():
                local_path = stack_component.local_path
                if local_path is None:
                    continue
                return False, (
                    f"The VM orchestrator is configured to run "
                    f"pipelines in a remote Kubernetes cluster designated "
                    f"by the '{self.config.kubernetes_context}' configuration "
                    f"context, but the '{stack_component.name}' "
                    f"{stack_component.type.value} is a local stack component "
                    f"and will not be available in the pipeline."
                    f"\nPlease ensure that you always use non-local "
                    f"stack components with a VM orchestrator, "
                    f"otherwise you may run into pipeline execution "
                    f"problems. You should use a flavor of "
                    f"{stack_component.type.value} other than "
                    f"'{stack_component.flavor}'."
                )

            if container_registry.config.is_local:
                return False, (
                    f"The VM orchestrator is configured to run "
                    f"pipelines using a container image accessible to a"
                    f"remote VM, but the '{container_registry.name}' "
                    f"container registry URI '{container_registry.config.uri}' "
                    f"points to a local container registry. Please ensure "
                    f"that you always use non-local stack components with "
                    f"a VM orchestrator, otherwise you will "
                    f"run into problems. You should use a flavor of "
                    f"container registry other than "
                    f"'{container_registry.flavor}'."
                )

            return True, ""

        return StackValidator(
            required_components={StackComponentType.CONTAINER_REGISTRY},
            custom_validation_function=_validate,
        )

    @property
    def is_running(self) -> bool:
        """Returns True if the orchestrator is running
        a pipeline, else False.
        """
        instance = self.get_instance()
        return instance.status == VMState.RUNNING

    @abstractmethod
    def launch_instance(
        image_name: str, command: str, arguments: str, **kwargs: Any
    ) -> VMInstanceView:
        """Defines launching a VM.

        Returns:
            A `VMInstanceView` with metadata of launched VM.
        """

    def get_instance(**kwargs: Any) -> VMInstanceView:
        """Returns the launched instance"""
        pass

    def get_logs_url(**kwargs: Any) -> Optional[str]:
        """Returns the logs url if instance is running."""
        pass

    def stream_logs(seconds_before: int, **kwargs: Any):
        """Streams logs onto the logger"""
        pass

    def prepare_pipeline_deployment(
        self,
        deployment: "PipelineDeployment",
        stack: "Stack",
    ) -> None:
        """Build a Docker image and push it to the container registry.

        Args:
            deployment: The pipeline deployment configuration.
            stack: The stack on which the pipeline will be deployed.
        """
        docker_image_builder = PipelineDockerImageBuilder()
        repo_digest = docker_image_builder.build_and_push_docker_image(
            deployment=deployment, stack=stack
        )
        deployment.add_extra(ORCHESTRATOR_DOCKER_IMAGE_KEY, repo_digest)

    def prepare_or_run_pipeline(
        self,
        deployment: "PipelineDeployment",
        stack: "Stack",
    ) -> Any:
        """Runs the pipeline on a GCP VM orchestrator.

        This function first compiles the ZenML pipeline into a Tekton yaml
        and then applies this configuration to run the pipeline.

        Args:
            deployment: The pipeline deployment to prepare or run.
            stack: The stack the pipeline will run on.

        Raises:
            RuntimeError: If you try to run the pipelines in a notebook environment.
        """
        # First check whether the code running in a notebook
        if Environment.in_notebook():
            raise RuntimeError(
                "The VM orchestrator cannot run pipelines in a notebook "
                "environment. The reason is that it is non-trivial to create "
                "a Docker image of a notebook. Please consider refactoring "
                "your notebook cells into separate scripts in a Python module "
                "and run the code outside of a notebook when using this "
                "orchestrator."
            )

        if deployment.schedule:
            logger.warning(
                "The VM does not support the "
                "use of schedules. The `schedule` will be ignored "
                "and the pipeline will be run immediately."
            )

        image_name = deployment.pipeline.extra[ORCHESTRATOR_DOCKER_IMAGE_KEY]

        command = VMEntrypointConfiguration.get_entrypoint_command()
        arguments = VMEntrypointConfiguration.get_entrypoint_arguments(
            step_name=step_name,
            **{RUN_NAME_OPTION: run_name},
        )

        # Launch the instance
        instance = self.launch_instance(
            image_name=image_name, command=command, arguments=arguments
        )

        # Resolve the logs url
        logger.info(
            f"Instance {instance.id} is now running the pipeline. "
            "Logs will be streamed soon. "
        )
        logs_url = self.get_logs_url()
        if logs_url:
            logger.info(f"You can also view the logs directly at: {logs_url}")

        try:
            # While VM is running, stream the logs
            while self.is_running:
                self.stream_logs(
                    seconds_before=STREAM_LOGS_POLLING_INTERVAL_SECS
                )
                time.sleep(STREAM_LOGS_POLLING_INTERVAL_SECS)
            self.stream_logs(
                seconds_before=STREAM_LOGS_POLLING_INTERVAL_SECS * 2
            )
        except KeyboardInterrupt:
            logger.info(f"Keyboard interupt detected! Exiting logs streaming.")
            if logs_url:
                logger.info(f"Please view logs directly at {logs_url}")