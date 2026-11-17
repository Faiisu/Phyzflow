from workspace_sdk import Flow, Input, Output, Step

from nodes.collect.entrypoint import collect
from nodes.summarize.entrypoint import summarize


flow = Flow(
    inputs=("count",),
    steps=(
        Step("Collect readings", collect, {"count": Input("count")}),
        Step("Summarize readings", summarize, {"readings": Output("Collect readings")}),
    ),
    output=Output("Summarize readings"),
)
