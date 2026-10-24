"""Bounded source readers. Compressed history never expands into temporary files."""
import collections
import os
from pathlib import Path
import platform
import sys

if sys.platform == 'darwin':
    location = 'macos-cp%d%d-' % sys.version_info[:2] + platform.machine()
    roots = [Path(__file__).parent / 'PythonRuntime', Path(__file__).resolve().parents[1] / 'DerivedData' / 'PythonRuntime']
    for root in roots:
        if (root / location).is_dir():
            if (root / 'common').is_dir():
                sys.path.insert(0, str(root / 'common'))
            sys.path.insert(0, str(root / location))
            break
try:
    import pyzstd
except ImportError:
    pyzstd = None

SOURCE_ERRORS = (OSError, EOFError) + ((pyzstd.ZstdError,) if pyzstd else ())


def logical_size(path, info):
    if not str(path).endswith('.jsonl.zst'):
        return info.st_size
    if pyzstd is None:
        raise OSError('compressed reader unavailable')
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as raw:
            value = pyzstd.get_frame_info(raw.read(18)).decompressed_size
        return value
    except Exception as error:
        raise OSError('compressed header unreadable') from error


class SourceReaders:
    """Retain a small number of streaming decoders between persisted batches.

    Codex's compressed files are single-frame streams. Re-opening on every batch
    would repeatedly decode the entire prefix; keeping the reader avoids that.
    A process restart safely replays the prefix once from the persisted offset.
    """
    def __init__(self):
        self.readers = collections.OrderedDict()

    def open(self, path, info, offset):
        if not str(path).endswith('.jsonl.zst'):
            stream = os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb')
            current = os.fstat(stream.fileno())
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                stream.close()
                raise OSError('source replaced')
            stream.seek(offset)
            return stream, True
        if pyzstd is None:
            raise OSError('compressed reader unavailable')
        key = (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        pair = self.readers.pop(key, None)
        if pair is None:
            raw = os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb')
            current = os.fstat(raw.fileno())
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != key[1:]:
                raw.close()
                raise OSError('source replaced')
            try:
                stream = pyzstd.ZstdFile(raw, 'r', level_or_option={pyzstd.DParameter.windowLogMax: 27})
            except Exception:
                raw.close()
                raise
            pair = (stream, raw)
        self.readers[key] = pair
        while len(self.readers) > 2:
            _, (stream, raw) = self.readers.popitem(last=False)
            stream.close(); raw.close()
        try:
            pair[0].seek(offset)
            return pair[0], False
        except Exception:
            self.discard(path)
            raise

    def discard(self, path):
        for key in list(self.readers):
            if key[0] == str(path):
                stream, raw = self.readers.pop(key)
                stream.close(); raw.close()

    def close(self):
        for stream, raw in self.readers.values():
            stream.close(); raw.close()
        self.readers.clear()
