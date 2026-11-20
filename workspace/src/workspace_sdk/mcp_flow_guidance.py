FLOW_AUTHORING_GUIDANCE = (
    "Before creating or changing a flow, inspect its flow.py and outline the intended graph. "
    "For each named Step, identify its inputs, output, and what users need to inspect, time, or debug independently. "
    "Use separate steps for distinct stages such as Prepare, Predict, and Decide when they benefit from separate visibility. "
    "Keep closely related operations in the same step as helpers. A single step is appropriate for a single stage. "
    "Use flow.py to wire directly callable Python functions and follow the sheet's existing source layout. "
)
