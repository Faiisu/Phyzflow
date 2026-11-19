from os import fspath
from subprocess import TimeoutExpired, list2cmdline
from threading import Lock
from time import monotonic, sleep

import pywintypes
import win32api
import win32con
import win32event
import win32job
import win32process


class WindowsWorker:
    def __init__(self, argv, *, cwd, env) -> None:
        self.args = [fspath(item) for item in argv]
        job = None
        try:
            job = win32job.CreateJobObject(None, "")
            limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
            process, thread, pid, _ = win32process.CreateProcess(
                self.args[0], list2cmdline(self.args), None, None, False,
                win32con.CREATE_SUSPENDED | win32con.CREATE_UNICODE_ENVIRONMENT,
                env, fspath(cwd), win32process.STARTUPINFO(),
            )
            try:
                try:
                    win32job.AssignProcessToJobObject(job, process)
                    win32process.ResumeThread(thread)
                except BaseException:
                    try:
                        win32api.TerminateProcess(process, 1)
                    finally:
                        process.Close()
                    raise
            finally:
                thread.Close()
        except BaseException as error:
            if job is not None:
                job.Close()
            if isinstance(error, pywintypes.error):
                raise OSError(f"Could not start the Windows sheet process: {error.strerror}.") from error
            raise
        self.pid = pid
        self._job = job
        self._stop_lock = Lock()
        self._process = process
        self._exit_code = None

    def poll(self) -> int | None:
        try:
            return self.wait(timeout=0)
        except TimeoutExpired:
            return None

    def wait(self, timeout: float) -> int:
        if self._exit_code is None:
            if win32event.WaitForSingleObject(self._process, int(timeout * 1000)) == win32event.WAIT_TIMEOUT:
                raise TimeoutExpired(self.args, timeout)
            self._exit_code = win32process.GetExitCodeProcess(self._process)
        return self._exit_code

    def stop(self) -> None:
        with self._stop_lock:
            if self._job is None:
                return
            try:
                win32job.TerminateJobObject(self._job, 1)
                deadline = monotonic() + 4
                while win32job.QueryInformationJobObject(
                    self._job, win32job.JobObjectBasicAccountingInformation,
                )["ActiveProcesses"]:
                    if monotonic() >= deadline:
                        raise OSError(f"Processes for Windows sheet worker {self.pid} are still running.")
                    sleep(.02)
                self.wait(timeout=max(0, deadline - monotonic()))
            except (pywintypes.error, TimeoutExpired) as error:
                raise OSError(f"Could not verify that processes for Windows sheet worker {self.pid} stopped.") from error
            self._job.Close()
            self._job = None
