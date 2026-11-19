from pathlib import Path
from stat import FILE_ATTRIBUTE_REPARSE_POINT, S_ISDIR, S_ISREG

import ntsecuritycon
import pywintypes
import win32api
import win32con
import win32file
import win32security


class WindowsCredentialPermissions:
    def __init__(self) -> None:
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            self.user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        self.trusted = (
            self.user,
            win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None),
            win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid, None),
        )

    def prepare(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        acl = win32security.ACL()
        inherit = win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
        acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, inherit, ntsecuritycon.FILE_ALL_ACCESS, self.user)
        security = pywintypes.SECURITY_ATTRIBUTES()
        descriptor = security.SECURITY_DESCRIPTOR
        descriptor.SetSecurityDescriptorDacl(True, acl, False)
        descriptor.SetSecurityDescriptorControl(
            win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED,
        )
        try:
            win32file.CreateDirectory(str(path), security)
        except pywintypes.error as error:
            if error.winerror != 183:
                raise OSError(f"Could not create a private application state folder: {error.strerror}.") from error
        self.verify(path, directory=True)

    def verify(self, path: Path, *, directory: bool = False) -> None:
        invalid = f"Phyzflow requires private access to {path}."
        info = path.lstat()
        expected_type = S_ISDIR if directory else S_ISREG
        if not expected_type(info.st_mode) or info.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(invalid)
        try:
            descriptor = win32security.GetNamedSecurityInfo(
                str(path), win32security.SE_FILE_OBJECT,
                win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
            )
        except pywintypes.error as error:
            raise ValueError(invalid) from error
        acl = descriptor.GetSecurityDescriptorDacl()
        if descriptor.GetSecurityDescriptorOwner() not in self.trusted or acl is None:
            raise ValueError(invalid)
        trusted = (*self.trusted, win32security.ConvertStringSidToSid("S-1-3-4"))
        for index in range(acl.GetAceCount()):
            kind, _, sid = acl.GetAce(index)
            if kind[0] != win32security.ACCESS_ALLOWED_ACE_TYPE or sid not in trusted:
                raise ValueError(invalid)
