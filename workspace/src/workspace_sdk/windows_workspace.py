from contextlib import contextmanager

import pythoncom
import pywintypes
from win32com.client import GetObject
from win32com.shell import shell, shellcon


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
            item = shell.SHCreateItemFromParsingName(str(path), None, shell.IID_IShellItem)
            source = item.BindToHandler(None, shell.BHID_Transfer, shell.IID_ITransferSource)
            destination = shell.SHCreateItemFromIDList(
                shell.SHGetSpecialFolderLocation(0, shellcon.CSIDL_BITBUCKET), shell.IID_IShellItem,
            )
            _, recycled_item = source.RecycleItem(item, destination, 0)
            if recycled_item is None or path.exists():
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
