"""One-file PDF text worker. No OCR, external helpers, actions, or network requests."""
import json
import logging
import os
from pathlib import Path
import sys
import threading

MAX_TEXT = 2 * 1024 * 1024
MAX_PAGES = 200


def extract(file):
    for root in (Path(__file__).parent / 'PythonRuntime', Path(__file__).resolve().parents[1] / 'DerivedData/PythonRuntime'):
        if (root / 'common/pypdf').is_dir():
            sys.path.insert(0, str(root / 'common'));break
    try:
        from pypdf import PdfReader, apply_configuration
    except ImportError:
        return {'error': 'parser_unavailable'}
    class Warnings(logging.Handler):
        count = 0
        def emit(self, _record):self.count += 1
    warning = Warnings();logger = logging.getLogger('pypdf')
    logger.addHandler(warning);logger.propagate = False
    try:
        # Explicit stream/tree budgets complement the parent's wall time and sampled RSS.
        # No image decoding or external jbig2 helper is permitted.
        with apply_configuration(maximum_declared_stream_length=8_000_000,
                array_based_stream_maximum_output_length=8_000_000,
                zlib_maximum_output_length=8_000_000, lzw_maximum_output_length=8_000_000,
                run_length_maximum_output_length=8_000_000, brotli_maximum_output_length=8_000_000,
                stream_decoding_work_maximum_length=16_000_000, stream_parsing_work_maximum_count=200_000,
                page_tree_maximum_entries=5000, page_tree_maximum_depth=30,
                array_maximum_element_count=20_000, dictionary_maximum_element_count=20_000,
                xform_maximum_invocations_per_extraction=500, jbig2dec_binary=None):
            reader = PdfReader(file, strict=True, root_object_recovery_limit=1000)
            if reader.is_encrypted:return {'error': 'encrypted'}
            count = len(reader.pages)
            if not 1 <= count <= MAX_PAGES:return {'error': 'page_limit'}
            pages = [];total = 0
            for number, page in enumerate(reader.pages, 1):
                try:
                    text = page.extract_text() or ''
                    if '\0' in text:raise ValueError()
                    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
                    size = len(text.encode('utf-8'))
                    total += size
                    if total > MAX_TEXT:return {'error': 'text_limit'}
                    pages.append({'number': number, 'status': 'text' if text else 'empty', 'text': text})
                except (MemoryError, RecursionError):return {'error': 'parser_limit'}
                except Exception:
                    pages.append({'number': number, 'status': 'unreadable', 'text': ''})
            return {'pages': pages, 'pageCount': count, 'parserWarnings': warning.count > 0, 'parser': 'pypdf-6.21.2'}
    except (MemoryError, RecursionError):return {'error': 'parser_limit'}
    except Exception:return {'error': 'invalid_pdf'}
    finally:logger.removeHandler(warning)


def main():
    # A pipe owned by the parent closes even if the node is killed. Do not leave
    # a parser running after its supervising process disappears.
    def parent_closed():
        try:os.read(0, 1)
        finally:os._exit(3)
    threading.Thread(target=parent_closed, daemon=True).start()
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        resource.setrlimit(resource.RLIMIT_FSIZE, (4 * MAX_TEXT, 4 * MAX_TEXT))
        # Not available on all macOS Python runtimes. The parent still monitors RSS.
        try:resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        except (ValueError, OSError):pass
    except (ImportError, ValueError, OSError):pass
    value = extract(sys.argv[1])
    Path(sys.argv[2]).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':main()
