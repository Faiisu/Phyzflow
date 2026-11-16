from workspace_sdk import Flow, Input, Output, Step

from nodes.aggregate.entrypoint import aggregate
from nodes.create_batch.entrypoint import create_batch
from nodes.deliver.entrypoint import deliver
from nodes.persist.entrypoint import persist
from nodes.reconcile.entrypoint import reconcile
from nodes.report.entrypoint import report
from nodes.stream_pipeline.entrypoint import stream_pipeline

flow = Flow(
    inputs=("config",),
    steps=(
        Step("Create Batch", create_batch, {"config": Input("config")}),
        Step("Run Parallel Pipeline", stream_pipeline, {"batch": Output("Create Batch")}),
        Step("Reconcile Events", reconcile, {
            "batch": Output("Create Batch"),
            "processed": Output("Run Parallel Pipeline"),
        }),
        Step("Aggregate Alerts", aggregate, {"processed": Output("Run Parallel Pipeline")}),
        Step("Persist Batch", persist, {
            "batch": Output("Create Batch"),
            "audit": Output("Reconcile Events"),
            "aggregates": Output("Aggregate Alerts"),
            "processed": Output("Run Parallel Pipeline"),
        }),
        Step("Deliver Alerts", deliver, {
            "receipt": Output("Persist Batch"),
            "config": Input("config"),
        }),
        Step("Report", report, {
            "processed": Output("Run Parallel Pipeline"),
            "audit": Output("Reconcile Events"),
            "aggregates": Output("Aggregate Alerts"),
            "receipt": Output("Persist Batch"),
            "delivery": Output("Deliver Alerts"),
        }),
    ),
    output=Output("Report"),
)
