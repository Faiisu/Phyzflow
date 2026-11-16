from contextlib import contextmanager
from pathlib import Path
import select
import signal
import subprocess
import sys

from playwright.sync_api import sync_playwright


@contextmanager
def browser_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome")
        try:
            yield browser.new_page(viewport={"width": 1440, "height": 900})
        finally:
            browser.close()


@contextmanager
def local_ui(project: Path, command=None):
    executable = Path(sys.executable).with_name("workspace")
    command = [str(executable)] if command is None else command
    process = subprocess.Popen(
        [*command, "ui", "--port", "0"],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        if not select.select([process.stdout], [], [], 10)[0]:
            raise RuntimeError("The Workspace UI did not start within ten seconds.")
        prefix = "Workspace UI: "
        line = process.stdout.readline().strip()
        if not line.startswith(prefix):
            raise RuntimeError(f"The Workspace UI did not publish its address: {line!r}.")
        yield line.removeprefix(prefix), process
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()
