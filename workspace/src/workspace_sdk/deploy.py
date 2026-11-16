"""Deploy a request/response project to a local Linux ARM64 Docker engine."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.request import ProxyHandler, build_opener


class DeploymentError(RuntimeError):
    pass


def wait_for_http(url: str) -> None:
    deadline = monotonic() + 15
    client = build_opener(ProxyHandler({}))
    while True:
        try:
            with client.open(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if monotonic() >= deadline:
            raise DeploymentError(f"The server did not become ready at {url}/health.")
        sleep(0.2)


def deploy(project: Path, sdk: Path, context: str | None, port: int) -> int:
    docker = ["docker", "--context", context] if context else ["docker"]

    def execute(*args: str, capture: bool = False) -> str:
        result = subprocess.run(
            [*docker, *args], check=True, text=True,
            stdout=subprocess.PIPE if capture else sys.stderr,
        )
        return result.stdout.strip() if capture else ""

    if not 0 <= port <= 65535:
        print("Deployment: port must be between 0 and 65535.", file=sys.stderr)
        return 1

    require_smoke = (project / "smoke_test.py").is_file()
    phase = "Build"
    candidate = ""
    live = ""
    try:
        with TemporaryDirectory() as temporary:
            iidfile = Path(temporary) / "image-id"
            execute(
                "build", "--platform", "linux/arm64", "--load",
                "--build-context", f"sdk={sdk}", "--iidfile", str(iidfile), str(project),
            )
            build = execute(
                "image", "inspect", "--format", "{{.Id}}", iidfile.read_text().strip(), capture=True,
            )

        phase = "Validation"
        print(f"Validating {build} in the target environment.", file=sys.stderr)
        smoke_available = execute(
            "run", "--rm", "--network", "none", "--pull", "never",
            "--entrypoint", "sh", build, "-ec",
            'workspace check >&2; if test -f smoke_test.py; then echo smoke; fi',
            capture=True,
        ) == "smoke"
        if require_smoke and not smoke_available:
            raise DeploymentError("Copy the project's smoke_test.py into the image working directory.")

        environment = f"WORKSPACE_BUILD_ID={build}"
        if smoke_available:
            phase = "Smoke test"
            print("Running the smoke test without publishing traffic.", file=sys.stderr)
            candidate = execute(
                "create", "--network", "none", "--pull", "never", "--env", environment,
                build, capture=True,
            )
            execute("start", candidate, capture=True)
            execute(
                "exec", candidate, "python", "-c",
                "from workspace_sdk.deploy import wait_for_http; wait_for_http('http://127.0.0.1:8000')",
            )
            execute("exec", candidate, "python", "smoke_test.py", "http://127.0.0.1:8000")
            execute("rm", "-f", candidate, capture=True)
            candidate = ""

        phase = "Publication"
        binding = f"127.0.0.1:{port or ''}:8000"
        live = execute(
            "create", "--rm", "--pull", "never", "--env", environment,
            "--publish", binding, build, capture=True,
        )
        execute("start", live, capture=True)
        address = execute("port", live, "8000/tcp", capture=True)
        url = f"http://{address}"
        wait_for_http(url)
        print(json.dumps({"build_id": build, "container_id": live, "url": url}))
        live = ""
        return 0
    except (DeploymentError, OSError, subprocess.CalledProcessError) as error:
        print(f"{phase} failed: {error}", file=sys.stderr)
        if candidate:
            subprocess.run([*docker, "logs", "--tail", "20", candidate], stdout=sys.stderr, check=False)
        return 1
    except KeyboardInterrupt:
        print(f"{phase} interrupted.", file=sys.stderr)
        return 130
    finally:
        for container in (candidate, live):
            if container:
                subprocess.run(
                    [*docker, "rm", "-f", container], check=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
