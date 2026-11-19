"""Deploy a request/response project to a local Linux ARM64 Docker engine."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.request import ProxyHandler, build_opener

from .ui_settings import load_deploy_settings


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


def deploy(
    project: Path, sdk: Path, context: str | None = None, port: int | None = None,
    *, ipv4: str | None = None, container_port: int | None = None,
) -> int:
    try:
        settings = load_deploy_settings(project, ipv4=ipv4, port=port, container_port=container_port, context=context)
    except (OSError, ValueError) as error:
        print(f"Deployment settings: {error}", file=sys.stderr)
        return 1
    docker = ["docker", "--context", settings.context] if settings.context else ["docker"]

    def execute(*args: str, capture: bool = False) -> str:
        result = subprocess.run(
            [*docker, *args], check=True, text=True,
            stdout=subprocess.PIPE if capture else sys.stderr,
        )
        return result.stdout.strip() if capture else ""

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
            candidate_url = f"http://127.0.0.1:{settings.container_port}"
            execute(
                "exec", candidate, "python", "-c",
                f"from workspace_sdk.deploy import wait_for_http; wait_for_http({candidate_url!r})",
            )
            execute("exec", candidate, "python", "smoke_test.py", candidate_url)
            execute("rm", "-f", candidate, capture=True)
            candidate = ""

        phase = "Publication"
        binding = f"{settings.ipv4}:{settings.port or ''}:{settings.container_port}"
        live = execute(
            "create", "--rm", "--pull", "never", "--env", environment,
            "--publish", binding, build, capture=True,
        )
        execute("start", live, capture=True)
        addresses = execute("port", live, f"{settings.container_port}/tcp", capture=True)
        prefix = f"{settings.ipv4}:"
        published_port = next((line.removeprefix(prefix) for line in addresses.splitlines() if line.startswith(prefix)), "")
        if not published_port.isdecimal():
            raise DeploymentError(f"Docker did not report a published port on {settings.ipv4}.")
        host = "127.0.0.1" if settings.ipv4 == "0.0.0.0" else settings.ipv4
        url = f"http://{host}:{published_port}"
        wait_for_http(url)
        print(json.dumps({
            "build_id": build, "container_id": live, "url": url,
            "ipv4": settings.ipv4, "port": int(published_port), "container_port": settings.container_port,
        }))
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
