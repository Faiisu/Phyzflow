from contextlib import contextmanager
from pathlib import Path
import select
import signal
import subprocess
import sys

from playwright.sync_api import sync_playwright


@contextmanager
def browser_page(*, show_scrollbars=False):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome", ignore_default_args=["--hide-scrollbars"] if show_scrollbars else None,
        )
        try:
            yield browser.new_page(viewport={"width": 1440, "height": 900})
        finally:
            browser.close()


def show_panel(page, name):
    switcher = page.locator("#panel-switcher")
    switch = switcher.locator("summary")
    if switcher.get_attribute("open") is None:
        switch.click()
    toggle = switcher.get_by_role("button", name=name, exact=True)
    if toggle.get_attribute("aria-pressed") == "false":
        toggle.click()
    switch.click()


@contextmanager
def local_ui(project: Path, command=None, *, port: int | None = 0, single_flow: bool = False):
    executable = Path(sys.executable).with_name("workspace")
    command = [str(executable)] if command is None else command
    options = ["--port", str(port)] if port is not None else []
    if single_flow:
        options.append("--single-flow")
    process = subprocess.Popen(
        [*command, "ui", *options],
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
