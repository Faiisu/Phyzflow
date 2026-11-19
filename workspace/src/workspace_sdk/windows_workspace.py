from contextlib import contextmanager

import pythoncom
import pywintypes
from win32com.client import GetObject
from win32com.shell import shell, shellcon


FOFX_RECYCLEONDELETE = 0x00080000


@contextmanager
def com_apartment():
    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def recycle_folder(path):
    with com_apartment():
        try:
            operation = pythoncom.CoCreateInstance(
                shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation,
            )
            operation.SetOperationFlags(
                FOFX_RECYCLEONDELETE | shellcon.FOF_NOERRORUI | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT,
            )
            item = shell.SHCreateItemFromParsingName(str(path), None, shell.IID_IShellItem)
            operation.DeleteItem(item, None)
            operation.PerformOperations()
            if operation.GetAnyOperationsAborted() or path.exists():
                raise OSError(f"Could not move {path.name} to the Windows Recycle Bin.")
        except pywintypes.com_error as error:
            raise OSError(f"Could not move {path.name} to the Windows Recycle Bin: {error.strerror}.") from error


def windows_process_commands():
    with com_apartment():
        try:
            service = GetObject(r"winmgmts:\\.\root\cimv2")
            return [
                (process.ProcessId, process.CommandLine or "", process.ExecutablePath or "")
                for process in service.ExecQuery("SELECT ProcessId,CommandLine,ExecutablePath FROM Win32_Process")
            ]
        except pywintypes.com_error as error:
            raise OSError(f"Could not verify Windows Workspace processes: {error.strerror}.") from error
