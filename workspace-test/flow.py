from ticket_demo.entrypoint import decide, predict, prepare
from workspace_sdk import Flow, Input, Output, Step

flow = Flow(
    inputs=("text",),
    steps=(
        Step("Prepare", prepare, {"text": Input("text")}),
        Step("Predict", predict, {"prepared": Output("Prepare")}),
        Step("Decide", decide, {"prepared": Output("Prepare"), "category": Output("Predict")}),
    ),
    output=Output("Decide"),
)
