"""Current-user Windows credentials for optional report drafting.

Native buffers are cleared after use. Python strings and copies cannot be
reliably erased; callers should keep keys only for the request that needs them.
"""

import ctypes
import os
import re

_TARGET = 'Saga/OpenAI/report-drafting'
_GENERIC = 1
_LOCAL_MACHINE = 2  # Current user, this machine; not enterprise roaming.
_NOT_FOUND = 1168
_MAX_BYTES = 2560
_BEARER = re.compile(r'[A-Za-z0-9._~+/-]+=*', re.ASCII)
_DWORD = ctypes.c_uint32
_BYTE_POINTER = ctypes.POINTER(ctypes.c_ubyte)


class _FILETIME(ctypes.Structure):
    _fields_ = [('low', _DWORD), ('high', _DWORD)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ('Flags', _DWORD), ('Type', _DWORD),
        ('TargetName', ctypes.c_wchar_p), ('Comment', ctypes.c_wchar_p),
        ('LastWritten', _FILETIME), ('CredentialBlobSize', _DWORD),
        ('CredentialBlob', _BYTE_POINTER), ('Persist', _DWORD),
        ('AttributeCount', _DWORD), ('Attributes', ctypes.c_void_p),
        ('TargetAlias', ctypes.c_wchar_p), ('UserName', ctypes.c_wchar_p),
    ]


_PCREDENTIAL = ctypes.POINTER(_CREDENTIALW)


class CredentialError(Exception):
    """A sanitized validation or Windows credential-store failure."""


def _load_api():
    if os.name != 'nt':
        raise CredentialError('Windows Credential Manager is unavailable on this system.')
    try:
        api = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
        api.CredReadW.argtypes = [ctypes.c_wchar_p, _DWORD, _DWORD,
                                 ctypes.POINTER(_PCREDENTIAL)]
        api.CredReadW.restype = ctypes.c_int32
        api.CredWriteW.argtypes = [_PCREDENTIAL, _DWORD]
        api.CredWriteW.restype = ctypes.c_int32
        api.CredDeleteW.argtypes = [ctypes.c_wchar_p, _DWORD, _DWORD]
        api.CredDeleteW.restype = ctypes.c_int32
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        return api
    except (OSError, AttributeError):
        raise CredentialError('Windows Credential Manager could not be opened.') from None


def _validate(key):
    if (not isinstance(key, str) or not 1 <= len(key) <= _MAX_BYTES
            or _BEARER.fullmatch(key) is None):
        raise CredentialError('Enter a valid API key without spaces or control characters.')


def _free_credential(api, credential):
    if credential:
        try:
            item = credential.contents
            if item.CredentialBlob and 0 < item.CredentialBlobSize <= _MAX_BYTES:
                ctypes.memset(item.CredentialBlob, 0, item.CredentialBlobSize)
        finally:
            api.CredFree(credential)


class CredentialStore:
    """One fixed application credential, independent of database/install paths."""

    def __init__(self):
        self._target = _TARGET

    def read(self) -> str | None:
        api = _load_api()
        credential = _PCREDENTIAL()
        if not api.CredReadW(self._target, _GENERIC, 0, ctypes.byref(credential)):
            if ctypes.get_last_error() == _NOT_FOUND:
                return None
            raise CredentialError('Windows Credential Manager could not read the API key.')
        try:
            if not credential:
                raise CredentialError('Windows Credential Manager returned an invalid API key.')
            item = credential.contents
            if not item.CredentialBlob or not 1 <= item.CredentialBlobSize <= _MAX_BYTES:
                raise CredentialError('Windows Credential Manager returned an invalid API key.')
            try:
                key = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize).decode('ascii')
            except UnicodeDecodeError:
                raise CredentialError('The stored API key is invalid; replace it in settings.') from None
            _validate(key)
            return key
        finally:
            _free_credential(api, credential)

    def exists(self) -> bool:
        """Check presence without copying the credential into a Python string."""
        api = _load_api()
        credential = _PCREDENTIAL()
        if not api.CredReadW(self._target, _GENERIC, 0, ctypes.byref(credential)):
            if ctypes.get_last_error() == _NOT_FOUND:
                return False
            raise CredentialError('Windows Credential Manager could not read the API key.')
        try:
            if not credential:
                raise CredentialError('Windows Credential Manager returned an invalid API key.')
            return True
        finally:
            _free_credential(api, credential)

    def save(self, key: str) -> None:
        _validate(key)
        api = _load_api()
        encoded = key.encode('ascii')
        buffer = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        del encoded
        try:
            credential = _CREDENTIALW()
            credential.Type = _GENERIC
            credential.TargetName = self._target
            credential.CredentialBlobSize = len(buffer)
            credential.CredentialBlob = ctypes.cast(buffer, _BYTE_POINTER)
            credential.Persist = _LOCAL_MACHINE
            credential.UserName = 'Saga'
            if not api.CredWriteW(ctypes.byref(credential), 0):
                raise CredentialError('Windows Credential Manager could not save the API key.')
        finally:
            ctypes.memset(buffer, 0, len(buffer))

    def remove(self) -> None:
        api = _load_api()
        if (not api.CredDeleteW(self._target, _GENERIC, 0)
                and ctypes.get_last_error() != _NOT_FOUND):
            raise CredentialError('Windows Credential Manager could not remove the API key.')


def _isolated_test_store():
    """Private native-smoke helper; never reads or modifies the production target."""
    import uuid

    store = CredentialStore()
    store._target = f'Saga/tests/OpenAI/{uuid.uuid4()}'
    return store
