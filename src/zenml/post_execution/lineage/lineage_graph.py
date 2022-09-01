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

from typing import List, Optional

from pydantic import BaseModel

from zenml.post_execution.lineage.edge import Edge
from zenml.post_execution.lineage.node import (
    ArtifactNode,
    ArtifactNodeDetails,
    BaseNode,
    StepNode,
    StepNodeDetails,
)
from zenml.post_execution.pipeline_run import PipelineRunView
from zenml.post_execution.step import StepView

ARTIFACT_PREFIX = "artifact_"
STEP_PREFIX = "step_"


class LineageGraph(BaseModel):
    """A lineage graph representation of a PipelineRunView."""

    nodes: List[BaseNode] = []
    edges: List[Edge] = []
    root_step_id: Optional[str]

    def generate_step_nodes_and_edges(self, step: StepView):
        step_output_artifacts = list(step.outputs.values())
        execution_id = (
            step_output_artifacts[0].producer_step.id
            if step_output_artifacts
            else step.id
        )
        step_id = STEP_PREFIX + str(step.id)
        if self.root_step_id is None:
            self.root_step_id = step_id
        self.nodes.append(
            StepNode(
                id=step_id,
                data=StepNodeDetails(
                    execution_id=execution_id,
                    entrypoint_name=step.entrypoint_name,  # redundant for consistency
                    name=step.name,  # redundant for consistency
                    parameters=step.parameters,
                    inputs={k: v.uri for k, v in step.inputs.items()},
                    outputs={k: v.uri for k, v in step.outputs.items()},
                ),
            )
        )

        for artifact_name, artifact in step.outputs.items():
            self.nodes.append(
                ArtifactNode(
                    id=ARTIFACT_PREFIX + str(artifact.id),
                    type="artifact",
                    data=ArtifactNodeDetails(
                        execution_id=artifact.id,
                        name=artifact_name,
                        is_cached=artifact.is_cached,
                        artifact_type=artifact.type,
                        artifact_data_type=artifact.data_type,
                        parent_step_id=artifact.parent_step_id,
                        producer_step_id=artifact.producer_step.id,
                        uri=artifact.uri,
                    ),
                )
            )
            self.edges.append(
                Edge(
                    id=STEP_PREFIX
                    + str(step.id)
                    + "_"
                    + ARTIFACT_PREFIX
                    + str(artifact.id),
                    source=STEP_PREFIX + str(step.id),
                    target=ARTIFACT_PREFIX + str(artifact.id),
                )
            )

        for artifact_name, artifact in step.inputs.items():
            self.edges.append(
                Edge(
                    id=STEP_PREFIX
                    + str(step.id)
                    + "_"
                    + ARTIFACT_PREFIX
                    + str(artifact.id),
                    source=ARTIFACT_PREFIX + str(artifact.id),
                    target=STEP_PREFIX + str(step.id),
                )
            )

    def generate_run_nodes_and_edges(self, run: PipelineRunView):
        for step in run.steps:
            self.generate_step_nodes_and_edges(step)


if __name__ == "__main__":
    from zenml.repository import Repository

    run = Repository().get_pipelines()[-1].runs[-1]
    g = LineageGraph()

    g.generate_run_nodes_and_edges(run)
    print([x.dict() for x in g.nodes])
    print("****************************************")
    print([x.dict() for x in g.edges])