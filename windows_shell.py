"""Windows identity and relaunch properties for the Python-hosted Luma window.

Uses the documented AppUserModel properties instead of pinning pythonw.exe.
https://learn.microsoft.com/windows/win32/properties/props-system-appusermodel-relaunchcommand
"""
import ctypes as C
from pathlib import Path
import uuid

APP_ID='Luma.Desktop.Library'

class GUID(C.Structure):
    _fields_=[('data',C.c_ubyte*16)]
    def __init__(self,value):
        super().__init__((C.c_ubyte*16).from_buffer_copy(uuid.UUID(value).bytes_le))

class PROPERTYKEY(C.Structure):
    _fields_=[('fmtid',GUID),('pid',C.c_uint32)]

class VariantData(C.Union):
    _fields_=[('pointer',C.c_void_p),('storage',C.c_ubyte*(16 if C.sizeof(C.c_void_p)==8 else 8))]

class PROPVARIANT(C.Structure):
    _anonymous_=('value',)
    _fields_=[('vt',C.c_ushort),('r1',C.c_ushort),('r2',C.c_ushort),('r3',C.c_ushort),('value',VariantData)]

IID=GUID('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')
FMTID=GUID('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3')

def checked(result):
    if result<0:
        raise OSError(f'Windows shell HRESULT 0x{result & 0xffffffff:08X}')

def process_identity():
    api=C.windll.shell32.SetCurrentProcessExplicitAppUserModelID
    api.argtypes=[C.c_wchar_p]
    api.restype=C.c_long
    checked(api(APP_ID))

class PropertyStore:
    def __init__(self,hwnd=None,path=None):
        self.pointer=C.c_void_p()
        if path is None:
            api=C.windll.shell32.SHGetPropertyStoreForWindow
            api.argtypes=[C.c_void_p,C.POINTER(GUID),C.POINTER(C.c_void_p)]
            api.restype=C.c_long
            checked(api(hwnd,C.byref(IID),C.byref(self.pointer)))
        else:
            api=C.windll.shell32.SHGetPropertyStoreFromParsingName
            api.argtypes=[C.c_wchar_p,C.c_void_p,C.c_uint32,C.POINTER(GUID),C.POINTER(C.c_void_p)]
            api.restype=C.c_long
            checked(api(str(path),None,2,C.byref(IID),C.byref(self.pointer)))
    def method(self,index,*args):
        vtable=C.cast(self.pointer,C.POINTER(C.POINTER(C.c_void_p))).contents
        return C.WINFUNCTYPE(C.c_long,C.c_void_p,*args)(vtable[index])
    def set(self,pid,text):
        key=PROPERTYKEY(FMTID,pid)
        value=PROPVARIANT()
        if text is not None:
            buffer=C.create_unicode_buffer(text)
            value.vt=31 # VT_LPWSTR. SetValue copies the string synchronously.
            value.pointer=C.cast(buffer,C.c_void_p).value
        checked(self.method(6,C.POINTER(PROPERTYKEY),C.POINTER(PROPVARIANT))(self.pointer,C.byref(key),C.byref(value)))
    def get(self,pid):
        key=PROPERTYKEY(FMTID,pid)
        value=PROPVARIANT()
        checked(self.method(5,C.POINTER(PROPERTYKEY),C.POINTER(PROPVARIANT))(self.pointer,C.byref(key),C.byref(value)))
        try:
            return C.wstring_at(value.pointer) if value.vt==31 and value.pointer else None
        finally:
            api=C.windll.ole32.PropVariantClear
            api.argtypes=[C.POINTER(PROPVARIANT)]
            api(C.byref(value))
    def commit(self):
        checked(self.method(7)(self.pointer))
    def close(self):
        if self.pointer:
            self.method(2)(self.pointer)
            self.pointer=C.c_void_p()

def configure_window(hwnd,root):
    root=Path(root).resolve()
    store=PropertyStore(hwnd=hwnd)
    try:
        # Set relaunch metadata before the explicit window identity.
        store.set(2,f'"{root / "Luma.exe"}"')
        store.set(3,str(root/'Luma.exe')+',0')
        store.set(4,'Luma')
        store.set(5,APP_ID)
    finally:
        store.close()

def clear_window(hwnd):
    store=PropertyStore(hwnd=hwnd)
    try:
        for pid in (2,3,4,5):
            store.set(pid,None)
    finally:
        store.close()

def identify_shortcut(path):
    store=PropertyStore(path=path)
    try:
        store.set(5,APP_ID)
        store.commit()
    finally:
        store.close()
