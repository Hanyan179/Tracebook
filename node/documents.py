"""Explicit local text-document imports with immutable source revisions."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import uuid
from local_sources import redact

NAMESPACE = uuid.UUID('2132fb96-e68f-4a7b-8bd2-697b876fcb0d')
MAX_REQUEST = 16 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024


class DocumentImports:
    def __init__(self, store):
        self.s = store
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS documents(
                id TEXT PRIMARY KEY,title TEXT NOT NULL,source_path TEXT NOT NULL,format TEXT NOT NULL,
                source_hash TEXT NOT NULL,created_at TEXT NOT NULL,imported_at TEXT NOT NULL,
                previous_id TEXT,redacted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS document_locations(path_hash TEXT PRIMARY KEY,document_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS document_previews(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,
                state TEXT NOT NULL,digest TEXT NOT NULL,report TEXT NOT NULL);
        ''')

    def error(self, status, message):
        from store import StoreError
        raise StoreError(status, message)

    @staticmethod
    def stamp():
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

    def preview(self, values):
        files = values.get('files')
        if not isinstance(files, list) or not 1 <= len(files) <= 100:
            self.error(400, '每次请选择 1 至 100 份文字资料。')
        items, warnings, seen = [], [], set()
        duplicate = 0
        total = 0
        for value in files:
            if not isinstance(value, dict):
                self.error(400, '资料信息格式不正确。')
            name = re.sub(r'[\x00-\x1f]', ' ', Path(str(value.get('name', ''))).name)[:160]
            text = value.get('text')
            suffix = Path(name).suffix.lower()
            reason = value.get('error')
            if suffix not in ('.md', '.markdown', '.txt', '.text'):
                reason = 'unsupported'
            elif not isinstance(text, str) or not text.strip() or '\0' in text:
                reason = reason or 'unreadable'
            elif len(text.encode()) > MAX_FILE:
                reason = 'too_large'
            if reason:
                warnings.append({'name': name, 'reason': reason if reason in ('unsupported', 'too_large') else 'unreadable'})
                continue
            total += len(text.encode())
            if total > 12 * 1024 * 1024:
                self.error(413, '本批资料超过 12 MB，请分批导入。')
            text, hidden = redact(text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n'))
            signature = hashlib.sha256(text.encode()).hexdigest()
            rid = str(uuid.uuid5(NAMESPACE, signature)).upper()
            if rid in seen:
                duplicate += 1
                continue
            seen.add(rid)
            source = value.get('sourcePath')
            source = source if isinstance(source, str) and 0 < len(source) <= 4096 else name
            moment = self.stamp()
            date_basis = 'imported'
            try:
                parsed = dt.datetime.fromisoformat(value.get('modifiedAt', '').replace('Z', '+00:00'))
                if not parsed.tzinfo or not 1970 <= parsed.year <= 2200:
                    raise ValueError()
                moment = parsed.astimezone(dt.timezone.utc).isoformat()
                date_basis = 'modified'
            except (ValueError, TypeError, AttributeError):
                pass
            with self.s.lock:
                existing = bool(self.s.db.execute('SELECT 1 FROM documents WHERE id=?', (rid,)).fetchone())
            items.append({'id': rid, 'title': name, 'text': text, 'source': source, 'format': suffix.lstrip('.'),
                          'signature': signature, 'created_at': moment, 'dateBasis': date_basis,
                          'redacted': hidden, 'existing': existing})
        rid = str(uuid.uuid4()).upper()
        report = {'id': rid, 'selected': len(files), 'accepted': len(items),
                  'added': sum(not i['existing'] for i in items),
                  'duplicates': duplicate+sum(i['existing'] for i in items),
                  'redacted': sum(i['redacted'] for i in items), 'warnings': warnings,
                  'titles': [i['title'] for i in items],
                  'samples': [{'name': i['title'], 'text': i['text'][:400]} for i in items[:3]]}
        data = json.dumps(items, ensure_ascii=False).encode()
        folder = self.s.data/'document-previews';folder.mkdir(exist_ok=True, mode=0o700)
        self.s._atomic(folder/(rid+'.json'), data)
        with self.s.lock, self.s.db:
            self.s.db.execute('INSERT INTO document_previews VALUES (?,?,?,?,?)',
                              (rid, self.stamp(), 'preview', hashlib.sha256(data).hexdigest(), json.dumps(report)))
        return report

    def commit(self, rid):
        from store import canonical_id
        rid = canonical_id(rid)
        with self.s.lock:
            row = self.s.db.execute('SELECT * FROM document_previews WHERE id=?', (rid,)).fetchone()
            if not row:
                self.error(404, '找不到这次资料预览。')
            if row['state'] == 'complete':
                return json.loads(row['report'])
            if (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(row['created_at'])).total_seconds() > 1800:
                self.error(409, '预览已过期，请重新选择资料。')
            file = self.s.data/'document-previews'/(rid+'.json')
            raw = file.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['digest']:
                self.error(409, '资料预览已变化，请重新选择。')
            items = json.loads(raw)
            added = skipped = versions = 0
            for item in items:
                path_hash = hashlib.sha256(item['source'].encode()).hexdigest()
                old = self.s.db.execute('SELECT document_id FROM document_locations WHERE path_hash=?', (path_hash,)).fetchone()
                previous = old[0] if old and old[0] != item['id'] else None
                exists = self.s.db.execute('SELECT 1 FROM documents WHERE id=?', (item['id'],)).fetchone()
                if exists:
                    skipped += 1
                else:
                    basis = '文件修改时间' if item['dateBasis'] == 'modified' else '导入时间（文件时间未知）'
                    body = '# '+item['title']+'\n\n来源：导入资料\n格式：'+item['format']+'\n'+basis+'：'+item['created_at']+'\n'
                    body += '作者：未确认。资料内容不自动成为本人事实或长期记忆。\n'
                    if previous:
                        body += '此前导入版本：['+previous+']('+previous+'.md)\n'
                        versions += 1
                    if item['redacted']:
                        body += '已隐藏识别到的常见凭证；原文件未修改。\n'
                    body += '\n---\n\n'+item['text']+'\n'
                    output = self.s.export('documents/'+item['id']+'.md', body, item['signature'])
                    if output['state'] == 'conflict':
                        self.error(409, '资料目标已有人工内容，已保留，请先核对另存版本。')
                    with self.s.db:
                        self.s.db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)',
                            (item['id'], item['title'], item['source'], item['format'], item['signature'],
                             item['created_at'], self.stamp(), previous, int(item['redacted'])))
                    added += 1
                with self.s.db:
                    self.s.db.execute('INSERT OR REPLACE INTO document_locations VALUES (?,?)', (path_hash, item['id']))
                self.s.catalog.index_document(item['id'])
            report = {'added': added, 'skipped': skipped, 'versions': versions}
            with self.s.db:
                self.s.db.execute("UPDATE document_previews SET state='complete',report=? WHERE id=?", (json.dumps(report), rid))
            file.unlink(missing_ok=True)
            return report

    def cancel(self, rid):
        from store import canonical_id
        rid = canonical_id(rid)
        with self.s.lock, self.s.db:
            row = self.s.db.execute('SELECT state FROM document_previews WHERE id=?', (rid,)).fetchone()
            if row and row[0] == 'preview':
                (self.s.data/'document-previews'/(rid+'.json')).unlink(missing_ok=True)
                self.s.db.execute('DELETE FROM document_previews WHERE id=?', (rid,))
        return {'cancelled': True}

    def listing(self, parameters):
        try:
            offset = max(0, int(parameters.get('offset', 0)))
        except (ValueError, TypeError):
            self.error(400, '读取位置不正确。')
        with self.s.lock:
            rows = self.s.db.execute('SELECT id FROM documents ORDER BY imported_at DESC,id DESC LIMIT 41 OFFSET ?', (offset,)).fetchall()
            items = []
            for row in rows[:40]:
                self.s.catalog.index_document(row['id'])
                source = self.s.db.execute("SELECT * FROM library_items WHERE kind='document' AND id=?", (row['id'],)).fetchone()
                items.append(self.s.knowledge.source_data(source, source['body']))
            return {'items': items, 'count': self.s.db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],
                    'nextOffset': offset+40 if len(rows)>40 else None}
