from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
from stat import S_IMODE
from tempfile import TemporaryDirectory
from time import sleep
import unittest

from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, sign_in
from test_application_lifecycle import create_workspace, wait_for
from workspace_sdk.console import ConsoleLogStore
from workspace_sdk.sheets import create_sheet


def console_entries(log_dir: Path) -> list[dict]:
    entries = []
    for segment in sorted(log_dir.glob("*.ndjson")):
        for line in segment.read_text(encoding="utf-8").splitlines():
            if line:
                entries.append(json.loads(line))
    return entries


def combined_stream(entries: list[dict], stream: str) -> str:
    return "".join(entry["text"] for entry in entries if entry["stream"] == stream)


class ApplicationConsoleLogsTests(unittest.TestCase):
    def test_default_settings_save_console_logs_per_run_and_restore_after_reopening(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            root = parent / "ConsoleDefaults"
            state = parent / "state"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                (root / "nodes/start/entrypoint.py").write_text(
                    r'''import os
import subprocess
import sys

def start(secret_input):
    os.write(1, b"alpha ")
    os.write(1, "ก".encode("utf-8")[:2])
    os.write(1, "ก".encode("utf-8")[2:] + b" beta\n")
    print(f"Main stdout {len(secret_input)}")
    os.write(2, b"warn ")
    subprocess.run([sys.executable, "-c", 'import os; os.write(2, b"child stderr\\n")'], check=True)
    return {"secret_output": "OUTPUT_MARKER"}
''',
                    encoding="utf-8",
                )
                (root / "flow.py").write_text(
                    "from workspace_sdk import Flow, Input, Output, Step\n"
                    "from nodes.start.entrypoint import start\n"
                    'flow = Flow(("secret_input",), (Step("Start", start, {"secret_input": Input("secret_input")}),), Output("Start"))\n',
                    encoding="utf-8",
                )
                (root / "sample.json").write_text(
                    json.dumps({"input": {"secret_input": "INPUT_MARKER"}}),
                    encoding="utf-8",
                )
                workspace.click()
                workspace_url = page.url
                flow = page.frame_locator('iframe[title="Main flow"]')
                save_logs = flow.get_by_label("Save console logs")
                retention = flow.get_by_label("Retention (days)")
                expect(save_logs).to_be_visible()
                expect(save_logs).to_be_checked()
                expect(retention).to_have_value("7")

                page.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                console = flow.get_by_role("region", name="Run console")
                expect(console.get_by_label("stdout")).to_contain_text("alpha ก beta")
                expect(console.get_by_label("stdout")).to_contain_text("Main stdout 12")
                expect(console.get_by_label("stderr")).to_contain_text("warn child stderr")

                run_id = page.request.get(workspace_url + "workspace").json()["flows"][0]["run_id"]
                self.assertIsNotNone(run_id)
                view = page.locator('iframe[title="Main flow"]').content_frame.locator("html").evaluate(
                    "document => document.baseURI"
                ).rstrip("/")
                guest = page.context.browser.new_context()
                try:
                    self.assertEqual(guest.request.get(f"{view}/console-logs").status, 401)
                    self.assertEqual(guest.request.get(f"{view}/console-logs/{run_id}").status, 401)
                    self.assertEqual(guest.request.post(f"{view}/console-logs", headers={"Origin": url}).status, 401)
                finally:
                    guest.close()
                for headers in ({}, {"Origin": "https://untrusted.example"}):
                    self.assertEqual(
                        page.request.post(f"{view}/console-logs", data={"save_logs": False}, headers=headers).status,
                        403,
                    )

                log_dir = root / ".workspace" / "logs" / run_id
                wait_for(lambda: log_dir.is_dir() and any(log_dir.glob("*.ndjson")))
                self.assertEqual(S_IMODE((root / ".workspace").stat().st_mode), 0o700)
                self.assertEqual(S_IMODE(log_dir.stat().st_mode), 0o700)
                segments = list(log_dir.glob("*.ndjson"))
                self.assertEqual(len(segments), 1)
                self.assertEqual(S_IMODE(segments[0].stat().st_mode), 0o600)

                entries = console_entries(log_dir)
                self.assertTrue(all(entry["run_id"] == run_id for entry in entries))
                self.assertEqual(combined_stream(entries, "stdout"), "alpha ก beta\nMain stdout 12\n")
                self.assertEqual(combined_stream(entries, "stderr"), "warn child stderr\n")
                raw_log = segments[0].read_text(encoding="utf-8")
                self.assertNotIn("INPUT_MARKER", raw_log)
                self.assertNotIn("OUTPUT_MARKER", raw_log)

                history_raw = (root / ".workspace" / "runs" / f"{run_id}.json").read_text(encoding="utf-8")
                self.assertNotIn("alpha ก beta", history_raw)
                self.assertNotIn("child stderr", history_raw)

                logs_payload = page.request.get(f"{view}/console-logs/{run_id}").json()
                self.assertEqual(logs_payload["settings"], {"save_logs": True, "retention_days": 7})
                self.assertEqual(logs_payload["run_id"], run_id)
                self.assertEqual(combined_stream(logs_payload["events"], "stdout"), "alpha ก beta\nMain stdout 12\n")
                self.assertEqual(combined_stream(logs_payload["events"], "stderr"), "warn child stderr\n")

                page.get_by_role("button", name="Reload flow").click()
                expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                expect(save_logs).to_be_checked()
                expect(retention).to_have_value("7")
                expect(console.get_by_label("stdout")).to_contain_text("alpha ก beta")
                expect(console.get_by_label("stdout")).to_contain_text("Main stdout 12")
                expect(console.get_by_label("stderr")).to_contain_text("warn child stderr")

                page.goto(url)
                page.get_by_role("link", name="ConsoleDefaults").click()
                expect(flow.get_by_role("button", name="Inspect Start")).to_be_visible()
                expect(console.get_by_label("stdout")).to_contain_text("alpha ก beta")
                expect(console.get_by_label("stderr")).to_contain_text("warn child stderr")

            with application(state) as url:
                sign_in(page, url, state)
                page.get_by_role("link", name="ConsoleDefaults").click()
                flow = page.frame_locator('iframe[title="Main flow"]')
                console = flow.get_by_role("region", name="Run console")
                expect(flow.get_by_label("Save console logs")).to_be_checked()
                expect(flow.get_by_label("Retention (days)")).to_have_value("7")
                expect(console.get_by_label("stdout")).to_contain_text("alpha ก beta")
                expect(console.get_by_label("stdout")).to_contain_text("Main stdout 12")
                expect(console.get_by_label("stderr")).to_contain_text("warn child stderr")

    def test_disabling_saving_keeps_live_output_without_writing_logs_per_sheet(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            root = parent / "ConsoleDisabled"
            state = parent / "state"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                (root / "nodes/start/entrypoint.py").write_text(
                    r'''from pathlib import Path
import sys
from time import monotonic, sleep

def start():
    print("Main before toggle", flush=True)
    print("Main stderr before toggle", file=sys.stderr, flush=True)
    deadline = monotonic() + 15
    while not Path("toggle-done").exists() and monotonic() < deadline:
        sleep(.02)
    if Path("toggle-done").exists():
        print("Main after toggle", flush=True)
        print("Main stderr after toggle", file=sys.stderr, flush=True)
    return "main ok"
''',
                    encoding="utf-8",
                )
                secondary_flow = create_sheet(root, "Secondary")
                secondary_root = secondary_flow.parent
                (secondary_root / "nodes/start/entrypoint.py").write_text(
                    r'''import sys

def start():
    print("Secondary stdout", flush=True)
    print("Secondary stderr", file=sys.stderr, flush=True)
    return "secondary ok"
''',
                    encoding="utf-8",
                )
                workspace.click()
                workspace_url = page.url
                main = page.frame_locator('iframe[title="Main flow"]')
                main_save = main.get_by_label("Save console logs")
                main_retention = main.get_by_label("Retention (days)")
                expect(main_save).to_be_checked()
                expect(main_retention).to_have_value("7")

                page.get_by_role("button", name="Run flow").click()
                main_console = main.get_by_role("region", name="Run console")
                expect(main_console.get_by_label("stdout")).to_contain_text("Main before toggle")
                expect(main_console.get_by_label("stderr")).to_contain_text("Main stderr before toggle")

                first_run_id = page.request.get(workspace_url + "workspace").json()["flows"][0]["run_id"]
                main_save.uncheck()
                expect(main.locator("#console-settings-message")).to_contain_text("Saved")
                self.assertEqual(
                    json.loads((root / ".workspace" / "console-settings.json").read_text(encoding="utf-8")),
                    {"save_logs": False, "retention_days": 7},
                )
                self.assertEqual(S_IMODE((root / ".workspace" / "console-settings.json").stat().st_mode), 0o600)

                (root / "toggle-done").touch()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                expect(main_console.get_by_label("stdout")).to_contain_text("Main after toggle")
                expect(main_console.get_by_label("stderr")).to_contain_text("Main stderr after toggle")
                first_entries = console_entries(root / ".workspace" / "logs" / first_run_id)
                self.assertIn("Main before toggle", combined_stream(first_entries, "stdout"))
                self.assertNotIn("Main after toggle", combined_stream(first_entries, "stdout"))
                self.assertNotIn("Main stderr after toggle", combined_stream(first_entries, "stderr"))

                page.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                expect(main_console.get_by_label("stdout")).to_contain_text("Main after toggle")
                expect(main_console.get_by_label("stderr")).to_contain_text("Main stderr after toggle")
                second_run_id = page.request.get(workspace_url + "workspace").json()["flows"][0]["run_id"]
                self.assertNotEqual(second_run_id, first_run_id)
                self.assertFalse((root / ".workspace" / "logs" / second_run_id).exists())

                view = page.locator('iframe[title="Main flow"]').content_frame.locator("html").evaluate(
                    "document => document.baseURI"
                ).rstrip("/")
                for bad_payload in (
                    {},
                    {"save_logs": "false"},
                    {"retention_days": True},
                    {"retention_days": 0},
                    {"retention_days": 3651},
                    {"unknown": 1},
                ):
                    self.assertEqual(
                        page.request.post(
                            f"{view}/console-logs",
                            data=bad_payload,
                            headers={"Origin": url},
                        ).status,
                        400,
                    )

                page.get_by_role("tab", name="Secondary").click()
                secondary = page.frame_locator('iframe[title="Secondary flow"]')
                expect(secondary.get_by_label("Save console logs")).to_be_checked()
                expect(secondary.get_by_label("Retention (days)")).to_have_value("7")
                page.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("tab", name="Secondary")).to_contain_text("Succeeded")
                sec_console = secondary.get_by_role("region", name="Run console")
                expect(sec_console.get_by_label("stdout")).to_contain_text("Secondary stdout")
                expect(sec_console.get_by_label("stderr")).to_contain_text("Secondary stderr")
                expect(sec_console.get_by_label("stdout")).not_to_contain_text("Main before toggle")
                sec_run_id = page.request.get(workspace_url + "workspace").json()["flows"][1]["run_id"]
                sec_entries = console_entries(secondary_root / ".workspace" / "logs" / sec_run_id)
                self.assertEqual(combined_stream(sec_entries, "stdout"), "Secondary stdout\n")
                self.assertEqual(combined_stream(sec_entries, "stderr"), "Secondary stderr\n")

                page.reload()
                expect(main_save).not_to_be_checked()
                expect(main_retention).to_have_value("7")

    def test_log_io_failure_does_not_fail_the_flow_run(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            root = parent / "ConsoleError"
            state = parent / "state"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                (root / "nodes/start/entrypoint.py").write_text(
                    'def start():\n    print("Live despite disk failure")\n    return "ok"\n',
                    encoding="utf-8",
                )
                (root / ".workspace").mkdir(mode=0o700, exist_ok=True)
                (root / ".workspace" / "logs").write_text("not a directory", encoding="utf-8")
                workspace.click()
                flow = page.frame_locator('iframe[title="Main flow"]')
                page.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                expect(flow.get_by_role("region", name="Run console").get_by_label("stdout")).to_contain_text(
                    "Live despite disk failure"
                )
                expect(flow.locator("#result pre")).to_have_text('"ok"')
                expect(flow.locator("#result")).to_contain_text("Console logs could not be saved.")

    def test_retention_rotates_and_expires_logs_across_date_boundary_during_continuous_run(self):
        with TemporaryDirectory() as temporary, browser_page() as page:
            parent = Path(temporary).resolve()
            root = parent / "ConsoleRetention"
            state = parent / "state"
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                (root / ".workspace").mkdir(mode=0o700, exist_ok=True)
                (root / ".workspace" / "log-date").write_text("2026-11-18\n", encoding="utf-8")
                (root / "nodes/start/entrypoint.py").write_text(
                    r'''from pathlib import Path
import sys
from time import monotonic, sleep

def start(mode):
    if mode == "once":
        print("Prior run day 1", flush=True)
        return "once ok"
    print("Continuous day 1 stdout", flush=True)
    print("Continuous day 1 stderr", file=sys.stderr, flush=True)
    day2 = False
    day3 = False
    deadline = monotonic() + 25
    while not Path("finish-continuous").exists() and monotonic() < deadline:
        if not day2 and Path("emit-day-2").exists():
            day2 = True
            print("Continuous day 2 stdout", flush=True)
            print("Continuous day 2 stderr", file=sys.stderr, flush=True)
            Path("day-2-done").touch()
        if not day3 and Path("emit-day-3").exists():
            day3 = True
            print("Continuous day 3 stdout", flush=True)
            Path("day-3-done").touch()
        sleep(.02)
    return "continuous ok"
''',
                    encoding="utf-8",
                )
                (root / "flow.py").write_text(
                    "from workspace_sdk import Flow, Input, Output, Step\n"
                    "from nodes.start.entrypoint import start\n"
                    'flow = Flow(("mode",), (Step("Start", start, {"mode": Input("mode")}),), Output("Start"))\n',
                    encoding="utf-8",
                )
                (root / "sample.json").write_text('{"input": {"mode": "once"}}', encoding="utf-8")

                secondary_root = create_sheet(root, "Secondary").parent
                (secondary_root / ".workspace").mkdir(mode=0o700, exist_ok=True)
                (secondary_root / ".workspace" / "log-date").write_text("2026-11-18\n", encoding="utf-8")
                (secondary_root / "nodes/start/entrypoint.py").write_text(
                    'def start():\n    print("Secondary preserved")\n    return "ok"\n',
                    encoding="utf-8",
                )

                workspace.click()
                workspace_url = page.url
                main = page.frame_locator('iframe[title="Main flow"]')
                page.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")
                prior_run_id = page.request.get(workspace_url + "workspace").json()["flows"][0]["run_id"]
                prior_segment = root / ".workspace" / "logs" / prior_run_id / "2026-11-18.ndjson"
                wait_for(lambda: prior_segment.is_file())

                page.get_by_role("tab", name="Secondary").click()
                secondary = page.frame_locator('iframe[title="Secondary flow"]')
                page.get_by_role("button", name="Run flow").click()
                expect(page.get_by_role("tab", name="Secondary")).to_contain_text("Succeeded")
                sec_run_id = page.request.get(workspace_url + "workspace").json()["flows"][1]["run_id"]
                sec_segment = secondary_root / ".workspace" / "logs" / sec_run_id / "2026-11-18.ndjson"
                wait_for(lambda: sec_segment.is_file())
                (secondary_root / ".workspace" / "log-date").write_text("2026-11-20\n", encoding="utf-8")

                page.get_by_role("tab", name="Main").click()
                retention = main.get_by_label("Retention (days)")
                retention.fill("0")
                expect(main.locator("#console-settings-message")).to_contain_text("1 to 3650")
                retention.fill("2")
                expect(main.locator("#console-settings-message")).to_contain_text("Saved")
                self.assertEqual(
                    json.loads((root / ".workspace" / "console-settings.json").read_text(encoding="utf-8")),
                    {"save_logs": True, "retention_days": 2},
                )

                main.locator("#input").fill('{"mode": "continuous"}')
                main.get_by_role("button", name="Run with this input").click()
                main_console = main.get_by_role("region", name="Run console")
                expect(main_console.get_by_label("stdout")).to_contain_text("Continuous day 1 stdout")
                expect(main_console.get_by_label("stderr")).to_contain_text("Continuous day 1 stderr")
                cont_run_id = page.request.get(workspace_url + "workspace").json()["flows"][0]["run_id"]
                cont_dir = root / ".workspace" / "logs" / cont_run_id
                wait_for(lambda: (cont_dir / "2026-11-18.ndjson").is_file())

                (root / ".workspace" / "log-date").write_text("2026-11-19\n", encoding="utf-8")
                (root / "emit-day-2").touch()
                wait_for(lambda: (root / "day-2-done").is_file() and (cont_dir / "2026-11-19.ndjson").is_file())
                expect(main_console.get_by_label("stdout")).to_contain_text("Continuous day 2 stdout")
                expect(main_console.get_by_label("stderr")).to_contain_text("Continuous day 2 stderr")
                self.assertTrue((cont_dir / "2026-11-18.ndjson").is_file())
                self.assertTrue(prior_segment.is_file())

                (root / ".workspace" / "log-date").write_text("2026-11-20\n", encoding="utf-8")
                wait_for(lambda: not (cont_dir / "2026-11-18.ndjson").exists())
                wait_for(lambda: not prior_segment.parent.exists())
                self.assertTrue((cont_dir / "2026-11-19.ndjson").is_file())
                self.assertTrue(sec_segment.is_file())

                (root / "emit-day-3").touch()
                wait_for(lambda: (root / "day-3-done").is_file() and (cont_dir / "2026-11-20.ndjson").is_file())
                (root / "finish-continuous").touch()
                expect(page.get_by_role("tab", name="Main")).to_contain_text("Succeeded")

                page.get_by_role("button", name="Reload flow").click()
                expect(main.get_by_role("button", name="Inspect Start")).to_be_visible()
                expect(main_console.get_by_label("stdout")).to_contain_text("Continuous day 2 stdout")
                expect(main_console.get_by_label("stdout")).to_contain_text("Continuous day 3 stdout")
                expect(main_console.get_by_label("stderr")).to_contain_text("Continuous day 2 stderr")
                expect(main_console.get_by_label("stdout")).not_to_contain_text("Continuous day 1 stdout")
                expect(main_console.get_by_label("stderr")).not_to_contain_text("Continuous day 1 stderr")

    def test_clock_sources_validation_and_corrupted_line_tolerance(self):
        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "StoreUnit"
            root.mkdir()
            previous = os.environ.get("PHYZFLOW_LOG_DATE")
            try:
                os.environ["PHYZFLOW_LOG_DATE"] = "2026-11-21"
                store = ConsoleLogStore(root)
                self.assertEqual(store.current_date(), date(2026, 11, 21))
                (root / ".workspace").mkdir(mode=0o700)
                (root / ".workspace" / "log-date").write_text("2026-11-22\n", encoding="utf-8")
                self.assertEqual(store.current_date(), date(2026, 11, 22))
                (root / ".workspace" / "log-date").unlink()
                del os.environ["PHYZFLOW_LOG_DATE"]
                self.assertEqual(store.current_date(), datetime.now(timezone.utc).date())
            finally:
                if previous is None:
                    os.environ.pop("PHYZFLOW_LOG_DATE", None)
                else:
                    os.environ["PHYZFLOW_LOG_DATE"] = previous

            injected = ConsoleLogStore(root, clock=lambda: date(2026, 11, 25))
            run_id = "a" * 32
            injected.append(run_id, "stdout", "clean line\n")
            segment = root / ".workspace" / "logs" / run_id / "2026-11-25.ndjson"
            with segment.open("a", encoding="utf-8") as handle:
                handle.write('{"run_id": "' + run_id + '", "stream": "stdout", "text": "truncated\n')
            self.assertEqual(
                injected.read_run(run_id),
                [{"kind": "console", "run_id": run_id, "stream": "stdout", "text": "clean line\n"}],
            )
            with self.assertRaises(ValueError):
                injected.append("../bad", "stdout", "x")
            with self.assertRaises(ValueError):
                injected.append(run_id, "other", "x")
            with self.assertRaises(ValueError):
                injected.read_run("not-a-run-id")
            outside = parent / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            (root / ".workspace" / "console-settings.json").symlink_to(outside)
            with self.assertRaises(ValueError):
                injected.settings()
