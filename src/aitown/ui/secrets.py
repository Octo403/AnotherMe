"""Optional Windows current-user DPAPI storage; no plaintext fallback."""
import base64
import ctypes
import os
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def transform(raw, decrypt=False):
    if os.name != 'nt':
        raise ValueError('本机不支持 Windows 加密保存，请取消保存 Key，仅在本次服务中使用。')
    buffer = (ctypes.c_ubyte*len(raw)).from_buffer_copy(raw)
    incoming = Blob(len(raw), buffer)
    outgoing = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ValueError('当前 Windows 账户无法读取或保存加密 Key，请重新填写；不会退回明文保存。')
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


def protect(value):
    return base64.b64encode(transform(value.encode('utf-8'))).decode('ascii') if value else ''


def unprotect(value):
    return transform(base64.b64decode(value), decrypt=True).decode('utf-8') if value else ''
