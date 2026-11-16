from time import sleep

from workspace_sdk import Flow, Input, Output, Step


def split_text(text: str) -> list[str]:
    """Split the sample text into words."""
    sleep(2)
    return text.split()


def count_words(words: list[str]) -> dict[str, int]:
    """Count the words prepared by the previous step."""
    sleep(2)
    return {"words": len(words)}


flow = Flow(
    inputs=("text",),
    steps=(
        Step("Split Text", split_text, {"text": Input("text")}),
        Step("Count Words", count_words, {"words": Output("Split Text")}),
    ),
    output=Output("Count Words"),
)
