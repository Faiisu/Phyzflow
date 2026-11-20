import unittest
from typing import cast
from unittest.mock import patch

import torch

from workspace_sdk import Flow, Output, Step, record_metrics, run


class RunnerTests(unittest.TestCase):
    def test_node_duration_excludes_time_between_generator_resumptions(self):
        now_ns = 0

        def advance(milliseconds):
            nonlocal now_ns
            now_ns += milliseconds * 1_000_000

        def produce():
            advance(2)
            yield 1
            advance(4)
            yield 2
            advance(3)

        def consume(values):
            advance(100)
            first = next(values)
            advance(10)
            return [first, *values]

        flow = Flow(
            inputs=(),
            steps=(Step("Produce", produce, {}, track_generator=True),
                   Step("Consume", consume, {"values": Output("Produce")})),
            output=Output("Consume"),
        )
        events = []
        with patch("workspace_sdk.runner.perf_counter_ns", side_effect=lambda: now_ns):
            result = run(flow, {}, events.append)

        self.assertEqual(result.output, [1, 2])
        durations = {event["step_id"]: event["duration_ms"] for event in events if "duration_ms" in event}
        self.assertEqual(durations, {"Produce": 9, "Consume": 119})

    def test_runner_closes_a_partially_consumed_tracked_generator(self):
        closed = []

        def produce():
            try:
                yield 1
                yield 2
            finally:
                closed.append(True)

        def first(values):
            return next(values)

        flow = Flow(
            inputs=(),
            steps=(Step("Produce", produce, {}, track_generator=True),
                   Step("First", first, {"values": Output("Produce")})),
            output=Output("First"),
        )
        events = []
        result = run(flow, {}, events.append)
        self.assertEqual(result.output, 1)
        self.assertEqual(closed, [True])
        self.assertEqual(
            [event["status"] for event in events if event.get("step_id") == "Produce"],
            ["running", "stopped"],
        )

    def test_operational_metrics_are_bounded_and_scoped_to_reporting_nodes(self):
        def report():
            record_metrics("x" * 49, {"Count": 1})
            record_metrics("Queue", cast(dict[str, int | float], {
                "Capacity": 0, "Wait": 1.25, "Raw": "private", "Infinite": float("inf"),
                "Invalid count": True, "Large count": 2**53, "x" * 49: 10,
                "Blocked": 4, "Overflow": 1,
            }))
            for index in range(20):
                record_metrics(f"Worker {index}", {"Overlap": index})
            return "private payload"

        def inspect(received):
            self.assertEqual(received, "private payload")
            return None

        flow = Flow(
            inputs=(),
            steps=(Step("Report", report, {}), Step("Inspect", inspect, {"received": Output("Report")})),
            output=Output("Inspect"),
        )
        for include_metadata in (True, False):
            with self.subTest(include_metadata=include_metadata):
                events = []
                self.assertEqual(run(flow, {}, events.append, include_metadata=include_metadata).status, "succeeded")
                reported = [event for event in events if "metrics" in event]
                if include_metadata:
                    self.assertEqual([event["step_id"] for event in reported], ["Report"])
                    metrics = reported[0]["metrics"]
                    self.assertEqual(list(metrics), ["Queue", *(f"Worker {index}" for index in range(7))])
                    self.assertEqual(metrics["Queue"], {"Capacity": 0, "Wait": 1.25, "Blocked": 4})
                else:
                    self.assertEqual(reported, [])
                self.assertFalse(any("output_sample" in event or "input_sample" in event for event in events))

    def test_failed_node_reports_metrics_collected_before_the_error(self):
        def check():
            record_metrics("Queue", {"Blocked": 3})
            raise RuntimeError("private failure")

        flow = Flow(inputs=(), steps=(Step("Check", check, {}),), output=Output("Check"))
        events = []
        self.assertEqual(run(flow, {}, events.append, include_metadata=True).status, "failed")
        failed = next(event for event in events if event.get("step_id") == "Check" and event["status"] == "failed")
        self.assertEqual(failed["metrics"], {"Queue": {"Blocked": 3}})

    def test_sample_capture_leaves_unsupported_objects_untouched(self):
        class MutatingList(list):
            def __iter__(self):
                self.clear()
                return super().__iter__()

        items = MutatingList([1, 2])

        def produce():
            return {"items": items}

        def inspect(received):
            self.assertIs(received["items"], items)
            self.assertEqual(items, [1, 2])
            return None

        flow = Flow(
            inputs=(),
            steps=(
                Step("Produce", produce, {}, capture_samples=True),
                Step("Inspect", inspect, {"received": Output("Produce")}, capture_samples=True),
            ),
            output=Output("Inspect"),
        )
        events = []
        result = run(flow, {}, events.append, include_metadata=True)
        self.assertEqual(result.status, "succeeded")
        samples = {event["step_id"]: event["output_sample"] for event in events if "output_sample" in event}
        self.assertEqual(samples, {"Produce": None, "Inspect": "null"})

    def test_runner_preserves_cpu_tensor_identity_and_device(self):
        tensor = torch.tensor([[1.0, 2.0]], device="cpu")

        def produce():
            return tensor

        def inspect(received):
            self.assertIs(received, tensor)
            self.assertEqual(received.device, torch.device("cpu"))
            return received

        flow = Flow(
            inputs=(),
            steps=(
                Step("Produce", produce, {}),
                Step("Inspect", inspect, {"received": Output("Produce")}),
            ),
            output=Output("Inspect"),
        )
        result = run(flow, {})
        self.assertEqual(result.status, "succeeded")
        self.assertIs(result.output, tensor)
