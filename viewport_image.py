"""Bounded viewport pixels from Windows' built-in imaging codecs (read-only)."""
import ctypes as C
from ctypes import wintypes as W
import uuid
from pathlib import Path
from PyQt6.QtGui import QImage

class GUID(C.Structure):
    _fields_=[('data',C.c_ubyte*16)]
    def __init__(self,value):
        super().__init__();self.data[:]=uuid.UUID(value).bytes_le
class Rect(C.Structure):
    _fields_=[('x',C.c_int),('y',C.c_int),('width',C.c_int),('height',C.c_int)]

def call(obj,index,types,*args):
    table=C.cast(obj,C.POINTER(C.POINTER(C.c_void_p))).contents
    result=C.WINFUNCTYPE(C.c_long,C.c_void_p,*types)(table[index])(obj,*args)
    if result<0:
        raise OSError(f'Windows 이미지 디코더 오류 0x{result&0xffffffff:08X}')
    return result

def viewport(path,crop,size,transformation=0):
    """Crop in oriented source coordinates, then scale; never allocate full output."""
    with open(path,'rb') as stream:
        header=stream.read(12)
    if header[:4]==b'RIFF' and header[8:12]==b'WEBP':
        return webp_viewport(path,crop,size)
    ole=C.OleDLL('ole32')
    try:
        hr=ole.CoInitializeEx(None,2)
    except OSError as error:
        if (error.winerror & 0xffffffff)!=0x80010106:
            raise
        hr=-1 # Existing COM apartment belongs to the caller.
    objects=[]
    def create(parent,index,types=(),args=()):
        obj=C.c_void_p();call(parent,index,(*types,C.POINTER(C.c_void_p)),*args,C.byref(obj));objects.append(obj);return obj
    try:
        factory=C.c_void_p()
        clsid=GUID('cacaf262-9370-4615-a13b-9f5539da4c0a')
        iid=GUID('ec5ec8a9-c395-4314-9c77-54d7a935ff70')
        ole.CoCreateInstance(C.byref(clsid),None,1,C.byref(iid),C.byref(factory));objects.append(factory)
        decoder=create(factory,3,(W.LPCWSTR,C.c_void_p,W.DWORD,C.c_uint),(str(path),None,0x80000000,0))
        source=create(decoder,13,(C.c_uint,),(0,))
        if transformation:
            rotator=create(factory,13)
            rotation={1:8,2:16,3:2,4:1,5:9,6:17,7:3}[transformation]
            call(rotator,8,(C.c_void_p,C.c_uint),source,rotation);source=rotator
        clip=create(factory,12)
        rectangle=Rect(*crop)
        call(clip,8,(C.c_void_p,C.POINTER(Rect)),source,C.byref(rectangle))
        scaler=create(factory,11)
        width,height=size
        call(scaler,8,(C.c_void_p,C.c_uint,C.c_uint,C.c_uint),clip,width,height,3)
        converter=create(factory,10)
        # 32bpp BGRA is directly consumable by Qt's ARGB32 image format.
        pixel=GUID('6fddc324-4e03-4bfe-b185-3d77768dc90f')
        call(converter,8,(C.c_void_p,C.POINTER(GUID),C.c_uint,C.c_void_p,C.c_double,C.c_uint),scaler,C.byref(pixel),0,None,0.0,0)
        image=QImage(width,height,QImage.Format.Format_ARGB32)
        if image.isNull():raise MemoryError('표시 영역 메모리를 확보하지 못했습니다.')
        call(converter,7,(C.c_void_p,C.c_uint,C.c_uint,C.c_void_p),None,image.bytesPerLine(),image.sizeInBytes(),int(image.bits()))
        return image
    finally:
        for obj in reversed(objects):
            call(obj,2,())
        if hr in (0,1):ole.CoUninitialize()


# libwebp's stable advanced decoder ABI: cropping precedes scaling.
class RGBABuffer(C.Structure):
    _fields_=[('pixels',C.c_void_p),('stride',C.c_int),('size',C.c_size_t)]
class YUVABuffer(C.Structure):
    _fields_=[(n,C.c_void_p) for n in ('y','u','v','a')]+[(n,C.c_int) for n in ('ys','us','vs','as_')]+[(n,C.c_size_t) for n in ('yb','ub','vb','ab')]
class BufferUnion(C.Union):
    _fields_=[('rgba',RGBABuffer),('yuva',YUVABuffer)]
class DecodeBuffer(C.Structure):
    _fields_=[(n,C.c_int) for n in ('colorspace','width','height','external')]+[('buffer',BufferUnion),('pad',C.c_uint32*4),('private',C.c_void_p)]
class Features(C.Structure):
    _fields_=[(n,C.c_int) for n in ('width','height','alpha','animation','format')]+[('pad',C.c_uint32*5)]
class Options(C.Structure):
    _fields_=[(n,C.c_int) for n in ('bypass','no_fancy','crop','left','top','width','height','scale','scaled_width','scaled_height','threads','dither','flip','alpha_dither')]+[('pad',C.c_uint32*5)]
class Config(C.Structure):
    _fields_=[('input',Features),('output',DecodeBuffer),('options',Options)]

def webp_viewport(path,crop,size):
    decoder=C.CDLL(str(Path(__file__).with_name('libwebp.dll')))
    decoder.WebPInitDecoderConfigInternal.argtypes=[C.POINTER(Config),C.c_int]
    decoder.WebPDecode.argtypes=[C.c_char_p,C.c_size_t,C.POINTER(Config)]
    decoder.WebPFreeDecBuffer.argtypes=[C.POINTER(DecodeBuffer)]
    config=Config()
    if not decoder.WebPInitDecoderConfigInternal(C.byref(config),0x0209):
        raise OSError('WebP 디코더 버전이 일치하지 않습니다.')
    image=QImage(*size,QImage.Format.Format_ARGB32)
    if image.isNull():raise MemoryError('표시 영역 메모리를 확보하지 못했습니다.')
    config.output.colorspace=3
    config.output.external=1
    config.output.buffer.rgba=RGBABuffer(int(image.bits()),image.bytesPerLine(),image.sizeInBytes())
    config.options.crop=1
    config.options.left,config.options.top,config.options.width,config.options.height=crop
    config.options.scale=1
    config.options.scaled_width,config.options.scaled_height=size
    config.options.threads=1
    data=Path(path).read_bytes()
    try:
        status=decoder.WebPDecode(data,len(data),C.byref(config))
        if status:raise OSError(f'WebP 이미지 디코딩 실패 ({status})')
        return image
    finally:
        decoder.WebPFreeDecBuffer(C.byref(config.output))

