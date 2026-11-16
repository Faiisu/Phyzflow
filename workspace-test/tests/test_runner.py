import unittest

import torch

from workspace_sdk import Flow, Output, Step, run


class RunnerTests(unittest.TestCase):
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
