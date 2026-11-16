from workspace_sdk import Flow, Output, Step

from nodes.start.entrypoint import start

flow = Flow(
    inputs=(),
    steps=(Step("Start", start, {}),),
    output=Output("Start"),
)
