from workspace_sdk import Flow, Output, Step


def start() -> dict[str, str]:
    return {"message": "Hello from Workspace"}


flow = Flow(
    inputs=(),
    steps=(Step("Start", start, {}),),
    output=Output("Start"),
)
