import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from workspace_sdk import Flow, NodeSetting, Output, Step, apply_node_settings, run
from workspace_sdk.node_settings import LiveSettings, SettingsStore


class NodeSettingsTests(unittest.TestCase):
    def test_run_snapshot_and_live_checkpoints(self):
        setting = NodeSetting(2, minimum=1, maximum=8, live=True)
        for apply in (True, False):
            with self.subTest(apply=apply), TemporaryDirectory() as temp:
                self.check_snapshot_and_live_checkpoints(setting, apply, Path(temp))

    def check_snapshot_and_live_checkpoints(self, setting, apply, directory):
        def process():
            before = setting.get()
            if apply:
                apply_node_settings()
            return before, setting.get()

        step = Step("Process", process, {}, settings={"Limit": setting})
        flow = Flow((), (step,), Output(step.id))
        store = SettingsStore(directory)
        store.save(step.id, "Limit", 3)
        control = LiveSettings()
        changes = []

        def observe(event):
            if event.get("setting_change"):
                changes.append(event["setting_change"])
            elif event.get("step_id") == step.id and event["status"] == "running":
                store.save(step.id, "Limit", 4)
                queued = control.queue(event["run_id"], step.id, "Limit", 5)
                self.assertEqual(queued["status"], "queued")
                with self.assertRaisesRegex(ValueError, "pending"):
                    control.queue(event["run_id"], step.id, "Limit", 6)

        result = run(flow, {}, observe, settings=store.snapshot(flow.steps), live_settings=control)
        self.assertEqual(result.output, (3, 5 if apply else 3))
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["status"], "applied" if apply else "rejected")
        with self.assertRaisesRegex(ValueError, "not active"):
            control.queue(result.id, step.id, "Limit", 6)
        self.assertEqual(run(flow, {}, settings=store.snapshot(flow.steps)).output, (4, 4))
        self.assertEqual(process(), (2, 2))
