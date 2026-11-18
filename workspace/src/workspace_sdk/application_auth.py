"""Provision the built-in administrator and manage browser sessions."""

from errno import EEXIST, ENOTEMPTY
from hashlib import scrypt
from hmac import compare_digest
from http.cookies import CookieError, SimpleCookie
from os import getuid
from pathlib import Path
from secrets import token_bytes, token_urlsafe
from stat import S_ISDIR, S_ISREG
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic


SESSION_SECONDS = 12 * 60 * 60


def password_verifier(password: str, salt: bytes) -> bytes:
    return scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def private_write(path: Path, value: bytes) -> None:
    path.touch(mode=0o600, exist_ok=False)
    path.write_bytes(value)


class Administrator:
    def __init__(self, state: Path) -> None:
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        state_info = state.stat()
        if state_info.st_uid != getuid() or state_info.st_mode & 0o022:
            raise ValueError("The application state folder must be owned by the installer and writable only by its owner.")

        self.directory = state / "admin"
        self.initial_password = self.directory / "initial-password"
        if not self.directory.exists():
            with TemporaryDirectory(dir=state, prefix=".admin-") as staging:
                folder = Path(staging)
                salt = token_bytes(16)
                password = token_urlsafe(24)
                private_write(folder / "initial-password", (password + "\n").encode())
                private_write(folder / "verifier", salt + password_verifier(password, salt))
                try:
                    folder.rename(self.directory)
                except OSError as error:
                    if error.errno not in (EEXIST, ENOTEMPTY):
                        raise

        directory_info = self.directory.lstat()
        verifier = self.directory / "verifier"
        verifier_info = verifier.lstat()
        if (
            not S_ISDIR(directory_info.st_mode) or directory_info.st_uid != getuid()
            or directory_info.st_mode & 0o077
            or not S_ISREG(verifier_info.st_mode) or verifier_info.st_uid != getuid()
            or verifier_info.st_mode & 0o077
        ):
            raise ValueError("The administrator credential requires owner-only access.")
        record = verifier.read_bytes()
        if len(record) != 48:
            raise ValueError("The administrator verifier is invalid.")
        self.salt, self.verifier = record[:16], record[16:]
        self.cookie_name = f"phyzflow_session_{self.salt.hex()}"
        self.sessions: dict[str, float] = {}
        self.lock = Lock()

    def sign_in(self, username: str, password: str) -> str | None:
        if username != "admin" or not compare_digest(password_verifier(password, self.salt), self.verifier):
            return None
        token = token_urlsafe(32)
        with self.lock:
            now = monotonic()
            self.sessions = {key: expires for key, expires in self.sessions.items() if expires > now}
            self.sessions[token] = now + SESSION_SECONDS
        return f"{self.cookie_name}={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict"

    def authenticated(self, cookies: str) -> bool:
        try:
            cookie = SimpleCookie(cookies).get(self.cookie_name)
        except CookieError:
            return False
        if cookie is None:
            return False
        with self.lock:
            return self.sessions.get(cookie.value, 0) > monotonic()
