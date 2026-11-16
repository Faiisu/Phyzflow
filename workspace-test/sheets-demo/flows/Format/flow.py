from time import sleep

from workspace_sdk import Flow, Input, Output, Step


def clean(text: str) -> str:
    """Trim the sample text."""
    sleep(2)
    return text.strip()


def format_text(cleaned: str) -> dict[str, str]:
    """Format the cleaned text for display."""
    sleep(2)
    return {"formatted": cleaned.title()}


flow = Flow(
    inputs=("text",),
    steps=(
        Step("Clean Text", clean, {"text": Input("text")}),
        Step("Format Text", format_text, {"cleaned": Output("Clean Text")}),
    ),
    output=Output("Format Text"),
)
