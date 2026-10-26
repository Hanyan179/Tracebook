"""Explicit, version-checked review of portable memory files; no fact or model writes."""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat


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
        parts = relative.split('/')
        if any(p in ('', '.', '..') for p in parts):self.error(400, '记忆文件位置不正确。')
        self.s.require_output()
        fd = os.open(self.s.output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            root = os.fstat(fd);identity = self.s.output_root.identity
            if (root.st_dev,root.st_ino)!=(identity['device'],identity['inode']):
                self.error(409, '核对的文件位置已变化，请重新读取。')
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd);fd = child
            raw_fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(raw_fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):self.error(409, '核对的文件已移走或不可读取，请重新检查文件交付。')
                raw = stream.read(self.MAX_BYTES + 1)
                if len(raw)>self.MAX_BYTES:self.error(413, '这份文件过大，请在电脑资料夹核对；应用内每个版本最多读取 128 KB。')
                after = os.fstat(stream.fileno());current = os.stat(parts[-1],dir_fd=fd,follow_symlinks=False)
                signature = lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
                parent = os.stat(self.s.output.joinpath(*parts[:-1]),follow_symlinks=False);held = os.fstat(fd)
                self.s.require_output()
                if (signature(before)!=signature(after) or signature(before)!=signature(current)
                        or not stat.S_ISDIR(parent.st_mode) or (parent.st_dev,parent.st_ino)!=(held.st_dev,held.st_ino)):
                    self.error(409, '核对的文件位置已变化，请重新读取。')
        except (FileNotFoundError, NotADirectoryError):
            self.error(409, '核对的文件已移走或不可读取，请重新检查文件交付。')
        except OSError as error:
            import errno
            if error.errno == errno.ELOOP:self.error(400, '文件包含链接，请在电脑资料夹核对，应用不会替换链接指向的内容。')
            raise
        finally:os.close(fd)
        try: text = raw.decode('utf-8')
        except UnicodeError: self.error(400, '这份文件不是可读取的文字，请在电脑资料夹核对。')
        return {'text': text, 'etag': hashlib.sha256(raw).hexdigest(), 'path': relative}

    def pair(self, path, allow_resolved=False):
        self.validate(path)
        delivery = self.db.execute('SELECT pending,output_root FROM memory_delivery WHERE id=1').fetchone()
        row = self.db.execute('SELECT * FROM exports WHERE root=? AND path=?', (str(self.s.output), path)).fetchone()
        decision = self.last_decision(path) if allow_resolved and row and row['state'] in ('current','reviewed','manual') else None
        delivery_pending = bool(delivery['pending']) or delivery['output_root'] != str(self.s.output)
        if delivery_pending and decision is None:
            self.error(409, '记忆文件仍有未完成的写入，请先重新检查并写入。')
        if not row or (decision is None and (row['state'] != 'conflict' or not row['generated_path'])):
            self.error(409, '这个文件的冲突状态已变化，请返回文件状态重新读取。')
        generated_path = decision['generatedPath'] if decision else row['generated_path']
        if not re.fullmatch(re.escape('.generated/' + path + '/') + r'[a-f0-9]{16}\.md', generated_path):
            self.error(409, '另存文件位置不正确，请重新检查文件交付。')
        original, generated = self.read(path), self.read(generated_path)
        if not generated_path.endswith('/' + generated['etag'][:16] + '.md'):
            self.error(409, '另存版本已被修改，不能直接采用。请在电脑资料夹核对并保留两个版本。')
        signature = [self.s.setting('nodeID'),str(self.s.output), path, row['semantic_hash'], original['etag'], generated['etag'], generated_path]
        snapshot = hashlib.sha256(json.dumps(signature).encode()).hexdigest()
        return {'nodeID':self.s.setting('nodeID'), 'path': path, 'snapshot': snapshot,
                'original': original, 'generated': generated, 'maxBytes':self.MAX_BYTES,
                'resolved':decision is not None, 'lastDecision':decision, 'deliveryPending':delivery_pending}, row

    def preview(self, path):
        with self.s.lock:
            pair = self.pair(path,allow_resolved=True)[0]
            pending = self.operation(path,'pending')
            pair['pendingOperation'] = {'requestID':pending['id'],'action':json.loads(pending['document'])['action']} if pending else None
            return pair

    def operation(self, path, state):
        for row in self.db.execute('SELECT * FROM memory_file_operations WHERE root=? AND state=? ORDER BY rowid DESC',(str(self.s.output),state)):
            if json.loads(row['document']).get('path')==path:return row
        return None

    def merged_text(self, path, text):
        try:valid=isinstance(text,str) and len(text.encode('utf-8'))<=self.MAX_BYTES and '\0' not in text
        except UnicodeError:valid=False
        if not valid:self.error(400, '合并文件须为有效文字，最多 128 KB。')
        if path.endswith('.json'):
            try:
                value=json.loads(text,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(value,dict):raise ValueError()
            except (ValueError,RecursionError):self.error(400, '历史文件的合并内容须为有效 JSON 对象；原文件和草稿仍保留。')
        return text

    def resolve(self, body):
        from store import canonical_id
        path, snapshot, action = body.get('path'), body.get('snapshot'), body.get('action')
        self.validate(path)
        if action not in ('keep', 'generated', 'merge') or not isinstance(snapshot, str) or not re.fullmatch(r'[a-f0-9]{64}', snapshot):
            self.error(400, '请选择保留当前文件或采用另存版本，并先读取两个版本。')
        if ('nodeID' in body or action=='merge') and body.get('nodeID')!=self.s.setting('nodeID'):
            self.error(409, '资料库已变化，草稿仍保留。请回到原资料库核对。')
        text = self.merged_text(path,body.get('text')) if action=='merge' else None
        if action!='merge' and body.get('text') is not None:self.error(400, '只有手动合并可以提交新的文字。')
        key = canonical_id(body.get('requestID'))
        # Retain the fingerprint contract for existing keep/adopt receipts.
        fingerprint = hashlib.sha256(json.dumps([path, snapshot, action]+([text] if action=='merge' else [])).encode()).hexdigest()
        with self.s.lock:
            old = self.db.execute('SELECT * FROM memory_file_operations WHERE id=?', (key,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint or old['root'] != str(self.s.output):
                    self.error(409, '这次文件处理的内容或资料夹已变化，请重新核对。')
                if old['state'] == 'blocked': self.error(409, '文件在处理期间发生变化，请重新核对两个版本。')
                if old['state'] == 'done': return self.receipt(old)
            else:
                if self.operation(path,'pending'):self.error(409, '上次文件核对仍未保存完成，请先继续上次处理。')
                pair, row = self.pair(path)
                if pair['snapshot'] != snapshot: self.error(409, '文件在预览后发生变化，请重新核对两个版本。')
                op = {'path': path, 'action': action, 'originalHash': pair['original']['etag'],
                      'generatedHash': pair['generated']['etag'], 'generatedPath': pair['generated']['path'],
                      'semantic': row['semantic_hash'], 'backupPath': '.history/' + path + '/review-' + key + '.md' if action != 'keep' else None}
                if action=='merge':op.update(mergedText=text,replacementHash=hashlib.sha256(text.encode()).hexdigest())
                with self.db:
                    self.db.execute('INSERT INTO memory_file_operations VALUES (?,?,?,?,?)',
                                    (key, fingerprint, str(self.s.output), json.dumps(op), 'pending'))
            return self.complete(key)

    def receipt(self, row):
        op = json.loads(row['document'])
        return {'requestID': row['id'], 'path': op['path'], 'action': op['action'], 'backupPath': op['backupPath'],
                'generatedPath':op['generatedPath'], 'contentVersion':self.replacement_hash(op)}

    def replacement_hash(self,op):
        return op['originalHash'] if op['action']=='keep' else op['replacementHash'] if op['action']=='merge' else op['generatedHash']

    def last_decision(self,path):
        from store import StoreError
        row=self.operation(path,'done')
        if not row:return None
        result=self.receipt(row)
        try:result['matchesCurrent']=self.read(path)['etag']==result['contentVersion']
        except (OSError,StoreError):result['matchesCurrent']=False
        return result

    def retry(self,body):
        from store import canonical_id
        key=canonical_id(body.get('requestID'));path=body.get('path');self.validate(path)
        if body.get('nodeID')!=self.s.setting('nodeID'):self.error(409,'资料库已变化，草稿仍保留。请回到原资料库核对。')
        with self.s.lock:
            row=self.db.execute('SELECT * FROM memory_file_operations WHERE id=?',(key,)).fetchone()
            if not row or row['root']!=str(self.s.output) or json.loads(row['document']).get('path')!=path:
                self.error(409,'找不到当前资料库中的文件核对记录。')
            if row['state']=='done':return self.receipt(row)
            if row['state']!='pending':self.error(409,'文件在处理期间发生变化，请重新核对两个版本。')
            return self.complete(key)

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
            if op['action'] not in ('keep','generated','merge'):self.error(409,'文件核对方式不受支持，请更新应用后重新核对。')
            replacement_hash = self.replacement_hash(op)
            if op['action'] != 'keep':
                backup = self.s._path(op['backupPath'])
                backup_ok = backup.is_file() and self.read(op['backupPath'])['etag'] == op['originalHash']
                already_written = original['etag'] == replacement_hash and backup_ok
                if not already_written:
                    if original['etag'] != op['originalHash'] or (backup.exists() and not backup_ok):
                        self.error(409, '文件在处理期间发生变化，请重新核对两个版本。')
                    if not backup_ok: self.s._atomic(backup, original['text'].encode())
                    # Recheck both files after writing the backup, before replacing the destination.
                    if self.pair(op['path'])[0]['snapshot'] != pair['snapshot']:
                        self.error(409, '文件在处理期间发生变化，请重新核对两个版本。')
                    replacement = self.merged_text(op['path'],op['mergedText']) if op['action']=='merge' else pair['generated']['text']
                    self.s._atomic(self.s._path(op['path']), replacement.encode())
            elif original['etag'] != op['originalHash']:
                self.error(409, '文件在处理期间发生变化，请重新核对两个版本。')
            if self.read(op['path'])['etag']!=replacement_hash:self.error(409,'文件在处理期间发生变化，请重新核对两个版本。')
            self.finish(row, op)
            return self.receipt(row)
        except StoreError as error:
            if 400<=error.status<500:
                with self.db: self.db.execute("UPDATE memory_file_operations SET state='blocked' WHERE id=?", (key,))
            raise

    def finish(self, row, op):
        from store import stamp
        with self.db:
            self.db.execute('UPDATE exports SET last_hash=?,state=?,generated_path=NULL,updated_at=? WHERE root=? AND path=?',
                            (self.replacement_hash(op), 'current' if op['action']=='generated' else 'reviewed', stamp(), row['root'], op['path']))
            self.db.execute("UPDATE memory_file_operations SET state='done' WHERE id=?", (row['id'],))

    def recover(self):
        with self.s.lock:
            for row in self.db.execute("SELECT id FROM memory_file_operations WHERE state='pending'").fetchall():
                try: self.complete(row['id'])
                except Exception: pass  # Keep I/O failures pending; stale versions are blocked for review.

    def pending(self):
        return self.db.execute("SELECT 1 FROM memory_file_operations WHERE root=? AND state='pending' LIMIT 1", (str(self.s.output),)).fetchone() is not None
