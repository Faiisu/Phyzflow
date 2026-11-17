from workspace_sdk import Flow, Output, Step

from nodes.count_up.entrypoint import count_up
from nodes.multiply_by_ten.entrypoint import multiply_by_ten

flow = Flow(
    inputs=(),
    steps=(
        Step("Count Up", count_up, {}, track_generator=True),
        Step("Multiply by Ten", multiply_by_ten, {"values": Output("Count Up")}),
    ),
    output=Output("Multiply by Ten"),
)
