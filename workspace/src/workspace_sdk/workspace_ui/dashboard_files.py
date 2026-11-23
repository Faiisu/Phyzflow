from http import HTTPStatus
import json
from urllib.parse import parse_qs

from ..application_mcp.file_paths import checked_path, open_file, relative_path, require_file
from ..dashboard import dashboard_assets


MAX_DASHBOARD_FILE_BYTES = 16 * 1024 * 1024


def send_dashboard_file(request, root, sheet, panel_id, query, protected):
    try:
        args = parse_qs(query, keep_blank_values=True, errors="strict", max_num_fields=1)
        value, = args.get("path", ())
        manifest = dashboard_assets(sheet, None, panel_id, manifest_only=True).get("bridge.json")
        directory = json.loads(manifest or "{}").get("file_root")
        if not isinstance(directory, str):
            raise FileNotFoundError
        relative = relative_path(directory) / relative_path(value)
        if any(part.startswith(".") for part in relative.parts):
            raise FileNotFoundError
        if relative.suffix.lower() not in (".html", ".json", ".wav"):
            raise FileNotFoundError
        target, mode, info = checked_path(root, relative, protected)
        info = require_file(mode, info)
        if info.st_size > MAX_DASHBOARD_FILE_BYTES:
            request.send_json({"error": "The dashboard file exceeds 16 MiB."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        with open_file(target, info) as stream:
            content = stream.read(MAX_DASHBOARD_FILE_BYTES + 1)
        if len(content) != info.st_size:
            raise FileNotFoundError
    except (AttributeError, OSError, ValueError):
        request.send_json({"error": "Dashboard file unavailable."}, HTTPStatus.NOT_FOUND)
    else:
        request.send_bytes(content, "application/octet-stream")
