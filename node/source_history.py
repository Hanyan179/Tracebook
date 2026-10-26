"""Device-only reading of exact saved source versions. Never used for model input.

Filesystem discovery is bounded and resumable. A saved name is only a locator:
every read checks the whole file digest and its identity before returning text.
"""
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import secrets
import stat
import time

MAX_BYTES = 8 * 1024 * 1024
BATCH = 256
MAX_SCANS = 4
SCAN_TTL = 120
FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
CHANGED = '历史文件或资料夹已变化，请重新读取。'


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class SourceHistory:
    def __init__(self, store):
        self.s = store
        self.scans = OrderedDict()

    def error(self, code, message):
        from store import StoreError
        raise StoreError(code, message)

    def close(self):
        for key in list(self.scans): self.drop(key)

    def drop(self, key):
        value = self.scans.pop(key, None)
        if value:
            value['iterator'].close()
            os.close(value['fd'])

    @contextmanager
    def directory(self, relative):
        self.s.require_output()
        parts = PurePosixPath(relative).parts
        if relative.startswith('/') or any(p in ('.', '..') for p in parts):
            self.error(400, '历史来源位置不正确。')
        fd = os.open(self.s.output, FLAGS)
        try:
            root = os.fstat(fd)
            bound = self.s.output_root.identity
            if (root.st_dev, root.st_ino) != (bound['device'], bound['inode']):
                self.error(409, CHANGED)
            for part in parts:
                child = os.open(part, FLAGS, dir_fd=fd)
                os.close(fd); fd = child
            yield fd
            self.s.require_output()
            # Re-open the path: a retained descriptor alone can refer to a moved directory.
            check = os.open(self.s.output, FLAGS)
            try:
                for part in parts:
                    child = os.open(part, FLAGS, dir_fd=check)
                    os.close(check); check = child
                a, b = os.fstat(fd), os.fstat(check)
                if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino): self.error(409, CHANGED)
            finally: os.close(check)
        finally: os.close(fd)

    def read_file(self, directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
                self.error(413, '历史文件过大或不是普通文字文件，未读取。')
            raw = stream.read(MAX_BYTES + 1)
            if (len(raw) != before.st_size or identity(os.fstat(stream.fileno())) != identity(before)
                    or identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != identity(before)):
                self.error(409, CHANGED)
            return raw.decode('utf-8')

    def page(self, row, body, state, offset, limit, locator=None):
        source = self.s.knowledge.source_data(row, body)
        return {'state': state, 'locator': locator, 'source': {**source,
                'text': body[offset:offset + limit], 'offset': offset, 'totalCharacters': len(body),
                'nextOffset': offset + limit if offset + limit < len(body) else None}}

    def read(self, kind, identifier, version, parameters):
        from knowledge import SCOPES
        from store import StoreError
        if not re.fullmatch(r'[a-f0-9]{64}', version): self.error(400, '来源版本不正确。')
        try: offset, limit = int(parameters.get('offset', 0)), int(parameters.get('limit', 8000))
        except (TypeError, ValueError): self.error(400, '读取位置不正确。')
        if offset < 0 or not 1 <= limit <= 16000: self.error(400, '每次最多读取 16000 个字符。')
        with self.s.lock:
            if parameters.get('nodeID') != self.s.setting('nodeID'):
                self.error(409, '资料库已变化，请重新连接后读取。')
            for key, scan in list(self.scans.items()):
                if time.monotonic() - scan['used'] > SCAN_TTL: self.drop(key)
            row = self.s.knowledge.source_row(kind, identifier, list(SCOPES))
            if kind == 'memory': return self.memory(row, version, parameters, offset, limit)
            if kind in ('input', 'document'):
                relative = json.loads(row['data'])['path']
                if kind == 'document':
                    valid = relative == 'documents/' + row['id'] + '.md'
                else:
                    item = self.s.db.execute('SELECT * FROM local_inputs WHERE id=?', (row['id'],)).fetchone()
                    provider = self.s.db.execute('SELECT provider FROM local_sources WHERE id=?', (item['source_id'],)).fetchone() if item else None
                    valid = provider and relative == self.s.local_sources.document_path(item, provider[0])
                if not valid: self.error(400, '历史来源位置不正确。')
            else:
                relative = {'recording': 'transcripts', 'note': 'notes', 'daily': 'daily', 'analysis': 'analysis'}[kind] + '/' + row['id'] + '.md'
            try:
                with self.directory(str(PurePosixPath(relative).parent)) as fd:
                    body = self.read_file(fd, PurePosixPath(relative).name)
                if hashlib.sha256(body.encode()).hexdigest() == version:
                    return self.page(row, body, 'current', offset, limit)
            except FileNotFoundError: pass  # A missing current file does not erase retained history.
            except (OSError, UnicodeError): self.error(409, CHANGED)
            directory = '.history/' + relative
            locator, cursor = parameters.get('locator'), parameters.get('cursor')
            binding = (str(self.s.output), kind, row['id'], version)
            try:
                with self.directory(directory) as fd:
                    if locator:
                        if not re.fullmatch(r'[0-9]{8}T[0-9]{12}-[a-f0-9]{10}\.md', locator):
                            self.error(400, '历史文件编号不正确。')
                        body = self.read_file(fd, locator)
                        if hashlib.sha256(body.encode()).hexdigest() != version: self.error(409, CHANGED)
                        return self.page(row, body, 'historical', offset, limit, locator)
                    if cursor:
                        scan = self.scans.get(cursor)
                        if not scan or scan['binding'] != binding: self.error(409, '历史查找已失效，请重新读取。')
                        if identity(os.fstat(fd)) != scan['identity']:
                            self.drop(cursor); self.error(409, CHANGED)
                    else:
                        while len(self.scans) >= MAX_SCANS: self.drop(next(iter(self.scans)))
                        cursor = secrets.token_urlsafe(24)
                        held = os.dup(fd)
                        try: iterator = os.scandir(held)
                        except OSError:
                            os.close(held)
                            raise
                        scan = {'binding': binding, 'fd': held, 'iterator': iterator,
                                'identity': identity(os.fstat(fd)), 'checked': 0, 'skipped': 0}
                        self.scans[cursor] = scan
                    scan['used'] = time.monotonic(); self.scans.move_to_end(cursor)
                    files_read = 0
                    for _ in range(BATCH):
                        entry = next(scan['iterator'], None)
                        if entry is None:
                            if identity(os.fstat(fd)) != scan['identity']: self.error(409, CHANGED)
                            result = {'state': 'incomplete' if scan['skipped'] else 'missing',
                                      'checked': scan['checked'], 'skipped': scan['skipped']}
                            self.drop(cursor); return result
                        scan['checked'] += 1
                        match = re.fullmatch(r'[0-9]{8}T[0-9]{12}-([a-f0-9]{10})\.md', entry.name)
                        if not match:
                            scan['skipped'] += 1; continue
                        if match[1] != version[:10]: continue
                        files_read += 1
                        try: body = self.read_file(fd, entry.name)
                        except (OSError, UnicodeError, StoreError):
                            scan['skipped'] += 1; body = None
                        if body is not None and hashlib.sha256(body.encode()).hexdigest() == version:
                            if identity(os.fstat(fd)) != scan['identity']: self.error(409, CHANGED)
                            self.drop(cursor)
                            return self.page(row, body, 'historical', offset, limit, entry.name)
                        if body is not None: scan['skipped'] += 1
                        if files_read >= 4: break
                    if identity(os.fstat(fd)) != scan['identity']: self.error(409, CHANGED)
                    return {'state': 'searching', 'nextCursor': cursor, 'checked': scan['checked'], 'skipped': scan['skipped']}
            except FileNotFoundError:
                if cursor and self.scans.get(cursor, {}).get('binding') == binding: self.drop(cursor)
                if locator or cursor: self.error(409, CHANGED)
                return {'state': 'missing', 'checked': 0, 'skipped': 0}
            except (OSError, UnicodeError):
                if cursor and self.scans.get(cursor, {}).get('binding') == binding: self.drop(cursor)
                self.error(409, CHANGED)
            except StoreError:
                if cursor and self.scans.get(cursor, {}).get('binding') == binding: self.drop(cursor)
                raise

    def memory(self, row, version, parameters, offset, limit):
        body = row['body']
        if hashlib.sha256(body.encode()).hexdigest() == version:
            return self.page(row, body, 'current', offset, limit)
        locator = parameters.get('locator')
        try:
            revision = int(locator.removeprefix('revision-')) if locator else None
            before = int(parameters.get('beforeRevision', 2**63 - 1))
            if not 1 <= before <= 2**63 - 1 or (revision is not None and not 1 <= revision <= 2**63 - 1): raise ValueError()
        except (ValueError, TypeError): self.error(400, '来源版本不正确。')
        rows = self.s.db.execute('SELECT revision,document FROM memory_versions WHERE id=? AND revision<?' +
            (' AND revision=?' if revision else '') + ' ORDER BY revision DESC LIMIT ?',
            (row['id'], before, revision, BATCH) if revision else (row['id'], before, BATCH)).fetchall()
        for item in rows:
            snapshot = json.loads(item['document']); body = snapshot['text']
            if snapshot['state'] == 'confirmed' and hashlib.sha256(body.encode()).hexdigest() == version:
                return self.page(row, body, 'historical', offset, limit, 'revision-' + str(item['revision']))
        if locator: self.error(409, CHANGED)
        return {'state': 'searching' if len(rows) == BATCH else 'missing',
                'beforeRevision': rows[-1]['revision'] if len(rows) == BATCH else None}
