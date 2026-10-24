"""DOCX imports share the existing bounded local parser process supervisor."""
from pathlib import Path
import sys
import tempfile
import threading
from pdf_documents import MAX_FILE, SLOTS, PDFError, supervise


class DOCXError(Exception):
    pass


def extract_docx(data, scratch, cancel=None, seconds=10):
    if not isinstance(data, bytes) or not data:raise DOCXError('invalid_docx')
    if len(data) > MAX_FILE:raise DOCXError('too_large')
    cancel = cancel or threading.Event()
    if not SLOTS.acquire(blocking=False):raise DOCXError('parser_busy')
    try:
        scratch = Path(scratch);scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='.docx-', dir=scratch) as temporary:
            folder = Path(temporary).resolve();source = folder/'input.docx'
            source.write_bytes(data);source.chmod(0o600)
            command = [sys.executable, '-E', '-s', '-B', str(Path(__file__).with_name('docx_worker.py').resolve()),
                       str(source), str(folder/'result.json')]
            try:value = supervise(command, folder, cancel, seconds)
            except PDFError as error:raise DOCXError('invalid_docx' if str(error) == 'invalid_pdf' else str(error))
        if not isinstance(value, dict):raise DOCXError('invalid_docx')
        if 'error' in value:
            errors = {'too_large', 'encrypted_docx', 'invalid_docx', 'unsupported_docx', 'text_limit', 'parser_limit', 'no_text'}
            raise DOCXError(value['error'] if value['error'] in errors else 'invalid_docx')
        blocks, parts, warnings = value.get('blocks'), value.get('parts'), value.get('warnings')
        if (value.get('format') != 'docx' or not isinstance(blocks, list) or not 1 <= len(blocks) <= 20000
                or not isinstance(parts, list) or not 1 <= len(parts) <= 20000 or not isinstance(warnings, list)):
            raise DOCXError('invalid_docx')
        total = 0
        for index, part in enumerate(parts, 1):
            if (not isinstance(part, dict) or part.get('id') != index
                    or part.get('kind') not in ('body', 'header', 'footer', 'footnote', 'endnote', 'comment')
                    or not isinstance(part.get('number'), int) or part['number'] < 1
                    or not isinstance(part.get('blocks'), int) or part['blocks'] < 0):raise DOCXError('invalid_docx')
        counts = {}
        for index, block in enumerate(blocks, 1):
            if (not isinstance(block, dict) or block.get('number') != index
                    or not isinstance(block.get('part'), int) or not 1 <= block['part'] <= len(parts)
                    or block.get('kind') not in ('paragraph', 'table_cell') or not isinstance(block.get('text'), str)
                    or '\0' in block['text']):raise DOCXError('invalid_docx')
            total += len(block['text'].encode())
            counts[block['part']] = counts.get(block['part'], 0) + 1
            if block['kind'] == 'table_cell' and any(not isinstance(block.get(key), int) or block[key] < 1 for key in ('table', 'row', 'cell')):
                raise DOCXError('invalid_docx')
        if any(counts.get(part['id'], 0) != part['blocks'] for part in parts):raise DOCXError('invalid_docx')
        if any(not isinstance(value.get(key), int) or value[key] < 0 for key in ('paragraphCount', 'tableCount')):
            raise DOCXError('invalid_docx')
        warning_names = {'external_links', 'alternate_content', 'revisions', 'hidden_text', 'fields',
                         'images_objects', 'equations', 'unsupported_content', 'tables', 'unreferenced_notes'}
        if len(warnings) > len(warning_names) or any(not isinstance(w, dict) or w.get('reason') not in warning_names
                or not isinstance(w.get('count'), int) or w['count'] < 1 for w in warnings):raise DOCXError('invalid_docx')
        if total > MAX_FILE:raise DOCXError('text_limit')
        return value
    finally:SLOTS.release()
