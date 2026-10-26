"""Bounded child-process supervision for local, explicitly chosen PDF bytes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

MAX_FILE = 2 * 1024 * 1024
MAX_OUTPUT = 4 * MAX_FILE
MAX_RSS = 384 * 1024 * 1024
SLOTS = threading.BoundedSemaphore(1)
ERRORS = {'parser_unavailable', 'encrypted', 'page_limit', 'text_limit', 'parser_limit', 'invalid_pdf'}


def ocr_available():
    return sys.platform == 'darwin'


class PDFError(Exception):
    pass


def resident_bytes(pid):
    if sys.platform.startswith('linux'):
        return int(Path('/proc/'+str(pid)+'/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    if sys.platform == 'darwin':
        value = subprocess.check_output(['/bin/ps', '-o', 'rss=', '-p', str(pid)],
                                        stderr=subprocess.DEVNULL, timeout=1).strip()
        return int(value) * 1024
    raise OSError('Unsupported parser monitor')


def worker_command(folder):
    if sys.platform == 'darwin':
        candidates = [Path(__file__).resolve().parent.parent/'Helpers/PDFTextWorker',
            Path(__file__).resolve().parents[1]/'DerivedData/Mac/Build/Products/Debug/PDFTextWorker']
        worker = next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)
        if worker is None:raise PDFError('parser_unavailable')
        return [str(worker), str(folder/'input.pdf'), str(folder/'result.json')]
    return [sys.executable, str(Path(__file__).with_name('pdf_worker.py').resolve()),
            str(folder/'input.pdf'), str(folder/'result.json')]


def supervise(command, folder, cancel, seconds=10, memory_limit=MAX_RSS):
    """The command is application-owned. Source contents never become command arguments."""
    output = folder / 'result.json'
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, cwd=folder, close_fds=True)
    deadline = time.monotonic() + seconds
    try:
        while process.poll() is None:
            if cancel.is_set():raise PDFError('cancelled')
            if time.monotonic() >= deadline:raise PDFError('parser_timeout')
            try:
                if resident_bytes(process.pid) > memory_limit:raise PDFError('parser_limit')
            except (OSError, ValueError, subprocess.SubprocessError):
                if process.poll() is None:raise PDFError('parser_unavailable')
            if output.exists() and output.stat().st_size > MAX_OUTPUT:raise PDFError('text_limit')
            cancel.wait(.1)
        if cancel.is_set():raise PDFError('cancelled')
        if process.returncode != 0:raise PDFError('parser_limit')
        if not output.is_file() or output.stat().st_size > MAX_OUTPUT:raise PDFError('text_limit')
        try:return json.loads(output.read_bytes())
        except (ValueError, UnicodeError):raise PDFError('invalid_pdf')
    finally:
        if process.poll() is None:process.kill()
        process.wait(timeout=3)
        process.stdin.close()


def extract_pdf(data, scratch, cancel=None, seconds=10, recognize_scans=False):
    if not isinstance(data, bytes) or not data:raise PDFError('invalid_pdf')
    if len(data) > MAX_FILE:raise PDFError('too_large')
    if not data.startswith(b'%PDF-'):raise PDFError('invalid_pdf')
    if recognize_scans and not ocr_available():raise PDFError('ocr_unavailable')
    cancel = cancel or threading.Event()
    if not SLOTS.acquire(blocking=False):raise PDFError('parser_busy')
    try:
        scratch = Path(scratch);scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='.pdf-', dir=scratch) as temporary:
            folder = Path(temporary).resolve();file = folder / 'input.pdf'
            file.write_bytes(data);file.chmod(0o600)
            command = worker_command(folder)
            if recognize_scans:command.append('--recognize-scans')
            value = supervise(command, folder, cancel, seconds, 1024*1024*1024 if recognize_scans else MAX_RSS)
        if not isinstance(value, dict):raise PDFError('invalid_pdf')
        if 'error' in value:raise PDFError(value['error'] if value['error'] in ERRORS else 'invalid_pdf')
        if recognize_scans and value.get('ocrRequested') is not True:raise PDFError('ocr_unavailable')
        pages = value.get('pages')
        if (not isinstance(pages, list) or not 1 <= len(pages) <= 200
                or value.get('pageCount') != len(pages)):raise PDFError('invalid_pdf')
        total = 0
        for number, page in enumerate(pages, 1):
            if (not isinstance(page, dict) or page.get('number') != number
                    or page.get('status') not in ('text', 'empty', 'unreadable')
                    or not isinstance(page.get('text'), str) or '\0' in page['text']):raise PDFError('invalid_pdf')
            if page.get('method') not in (None, 'ocr') or page.get('ocrStatus') not in (None, 'recognized', 'no_text', 'failed', 'limit'):
                raise PDFError('invalid_pdf')
            if (page.get('method') == 'ocr') != (page.get('ocrStatus') == 'recognized'):
                raise PDFError('invalid_pdf')
            if page.get('method') == 'ocr' and (not recognize_scans or page['status'] != 'text' or not page['text'].strip()):
                raise PDFError('invalid_pdf')
            total += len(page['text'].encode())
        if total > MAX_FILE:raise PDFError('text_limit')
        return value
    finally:SLOTS.release()
