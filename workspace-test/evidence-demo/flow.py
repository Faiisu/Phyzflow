from ticket_demo.entrypoint import decide, predict, prepare
from workspace_sdk import Flow, Input, Output, Step, record_reason


def predict_with_evidence(prepared: str) -> str:
    category = predict(prepared)
    record_reason("The demo predictor assigns every ticket to billing.")
    return category


flow = Flow(
    inputs=("text",),
    steps=(
        Step("Prepare", prepare, {"text": Input("text")}),
        Step("Predict", predict_with_evidence, {"prepared": Output("Prepare")}, capture_samples=True),
        Step("Decide", decide, {"prepared": Output("Prepare"), "category": Output("Predict")}),
    ),
    output=Output("Decide"),
)
