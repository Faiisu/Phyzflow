from functools import cache
from pathlib import Path
import pickle

from workspace_sdk import Flow, Input, Output, Step


@cache
def load_classifier():
    # Only load the artifact trained inside this image from the bundled examples.
    with Path(__file__).with_name("model.pkl").open("rb") as artifact:
        return pickle.load(artifact)


def prepare(text: str) -> str:
    return text.strip().lower()


def predict(prepared: str) -> str:
    if prepared.startswith("debug failure:"):
        raise RuntimeError(prepared)
    return str(load_classifier().predict([prepared])[0])


def decide(prepared: str, category: str) -> dict[str, str]:
    priority = "urgent" if "urgent" in prepared else "standard"
    return {"category": category, "priority": priority}


flow = Flow(
    inputs=("text",),
    steps=(
        Step("Prepare", prepare, {"text": Input("text")}),
        Step("Predict", predict, {"prepared": Output("Prepare")}),
        Step("Decide", decide, {"prepared": Output("Prepare"), "category": Output("Predict")}),
    ),
    output=Output("Decide"),
)
