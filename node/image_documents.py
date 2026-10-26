"""Bounded on-device recognition of explicitly selected single images."""
from pathlib import Path
import sys
import tempfile
import threading
import struct
import zlib
from pdf_documents import PDFError, SLOTS, supervise, worker_command

IMAGE_EXTENSIONS = ('png', 'jpg', 'jpeg', 'heic', 'heif')
MAX_IMAGE = 8 * 1024 * 1024
TYPES = {'png': 'public.png', 'jpg': 'public.jpeg', 'jpeg': 'public.jpeg',
         'heic': 'public.heic', 'heif': ('public.heif','public.heic')}
ERRORS = {'invalid_image', 'image_frames', 'image_dimensions', 'image_ocr_failed', 'text_limit'}


def image_ocr_available():
    return sys.platform == 'darwin'


def validate_container(data, extension):
    # ImageIO can decode a useful prefix of a truncated image. Require complete
    # container boundaries before letting partial pixels look like a full import.
    if extension in ('jpg','jpeg'):
        if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):raise PDFError('invalid_image')
    elif extension=='png':
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):raise PDFError('invalid_image')
        position=8;count=0;ended=False
        while position<len(data):
            if len(data)-position<12 or count>=10000:raise PDFError('invalid_image')
            size=struct.unpack_from('>I',data,position)[0];end=position+12+size
            if end>len(data):raise PDFError('invalid_image')
            kind=data[position+4:position+8];payload=data[position+8:end-4]
            expected=struct.unpack_from('>I',data,end-4)[0]
            if zlib.crc32(kind+payload)&0xffffffff!=expected:raise PDFError('invalid_image')
            if count==0 and (kind!=b'IHDR' or size!=13):raise PDFError('invalid_image')
            if kind==b'IEND':
                if size!=0 or end!=len(data):raise PDFError('invalid_image')
                ended=True
            position=end;count+=1
        if not ended:raise PDFError('invalid_image')
    else:
        position=0;count=0
        while position<len(data):
            if len(data)-position<8 or count>=10000:raise PDFError('invalid_image')
            size=struct.unpack_from('>I',data,position)[0];kind=data[position+4:position+8];header=8
            if size==1:
                if len(data)-position<16:raise PDFError('invalid_image')
                size=struct.unpack_from('>Q',data,position+8)[0];header=16
            elif size==0:size=len(data)-position
            if size<header or position+size>len(data) or (count==0 and kind!=b'ftyp'):raise PDFError('invalid_image')
            position+=size;count+=1
        if count==0:raise PDFError('invalid_image')


def extract_image(data, extension, scratch, cancel=None, seconds=60):
    if not image_ocr_available():raise PDFError('ocr_unavailable')
    if extension not in IMAGE_EXTENSIONS or not isinstance(data, bytes) or not data:raise PDFError('invalid_image')
    if len(data) > MAX_IMAGE:raise PDFError('image_too_large')
    validate_container(data,extension)
    cancel = cancel or threading.Event()
    if not SLOTS.acquire(blocking=False):raise PDFError('parser_busy')
    try:
        scratch = Path(scratch);scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='.image-', dir=scratch) as temporary:
            folder=Path(temporary).resolve();source=folder/'input.image';source.write_bytes(data);source.chmod(0o600)
            command=worker_command(folder);command[1]=str(source);command.append('--recognize-image')
            value=supervise(command,folder,cancel,seconds,1024*1024*1024)
        if not isinstance(value,dict):raise PDFError('invalid_image')
        if 'error' in value:raise PDFError(value['error'] if value['error'] in ERRORS else 'invalid_image')
        if value.get('format')!='image' or value.get('parser')!='apple-vision-revision-3' or value.get('containerType') not in ((TYPES[extension],) if isinstance(TYPES[extension],str) else TYPES[extension]):
            raise PDFError('invalid_image')
        for key,limit in [('width',16000),('height',16000),('orientation',8),('recognizedWidth',2400),('recognizedHeight',2400)]:
            if type(value.get(key)) is not int or not 1<=value[key]<=limit:raise PDFError('invalid_image')
        if value['width']*value['height']>64_000_000:raise PDFError('image_dimensions')
        lines=value.get('lines')
        if not isinstance(lines,list) or len(lines)>2000 or any(not isinstance(line,str) or not line.strip() or '\0' in line or '\n' in line or '\r' in line for line in lines):
            raise PDFError('invalid_image')
        if value.get('status')!=('recognized' if lines else 'no_text'):raise PDFError('invalid_image')
        if len('\n'.join(lines).encode())>2*1024*1024:raise PDFError('text_limit')
        return value
    finally:SLOTS.release()
