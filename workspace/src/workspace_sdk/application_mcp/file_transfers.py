from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from shutil import disk_usage
from stat import S_IMODE
from threading import Event, RLock, Thread
from time import monotonic
from typing import Any, BinaryIO
from uuid import uuid4

from ..workspace_environment import is_protected_path
from .access import ensure_not_protected
from .file_paths import (
    STAGING_PREFIX, atomic_move, checked_path, file_metadata, mutate_files, open_file,
    path_info, relative_path, require_file, revision, verify_revision,
)
from .upload_paths import UPLOAD_DIRECTORY, file_folder, file_name

CHUNK_BYTES = 512 * 1024
IDLE_SECONDS = 15 * 60
DISK_RESERVE = 256 * 1024 * 1024
MAX_TRANSFERS = 32
MAX_RECEIPTS = 128


@dataclass
class Transfer:
    workspace_id: str
    principal: str
    root: Path
    relative: Path
    stream: BinaryIO
    expected: str
    size: int
    digest: Any = field(default_factory=sha256)
    offset: int = 0
    last_offset: int = -1
    last_chunk: bytes = b""
    deadline: float = field(default_factory=lambda: monotonic() + IDLE_SECONDS)
    stage: Path | None = None
    device: int | None = None
    expected_sha256: str | None = None
    stage_revision: str | None = None
    upload_area: bool = False


def publish_file(stage: Path, target: Path, mode: str, info: os.stat_result | None) -> None:
    if mode == "missing":
        atomic_move(stage, target)
    else:
        if info is not None and os.name != "nt":
            stage.chmod(S_IMODE(info.st_mode))
        stage.replace(target)


class FileTransfers:
    def __init__(self, access, directory: Path):
        self.access = access
        self.directory = directory
        self.lock = RLock()
        self.transfers: dict[str, Transfer] = {}
        self.receipts: dict[str, tuple[str, str, float, dict]] = {}
        self.stopping = Event()
        self.worker = Thread(target=self.sweep, daemon=True)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.recover()
        self.worker.start()

    def staging_directory_exists(self, root: Path, parent: Path, upload_area: bool) -> bool:
        if upload_area:
            path = parent.relative_to(root / UPLOAD_DIRECTORY).as_posix()
            try:
                return file_folder(root, path) is not None and not is_protected_path(parent, self.access.protected_folders)
            except FileNotFoundError:
                return False
        relative = relative_path(parent.relative_to(root).as_posix(), directory=True)
        return checked_path(root, relative, self.access.protected_folders)[1] == "directory"

    def clean_stage(self, identity: str, root: Path, stage: Path, upload_area: bool) -> None:
        if stage.name != STAGING_PREFIX + identity or not stage.is_relative_to(root):
            raise ValueError("Invalid upload staging path.")
        if self.staging_directory_exists(root, stage.parent, upload_area):
            stage.unlink(missing_ok=True)
        (self.directory / identity).unlink(missing_ok=True)

    def recover(self) -> None:
        for record in self.directory.iterdir():
            if not re.fullmatch(r"[0-9a-f]{32}", record.name) or not record.is_file():
                continue
            with suppress(OSError, ValueError, KeyError, TypeError):
                data = json.loads(record.read_text(encoding="utf-8"))
                root = self.access.registry.get(data["workspace_id"])
                if root is None:
                    record.unlink()
                else:
                    self.clean_stage(record.name, root, root / data["staging_path"], data.get("upload_area") is True)

    def sweep(self) -> None:
        while not self.stopping.wait(30):
            with self.lock:
                self.expire()

    def expire(self) -> None:
        now = monotonic()
        for identity, transfer in list(self.transfers.items()):
            allowed = self.access.tokens.workspace_ids(transfer.principal)
            if transfer.deadline <= now or not allowed or transfer.workspace_id not in allowed:
                self.discard(identity)
        for identity, (_, _, deadline, _) in list(self.receipts.items()):
            if deadline <= now:
                self.receipts.pop(identity)

    def discard(self, identity: str) -> None:
        transfer = self.transfers.pop(identity)
        with suppress(OSError):
            transfer.stream.close()
        if transfer.stage is not None:
            with suppress(OSError, ValueError):
                self.clean_stage(identity, transfer.root, transfer.stage, transfer.upload_area)

    def cancel_workspace(self, workspace_id: str) -> None:
        with self.lock:
            for identity, transfer in list(self.transfers.items()):
                if transfer.workspace_id == workspace_id:
                    self.discard(identity)

    def close(self) -> None:
        self.stopping.set()
        self.worker.join()
        with self.lock:
            for identity in list(self.transfers):
                self.discard(identity)

    def remaining_space(self, parent: Path, device: int, pending: int, identity: str) -> None:
        reserved = sum(
            transfer.size - transfer.offset for key, transfer in self.transfers.items()
            if key != identity and transfer.device == device
        )
        if pending and pending + reserved + DISK_RESERVE > disk_usage(parent).free:
            raise ValueError("There is insufficient free disk space for this upload.")

    def transfer_target(self, root: Path, relative: Path, upload_area: bool, *, create: bool = False):
        if not upload_area:
            return checked_path(root, relative, self.access.protected_folders)
        name = file_name(relative.name)
        ensure_not_protected(root / UPLOAD_DIRECTORY / relative, self.access.protected_folders)
        folder = file_folder(root, relative.parent.as_posix(), create=create)
        if folder is None:
            raise FileNotFoundError("This folder is unavailable.")
        target = folder / name
        if path_info(target)[0] != "missing":
            raise ValueError("A file or folder with this name already exists.")
        return target, "missing", None

    def begin(self, workspace_id, path, direction, expected_revision, size, checksum, *, upload_area=False):
        with self.access.file_gate, self.lock:
            root = self.access.workspace(workspace_id)
            self.expire()
            principal = self.access.principal()
            uploads = direction == "upload"
            if len(self.transfers) >= MAX_TRANSFERS or any(
                item.principal == principal and (item.stage is not None) == uploads
                for item in self.transfers.values()
            ):
                raise ValueError("The active transfer limit was reached. Finish or cancel a transfer first.")
            relative = relative_path(path)
            target, mode, info = self.transfer_target(root, relative, upload_area, create=True)
            identity = uuid4().hex
            if uploads:
                if (
                    size is None or size < 0 or expected_revision is None or checksum is None
                    or not re.fullmatch(r"[a-f0-9]{64}", checksum)
                ):
                    raise ValueError("An upload requires its size, SHA-256, and expected revision.")
                verify_revision(mode, info, expected_revision)
                parent = next(parent for parent in target.parents if parent.is_dir())
                device = parent.stat().st_dev
                self.remaining_space(parent, device, size, identity)
                stage = parent / (STAGING_PREFIX + identity)
                record = self.directory / identity
                try:
                    with record.open("x", encoding="utf-8") as journal:
                        json.dump({
                            "workspace_id": workspace_id, "staging_path": stage.relative_to(root).as_posix(),
                            "upload_area": upload_area,
                        }, journal)
                        journal.flush()
                        os.fsync(journal.fileno())
                    stream = os.fdopen(os.open(stage, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600), "wb", buffering=0)
                except OSError:
                    record.unlink(missing_ok=True)
                    raise
                transfer = Transfer(
                    workspace_id, principal, root, relative, stream, expected_revision, size,
                    stage=stage, device=device, expected_sha256=checksum,
                    stage_revision=revision(os.fstat(stream.fileno())),
                    upload_area=upload_area,
                )
            else:
                if size is not None or checksum is not None:
                    raise ValueError("Size and SHA-256 are supplied only for uploads.")
                info = require_file(mode, info)
                if expected_revision is not None:
                    verify_revision(mode, info, expected_revision)
                transfer = Transfer(workspace_id, principal, root, relative, open_file(target, info), revision(info), info.st_size)
            self.transfers[identity] = transfer
            return {
                "transfer_id": identity, "chunk_bytes": CHUNK_BYTES, "next_offset": 0, "size": transfer.size,
                "revision": transfer.expected,
            }

    def get(self, identity: str) -> Transfer:
        transfer = self.transfers.get(identity)
        if transfer is None or transfer.principal != self.access.principal():
            raise ValueError("This file transfer is unavailable.")
        if transfer.deadline <= monotonic():
            self.discard(identity)
            raise ValueError("This file transfer has expired.")
        if self.access.workspace(transfer.workspace_id) != transfer.root:
            self.discard(identity)
            raise ValueError("The Workspace folder changed.")
        transfer.deadline = monotonic() + IDLE_SECONDS
        return transfer

    def check_download(self, identity: str, transfer: Transfer) -> None:
        try:
            _, mode, info = checked_path(transfer.root, transfer.relative, self.access.protected_folders)
            verify_revision(mode, info, transfer.expected)
            verify_revision("file", os.fstat(transfer.stream.fileno()), transfer.expected)
        except (OSError, ValueError):
            self.discard(identity)
            raise ValueError("The file changed during download. Start a new download.") from None

    def read(self, identity: str, offset: int, count: int) -> dict:
        with self.lock:
            item = self.get(identity)
            if item.stage is not None:
                raise ValueError("This file transfer is an upload.")
            if not 1 <= count <= CHUNK_BYTES or offset < 0:
                raise ValueError("Invalid chunk offset or byte count.")
            self.check_download(identity, item)
            if offset == item.last_offset:
                data = item.last_chunk
                if len(data) > count:
                    raise ValueError("A retry must allow the original chunk size.")
            elif offset == item.offset and offset < item.size:
                data = item.stream.read(min(count, item.size - offset))
                self.check_download(identity, item)
                if not data:
                    self.discard(identity)
                    raise ValueError("The file changed during download.")
                item.digest.update(data)
                item.offset += len(data)
                item.last_offset = offset
                item.last_chunk = data
            elif offset == item.size == item.offset:
                data = b""
            else:
                raise ValueError(f"Expected byte offset {item.offset}.")
            return {"data": b64encode(data).decode("ascii"), "next_offset": offset + len(data), "eof": offset + len(data) == item.size}

    def write(self, identity: str, offset: int, encoded: str) -> dict:
        if len(encoded) > 4 * ((CHUNK_BYTES + 2) // 3):
            raise ValueError("The Base64 chunk exceeds the transfer limit.")
        try:
            data = b64decode(encoded, validate=True)
        except Base64Error:
            raise ValueError("The chunk is not valid Base64 data.") from None
        if not 0 < len(data) <= CHUNK_BYTES or offset < 0:
            raise ValueError("Invalid chunk offset or size.")
        with self.lock:
            item = self.get(identity)
            if item.stage is None:
                raise ValueError("This file transfer is a download.")
            if offset == item.last_offset and sha256(data).digest() == item.last_chunk:
                return {"next_offset": item.offset}
            if offset != item.offset or item.offset + len(data) > item.size:
                raise ValueError(f"Expected byte offset {item.offset} within the declared file size.")
            try:
                self.remaining_space(item.stage.parent, item.device, item.size - item.offset, identity)
                if revision(os.fstat(item.stream.fileno())) != item.stage_revision:
                    raise ValueError("The staged upload changed.")
                if item.stream.write(data) != len(data):
                    raise OSError("Could not write the complete upload chunk.")
                item.stage_revision = revision(os.fstat(item.stream.fileno()))
            except (OSError, ValueError):
                self.discard(identity)
                raise
            item.digest.update(data)
            item.offset += len(data)
            item.last_offset = offset
            item.last_chunk = sha256(data).digest()
            return {"next_offset": item.offset}

    def finish(self, identity: str, session_ids: dict[str, str]) -> dict:
        with self.access.file_gate, self.lock:
            receipt = self.receipts.get(identity)
            if receipt is not None and receipt[0] == self.access.principal() and receipt[2] > monotonic():
                self.access.workspace(receipt[1])
                return receipt[3]
            item = self.get(identity)
            if item.offset != item.size:
                raise ValueError(f"Transfer is incomplete. Expected byte offset {item.offset}.")
            checksum = item.digest.hexdigest()
            if item.stage is None:
                self.check_download(identity, item)
                result = {"path": item.relative.as_posix(), "revision": item.expected, "size": item.size}
            else:
                if checksum != item.expected_sha256:
                    self.discard(identity)
                    raise ValueError("The upload SHA-256 does not match.")
                result = self.commit_upload(item, session_ids)
            self.discard(identity)
            result["sha256"] = checksum
            if len(self.receipts) >= MAX_RECEIPTS:
                self.receipts.pop(next(iter(self.receipts)))
            self.receipts[identity] = (item.principal, item.workspace_id, monotonic() + IDLE_SECONDS, result)
            return result

    def commit_upload(self, item: Transfer, session_ids: dict[str, str]) -> dict:
        stage = item.stage
        assert stage is not None

        def commit(root):
            target, mode, info = self.transfer_target(root, item.relative, item.upload_area)
            verify_revision(mode, info, item.expected)
            if not self.staging_directory_exists(root, stage.parent, item.upload_area):
                raise ValueError("The staging directory changed.")
            staged = stage.lstat()
            opened = os.fstat(item.stream.fileno()) if not item.stream.closed else None
            if revision(staged) != item.stage_revision or staged.st_nlink != 1 or staged.st_size != item.size:
                raise ValueError("The staged upload changed.")
            if opened is not None:
                if revision(opened) != item.stage_revision:
                    raise ValueError("The staged upload changed.")
                item.stream.flush()
                os.fsync(item.stream.fileno())
                if os.name == "nt":
                    item.stream.close()
            if not item.upload_area:
                target.parent.mkdir(parents=True, exist_ok=True)
            _, mode, info = self.transfer_target(root, item.relative, item.upload_area)
            verify_revision(mode, info, item.expected)
            publish_file(stage, target, mode, info)
            return file_metadata(item.relative, target.stat())

        if item.upload_area:
            if session_ids:
                raise ValueError("Workspace Files uploads do not use Sheet editing sessions.")
            return commit(self.access.workspace(item.workspace_id))
        return mutate_files(self.access, item.workspace_id, (item.relative,), session_ids, commit)

    def cancel(self, identity: str) -> dict:
        with self.lock:
            self.get(identity)
            self.discard(identity)
            return {"cancelled": True}
