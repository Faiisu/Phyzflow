import unittest

import torch

from workspace_sdk import Flow, Output, Step, run


class RunnerTests(unittest.TestCase):
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
