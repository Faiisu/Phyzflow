import errno
from http import HTTPStatus
from os.path import samestat
from pathlib import Path
from shutil import disk_usage
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs

from ..application_mcp.file_paths import STAGING_PREFIX, atomic_move
from ..application_mcp.upload_paths import file_folder, file_name, folder_entries
from ..web import UIRequestHandler


def upload_file(request: UIRequestHandler, folder: Path, name: str, publish) -> None:
    lengths = request.headers.get_all("Content-Length", [])
    if (request.headers.get_content_type() != "application/octet-stream" or request.headers.get("Transfer-Encoding")
            or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal()):
        raise ValueError("Send a file with a valid content length.")
    remaining = int(lengths[0])
    if remaining > disk_usage(folder).free:
        raise OSError(errno.ENOSPC, "Not enough space to save this file.")
    target = folder / name
    if target.exists() or target.is_symlink():
        raise FileExistsError

    folder_info = folder.stat()
    timeout = request.connection.gettimeout()
    stage = None
    try:
        request.connection.settimeout(30)
        with NamedTemporaryFile(dir=folder, prefix=STAGING_PREFIX, delete=False) as stream:
            stage = Path(stream.name)
            while remaining:
                chunk = request.rfile.read1(min(remaining, 1024 * 1024))
                if not chunk:
                    raise ValueError("The upload ended before all bytes were received.")
                stream.write(chunk)
                remaining -= len(chunk)
        publish(stage, target, folder_info)
    finally:
        request.connection.settimeout(timeout)
        if stage is not None and folder.exists() and samestat(folder_info, folder.stat()):
            stage.unlink(missing_ok=True)


def send_workspace_files(request: UIRequestHandler, root: Path, route: str, query: str, lock, is_closing) -> None:
    def available() -> None:
        if is_closing():
            raise FileNotFoundError

    try:
        args = parse_qs(query, keep_blank_values=True, errors="strict", max_num_fields=2)
        path, = args.get("path", ["."])
        if route == "/files/folders":
            if request.headers.get_content_type() != "application/json":
                raise ValueError("Send a JSON object with the folder name.")
            name = file_name(request.read_json_dict(4096).get("name"))
        elif route == "/files/upload":
            name, = args.get("name", [""])
            name = file_name(name)
        with lock:
            available()
            folder = file_folder(root, path, create=request.command == "POST")
            if route == "/files/folders":
                (folder / name).mkdir(mode=0o700)
            elif route == "/files":
                entries = folder_entries(folder)

        if request.command == "GET":
            request.send_json({"entries": entries})
            return
        if route == "/files/upload":
            def publish(stage, target, folder_info):
                with lock:
                    available()
                    current = file_folder(root, path)
                    if current is None or not samestat(folder_info, current.stat()):
                        raise FileNotFoundError
                    atomic_move(stage, target)

            upload_file(request, folder, name, publish)
    except FileExistsError:
        request.send_json({"error": "A file or folder with this name already exists."}, HTTPStatus.CONFLICT)
    except FileNotFoundError:
        request.send_json({"error": "This folder is unavailable."}, HTTPStatus.NOT_FOUND)
    except TimeoutError:
        request.send_json({"error": "The upload timed out."}, HTTPStatus.REQUEST_TIMEOUT)
    except (ValueError, RecursionError) as error:
        message = str(error) if type(error) is ValueError else ""
        request.send_json({"error": message or "The file request is invalid."}, HTTPStatus.BAD_REQUEST)
    except OSError as error:
        if error.errno == errno.ENOSPC:
            request.send_json({"error": "Not enough space to save this file."}, HTTPStatus.INSUFFICIENT_STORAGE)
        else:
            request.send_json({"error": "Could not access Workspace files."}, HTTPStatus.INTERNAL_SERVER_ERROR)
    else:
        request.send_json({"name": name}, HTTPStatus.CREATED)
