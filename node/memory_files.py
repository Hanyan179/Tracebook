"""Explicit, version-checked review of portable memory files; no fact or model writes."""
from __future__ import annotations
import hashlib
import json
import re


class MemoryFiles:
    MAX_BYTES = 128 * 1024
    NAMES = {'memory.md', 'memory-inbox.md', 'memory-inactive.md', 'memory-rejected.md', 'memory-archive.md'}

    def __init__(self, store):
        self.s, self.db = store, store.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS memory_file_operations(
            id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, root TEXT NOT NULL,
            document TEXT NOT NULL, state TEXT NOT NULL)''')
        self.db.commit()

    def error(self, status, text):
        from store import StoreError
        raise StoreError(status, text)

    def validate(self, path):
        if not isinstance(path, str) or not (path in self.NAMES or re.fullmatch(r'memories/[A-F0-9-]{36}\.json', path)):
            self.error(400, '只能核对记忆文字与修订历史文件。')
        if path.startswith('memories/'):
            from store import canonical_id
            if path != 'memories/' + canonical_id(path[9:-5]) + '.json':
                self.error(400, '记忆文件位置不正确。')

    def read(self, relative):
        raw_path = self.s.output / relative
        if any(p.is_symlink() for p in [raw_path] + list(raw_path.parents) if p != self.s.output and self.s.output in p.parents):
            self.error(400, '文件包含链接，请在电脑资料夹核对，应用不会替换链接指向的内容。')
        path = self.s._path(relative)
        if not path.is_file(): self.error(409, '核对的文件已移走或不可读取，请重新检查文件交付。')
        with path.open('rb') as stream: raw = stream.read(self.MAX_BYTES + 1)
        if len(raw) > self.MAX_BYTES:
            self.error(413, '这份文件过大，请在电脑资料夹核对；应用内每个版本最多读取 128 KB。')
        try: text = raw.decode('utf-8')
        except UnicodeError: self.error(400, '这份文件不是可读取的文字，请在电脑资料夹核对。')
        return {'text': text, 'etag': hashlib.sha256(raw).hexdigest(), 'path': relative}

    def pair(self, path):
        self.validate(path)
        delivery = self.db.execute('SELECT pending,output_root FROM memory_delivery WHERE id=1').fetchone()
        if delivery['pending'] or delivery['output_root'] != str(self.s.output):
            self.error(409, '记忆文件仍有未完成的写入，请先重新检查并写入。')
        row = self.db.execute('SELECT * FROM exports WHERE root=? AND path=?', (str(self.s.output), path)).fetchone()
        if not row or row['state'] != 'conflict' or not row['generated_path']:
            self.error(409, '这个文件的冲突状态已变化，请返回文件状态重新读取。')
        generated_path = row['generated_path']
        if not re.fullmatch(re.escape('.generated/' + path + '/') + r'[a-f0-9]{16}\.md', generated_path):
            self.error(409, '另存文件位置不正确，请重新检查文件交付。')
        original, generated = self.read(path), self.read(generated_path)
        if not generated_path.endswith('/' + generated['etag'][:16] + '.md'):
            self.error(409, '另存版本已被修改，不能直接采用。请在电脑资料夹核对并保留两个版本。')
        signature = [str(self.s.output), path, row['semantic_hash'], original['etag'], generated['etag'], generated_path]
        snapshot = hashlib.sha256(json.dumps(signature).encode()).hexdigest()
        return {'path': path, 'snapshot': snapshot, 'original': original, 'generated': generated}, row

    def preview(self, path):
        with self.s.lock: return self.pair(path)[0]

    def resolve(self, body):
        from store import canonical_id
        path, snapshot, action = body.get('path'), body.get('snapshot'), body.get('action')
        self.validate(path)
        if action not in ('keep', 'generated') or not isinstance(snapshot, str) or not re.fullmatch(r'[a-f0-9]{64}', snapshot):
            self.error(400, '请选择保留当前文件或采用另存版本，并先读取两个版本。')
        key = canonical_id(body.get('requestID'))
        fingerprint = hashlib.sha256(json.dumps([path, snapshot, action]).encode()).hexdigest()
        with self.s.lock:
            old = self.db.execute('SELECT * FROM memory_file_operations WHERE id=?', (key,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint or old['root'] != str(self.s.output):
                    self.error(409, '这次文件处理的内容或资料夹已变化，请重新核对。')
                if old['state'] == 'blocked': self.error(409, '文件在处理期间发生变化，请重新核对两个版本。')
                if old['state'] == 'done': return self.receipt(old)
            else:
                pair, row = self.pair(path)
                if pair['snapshot'] != snapshot: self.error(409, '文件在预览后发生变化，请重新核对两个版本。')
                op = {'path': path, 'action': action, 'originalHash': pair['original']['etag'],
                      'generatedHash': pair['generated']['etag'], 'generatedPath': pair['generated']['path'],
                      'semantic': row['semantic_hash'], 'backupPath': '.history/' + path + '/review-' + key + '.md' if action == 'generated' else None}
                with self.db:
                    self.db.execute('INSERT INTO memory_file_operations VALUES (?,?,?,?,?)',
                                    (key, fingerprint, str(self.s.output), json.dumps(op), 'pending'))
            return self.complete(key)

    def receipt(self, row):
        op = json.loads(row['document'])
        return {'requestID': row['id'], 'path': op['path'], 'action': op['action'], 'backupPath': op['backupPath']}

    def complete(self, key):
        from store import StoreError
        row = self.db.execute('SELECT * FROM memory_file_operations WHERE id=?', (key,)).fetchone()
        op = json.loads(row['document'])
        try:
            if row['root'] != str(self.s.output): self.error(409, '资料夹已变化，请重新核对。')
            pair, export = self.pair(op['path'])
            if (export['semantic_hash'] != op['semantic'] or pair['generated']['path'] != op['generatedPath']
                    or pair['generated']['etag'] != op['generatedHash']):
                self.error(409, '文件在处理期间发生变化，请重新核对两个版本。')
            original = pair['original']
            if op['action'] == 'generated':
                backup = self.s._path(op['backupPath'])
                backup_ok = backup.is_file() and self.read(op['backupPath'])['etag'] == op['originalHash']
                already_written = original['etag'] == op['generatedHash'] and backup_ok
                if not already_written:
                    if original['etag'] != op['originalHash'] or (backup.exists() and not backup_ok):
                        self.error(409, '文件在处理期间发生变化，请重新核对两个版本。')
                    if not backup_ok: self.s._atomic(backup, original['text'].encode())
                    # Recheck both files after writing the backup, before replacing the destination.
                    if self.pair(op['path'])[0]['snapshot'] != pair['snapshot']:
                        self.error(409, '文件在处理期间发生变化，请重新核对两个版本。')
                    self.s._atomic(self.s._path(op['path']), pair['generated']['text'].encode())
            elif original['etag'] != op['originalHash']:
                self.error(409, '文件在处理期间发生变化，请重新核对两个版本。')
            self.finish(row, op)
            return self.receipt(row)
        except StoreError:
            with self.db: self.db.execute("UPDATE memory_file_operations SET state='blocked' WHERE id=?", (key,))
            raise

    def finish(self, row, op):
        from store import stamp
        with self.db:
            self.db.execute('UPDATE exports SET last_hash=?,state=?,generated_path=NULL,updated_at=? WHERE root=? AND path=?',
                            (op['originalHash'] if op['action']=='keep' else op['generatedHash'],
                             'reviewed' if op['action']=='keep' else 'current', stamp(), row['root'], op['path']))
            self.db.execute("UPDATE memory_file_operations SET state='done' WHERE id=?", (row['id'],))

    def recover(self):
        with self.s.lock:
            for row in self.db.execute("SELECT id FROM memory_file_operations WHERE state='pending'").fetchall():
                try: self.complete(row['id'])
                except Exception: pass  # Keep I/O failures pending; stale versions are blocked for review.

    def pending(self):
        return self.db.execute("SELECT 1 FROM memory_file_operations WHERE root=? AND state='pending' LIMIT 1", (str(self.s.output),)).fetchone() is not None
