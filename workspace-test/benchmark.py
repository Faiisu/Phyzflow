import argparse
import json
import math
import platform
from pathlib import Path
import resource
import signal
from statistics import median
import sys
from time import perf_counter_ns

from workspace_sdk import run


DEMO_PROJECT = Path(__file__).resolve().parent
TRAINED_PROJECT = DEMO_PROJECT / "deployment-trial"
UI_RUNS = """
async ({warmup, runs, output}) => {
  const button = document.querySelector("#run-flow");
  const result = document.querySelector("#result");
  const durations = [];

  for (let index = 0; index < warmup + runs; index++) {
    const started = performance.now();
    const duration = await new Promise((resolve, reject) => {
      const observer = new MutationObserver(() => {
        if (button.disabled) return;
        const elapsed = performance.now() - started;
        observer.disconnect();
        clearTimeout(timer);
        const actual = result.querySelector("pre");
        if (actual && JSON.stringify(JSON.parse(actual.textContent)) === JSON.stringify(output)) {
          resolve(elapsed);
        } else {
          reject(new Error("The live UI returned an unexpected result."));
        }
      });
      const timer = setTimeout(() => {
        observer.disconnect();
        reject(new Error("Timed out waiting for the live UI."));
      }, 5000);
      observer.observe(button, {attributes: true, attributeFilter: ["disabled"]});
      button.click();
    });
    if (index >= warmup) durations.push(duration);
  }

  return durations;
}
"""


def python_runs(project, sample, mode, warmup, runs):
    sys.path.insert(0, str(project))
    from flow import decide, flow, predict, prepare

    inputs = sample["input"]
    durations = []
    for index in range(warmup + runs):
        started = perf_counter_ns()
        if mode == "direct":
            prepared = prepare(**inputs)
            result = decide(prepared, predict(prepared))
        else:
            outcome = run(flow, inputs)
            if outcome.status != "succeeded":
                raise RuntimeError("The Runner failed during the benchmark.")
            result = outcome.output
        elapsed = perf_counter_ns() - started
        if result != sample["output"]:
            raise RuntimeError(f"Unexpected sample output: {result!r}.")
        if index >= warmup:
            durations.append(elapsed / 1_000_000)
    return durations, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def live_ui_runs(project, sample, warmup, runs):
    from playwright.sync_api import expect
    from browser_support import browser_page, local_ui

    # The benchmark wrapper measures the CLI process without adding a profiling route.
    measure_cli = """
import resource
from workspace_sdk.cli import main
status = main()
print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, flush=True)
raise SystemExit(status)
"""
    with local_ui(project, [sys.executable, "-c", measure_cli]) as (url, process), browser_page() as page:
        page.goto(url)
        expect(page.get_by_role("button", name="Run flow")).to_be_enabled()
        page.get_by_role("tab", name="Run").click()
        page.locator("#input").fill(json.dumps(sample["input"]))
        durations = page.evaluate(UI_RUNS, {
            "warmup": warmup, "runs": runs, "output": sample["output"],
        })
        browser = page.context.browser
        assert browser is not None
        version = browser.version
        process.send_signal(signal.SIGINT)
        peak, _ = process.communicate(timeout=10)
        if process.returncode != 0:
            raise RuntimeError(f"Benchmark UI exited with status {process.returncode}.")
    return durations, int(peak.strip()), version


def main():
    parser = argparse.ArgumentParser(description="Measure Workspace overhead for support-ticket classification.")
    parser.add_argument("mode", choices=("direct", "runner", "ui"))
    parser.add_argument("--workload", choices=("demo", "trained"), default="demo")
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--runs", type=int, default=100)
    args = parser.parse_args()
    if args.warmup < 0 or args.runs < 1:
        parser.error("Warm-up must be nonnegative and runs must be positive.")
    if sys.platform not in ("darwin", "linux"):
        parser.error("Peak RSS is supported by this procedure on macOS and Linux.")

    project = TRAINED_PROJECT if args.workload == "trained" else DEMO_PROJECT
    if args.workload == "trained" and not (project / "model.pkl").is_file():
        parser.error("Train the model with deployment-trial/train_model.py before benchmarking.")
    sample = json.loads((project / "sample.json").read_text())

    browser_version = None
    if args.mode == "ui":
        durations, peak, browser_version = live_ui_runs(project, sample, args.warmup, args.runs)
    else:
        durations, peak = python_runs(project, sample, args.mode, args.warmup, args.runs)
    p95 = sorted(durations)[math.ceil(len(durations) * 0.95) - 1]
    bytes_per_unit = 1 if sys.platform == "darwin" else 1024
    report = {
        "mode": args.mode,
        "workload": args.workload,
        "python": platform.python_version(),
        "machine": platform.platform(),
        "warmup": args.warmup,
        "runs": args.runs,
        "p50_ms": round(median(durations), 6),
        "p95_ms": round(p95, 6),
        "peak_rss_mib": round(peak * bytes_per_unit / (1024 ** 2), 3),
        "memory_scope": "Python execution process only",
    }
    if browser_version:
        report["browser"] = f"Chrome {browser_version}"
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
