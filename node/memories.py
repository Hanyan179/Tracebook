"""User-reviewed memories with immutable source excerpts and revision history."""
from __future__ import annotations
import hashlib
import json
import uuid


class Memories:
    KINDS = {'recording', 'note', 'input', 'document'}

    def __init__(self, store):
        self.s, self.db = store, store.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS memory_details(
            id TEXT PRIMARY KEY, revision INTEGER NOT NULL, evidence TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_versions(
            id TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL,
            document TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(id,revision));
        CREATE TABLE IF NOT EXISTS memory_operations(
            id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, candidate_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_relations(
            id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, target_id TEXT NOT NULL,
            kind TEXT NOT NULL, candidate_revision INTEGER NOT NULL,
            target_revision INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS memory_relation_candidate ON memory_relations(candidate_id);
        CREATE INDEX IF NOT EXISTS memory_relation_target ON memory_relations(target_id);
        CREATE TABLE IF NOT EXISTS memory_delivery(
            id INTEGER PRIMARY KEY CHECK(id=1), pending INTEGER NOT NULL,
            output_root TEXT, last_success TEXT, problem TEXT);
        INSERT OR IGNORE INTO memory_delivery(id,pending) VALUES (1,1);
        ''')
        if 'proposal' not in {r[1] for r in self.db.execute('PRAGMA table_info(memory_details)')}:
            with self.db:self.db.execute('ALTER TABLE memory_details ADD COLUMN proposal TEXT')
        if 'last_attempt' not in {r[1] for r in self.db.execute('PRAGMA table_info(memory_delivery)')}:
            with self.db:self.db.execute('ALTER TABLE memory_delivery ADD COLUMN last_attempt TEXT')

    def error(self, status, message):
        from store import StoreError
        raise StoreError(status, message)

    def data(self, row):
        detail = self.db.execute('SELECT * FROM memory_details WHERE id=?', (row['id'],)).fetchone()
        return {**dict(row), 'sources': json.loads(row['sources']),
                'memoryRevision': detail['revision'] if detail else 0,
                'proposal': json.loads(detail['proposal']) if detail and detail['proposal'] else None,
                'evidence': json.loads(detail['evidence']) if detail else []}

    def migrate(self):
        """Old recording links survive; their historic text version is unknown."""
        with self.s.lock, self.db:
            rows = self.db.execute('''SELECT c.* FROM candidates c LEFT JOIN memory_details d
                ON c.id=d.id WHERE d.id IS NULL''').fetchall()
            for row in rows:
                evidence = [{'kind': 'recording', 'id': rid, 'title': '来源录音',
                             'date': '', 'contentVersion': '', 'quote': '', 'quoteStart': 0,
                             'capturedAt': '', 'snapshotState': 'legacy', 'path': 'transcripts/' + rid + '.md'}
                            for rid in json.loads(row['sources'])]
                self.db.execute('INSERT INTO memory_details(id,revision,evidence) VALUES (?,1,?)',
                                (row['id'], json.dumps(evidence, ensure_ascii=False)))
                self.record_version(row['id'], 'migrated', row['updated_at'])
        for row in rows:
            self.s.catalog.index_memory(row['id'])
        if rows:
            self.deliver_pending()

    def source(self, kind, identifier):
        from store import canonical_id
        if not isinstance(kind, str) or kind not in self.KINDS:
            self.error(400, '请选择录音文字、笔记、本地输入或导入资料作为来源。')
        rid = canonical_id(identifier)
        getattr(self.s.catalog, 'index_' + kind)(rid)
        row = self.db.execute('SELECT * FROM library_items WHERE kind=? AND id=?', (kind, rid)).fetchone()
        if not row or row['archived']:
            self.error(404, '来源不存在或已归档，请重新选择。')
        # Read the actual file, not a possibly stale search index.
        path = (json.loads(row['data'])['path'] if kind in ('input', 'document') else
                ('transcripts/' if kind == 'recording' else 'notes/') + rid + '.md')
        if not self.s._path(path).is_file():
            self.error(404, '来源文件暂时不可读取，已保存的依据仍保留。')
        body = self.s.read_document(path)['text']
        return row, body, hashlib.sha256(body.encode()).hexdigest()

    def capture(self, references, legacy_sources=None):
        from store import canonical_id, stamp
        legacy = references is None
        if legacy:
            if not isinstance(legacy_sources, list) or not legacy_sources:
                self.error(400, '请填写记忆建议并选择来源。')
            references = [{'kind': 'recording', 'id': rid} for rid in legacy_sources]
        if not isinstance(references, list) or not 1 <= len(references) <= 16:
            self.error(400, '每条记忆需要 1 至 16 条来源。')
        evidence, seen = [], set()
        for reference in references:
            if not isinstance(reference, dict):
                self.error(400, '来源格式不正确。')
            kind, rid = reference.get('kind'), canonical_id(reference.get('id'))
            row, body, version = self.source(kind, rid)
            if not legacy and reference.get('contentVersion') != version:
                self.error(409, '来源已变化，请重新读取原文后提出建议。')
            quote = body[:4000] if legacy else reference.get('quote')
            if (not isinstance(quote, str) or len(quote) > 4000 or
                    (not legacy and not quote.strip()) or quote not in body):
                self.error(400, '依据必须是原文中的连续片段，最多 4000 个字符。')
            start = reference.get('quoteStart', body.find(quote))
            if (not isinstance(start, int) or isinstance(start, bool) or start < 0 or
                    body[start:start + len(quote)] != quote):
                self.error(400, '依据在原文中的位置不正确。')
            key = (kind, rid, start, quote)
            if key in seen:
                continue
            seen.add(key)
            evidence.append({'kind': kind, 'id': rid, 'title': row['title'], 'date': row['day'],
                             'contentVersion': version, 'quote': quote, 'quoteStart': start,
                             'capturedAt': stamp(), 'snapshotState': 'captured' if quote else 'noText',
                             'path': json.loads(row['data'])['path'] if kind in ('input', 'document') else
                                     ('transcripts/' if kind == 'recording' else 'notes/') + rid + '.md'})
        return evidence

    def operation(self, operation_id, payload):
        from store import canonical_id
        if operation_id is None:
            return None, None, None
        key = canonical_id(operation_id)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        old = self.db.execute('SELECT * FROM memory_operations WHERE id=?', (key,)).fetchone()
        if old and old['fingerprint'] != fingerprint:
            self.error(409, '这次操作的内容已变化，请重新提交。')
        return key, fingerprint, old['candidate_id'] if old else None

    def save_operation(self, key, fingerprint, cid):
        if key:
            self.db.execute('INSERT INTO memory_operations VALUES (?,?,?)', (key, fingerprint, cid))

    def add(self, text, sources=None, evidence=None, request_id=None):
        from store import stamp
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            self.error(400, '请填写记忆建议，最多 10000 个字符。')
        with self.s.lock:
            key, fingerprint, existing = self.operation(request_id, ['create', text, sources, evidence])
            if existing:
                self.s.export_memory()
                return {'id': existing, 'reused': True}
            captured = self.capture(evidence, sources)
            cid, now = str(uuid.uuid4()).upper(), stamp()
            with self.db:
                self.insert(cid, text, captured, now)
                self.save_operation(key, fingerprint, cid)
            self.s.catalog.index_memory(cid)
            self.s.export_memory()
            return {'id': cid, 'reused': False}

    def insert(self, cid, text, captured, now, proposal=None):
        """Caller holds the store lock and owns the transaction; evidence is validated first."""
        recording_ids = sorted({e['id'] for e in captured if e['kind'] == 'recording'})
        self.db.execute('INSERT INTO candidates VALUES (?,?,?,?,?,?)',
                        (cid, text.strip(), json.dumps(recording_ids), 'pending', now, now))
        self.db.execute('INSERT INTO memory_details(id,revision,evidence,proposal) VALUES (?,1,?,?)',
                        (cid, json.dumps(captured, ensure_ascii=False), json.dumps(proposal) if proposal else None))
        self.record_version(cid, 'created', now)

    def record_version(self, cid, action, now):
        row = self.db.execute('SELECT * FROM candidates WHERE id=?', (cid,)).fetchone()
        value = self.data(row)
        self.db.execute('INSERT INTO memory_versions VALUES (?,?,?,?,?)',
                        (cid, value['memoryRevision'], action, json.dumps(value, ensure_ascii=False), now))
        self.mark_delivery_pending()

    def mark_delivery_pending(self):
        """Caller owns the transaction: a committed change must survive a crash before export."""
        self.db.execute('UPDATE memory_delivery SET pending=1,problem=NULL WHERE id=1')

    def delivery_status(self):
        with self.s.lock:
            row = self.db.execute('SELECT * FROM memory_delivery WHERE id=1').fetchone()
            same_root = row['output_root'] == str(self.s.output)
            pending = bool(row['pending']) or not same_root
            names = {'memory.md', 'memory-inbox.md', 'memory-inactive.md', 'memory-rejected.md', 'memory-archive.md'}
            conflicts = [dict(r) for r in self.db.execute(
                "SELECT path,state,generated_path AS generatedPath FROM exports WHERE root=? AND state='conflict' ORDER BY path",
                (str(self.s.output),)) if r['path'] in names or r['path'].startswith('memories/')]
            return {'state': ('failed' if row['problem'] else 'pending') if pending else ('review' if conflicts else 'ready'),
                    'lastAttemptAt': row['last_attempt'],
                    'lastSuccessAt': row['last_success'] if same_root else None,
                    'problem': row['problem'] if pending else None, 'conflicts': conflicts}

    def deliver_pending(self, force=False):
        """Only project saved data; never run a model or repeat a memory decision."""
        with self.s.lock:
            if force or self.delivery_status()['state'] in ('pending', 'failed'):
                try:
                    self.s.export_memory()
                except Exception:
                    pass  # export_memory keeps the durable failure; retry on the next scheduler pass.
            return self.delivery_status()

    def detail(self, cid):
        from store import canonical_id, StoreError
        with self.s.lock:
            cid = canonical_id(cid)
            row = self.db.execute('SELECT * FROM candidates WHERE id=?', (cid,)).fetchone()
            if not row:
                self.error(404, '找不到这条记忆。')
            result = self.data(row)
            links = self.relations(cid, limit=21)
            result['relations'] = links[:20]
            result['relationsTruncated'] = len(links) > 20
            for evidence in result['evidence']:
                try:
                    _, _, version = self.source(evidence['kind'], evidence['id'])
                    evidence['sourceStatus'] = ('legacy' if evidence['snapshotState'] == 'legacy' else
                                                'current' if evidence['contentVersion'] == version else 'changed')
                except (StoreError, OSError, UnicodeError):
                    evidence['sourceStatus'] = 'unavailable'
            return result

    def relations(self, cid, limit=None):
        rows = self.db.execute('''SELECT * FROM memory_relations WHERE candidate_id=? OR target_id=?
            ORDER BY created_at DESC,id DESC''' + (' LIMIT ?' if limit else ''),
            [cid, cid] + ([limit] if limit else [])).fetchall()
        values = []
        for row in rows:
            outgoing = row['candidate_id'] == cid
            other = row['target_id'] if outgoing else row['candidate_id']
            revision = row['target_revision'] if outgoing else row['candidate_revision']
            snapshot = self.db.execute('SELECT document FROM memory_versions WHERE id=? AND revision=?', (other, revision)).fetchone()
            values.append({'id': row['id'], 'kind': row['kind'], 'outgoing': outgoing,
                           'otherID': other, 'otherRevision': revision,
                           'otherText': json.loads(snapshot['document'])['text'] if snapshot else '',
                           'createdAt': row['created_at']})
        return values

    def resolve(self, cid, values):
        """An explicit human decision commits both states, history and receipt together."""
        from store import canonical_id, stamp
        with self.s.lock:
            cid, target = canonical_id(cid), canonical_id(values.get('targetID'))
            kind = values.get('relation')
            if cid == target or not isinstance(kind, str) or kind not in ('duplicate', 'supplement', 'replace'):
                self.error(400, '请选择另一条已确认记忆，并核对处理方式。')
            operation = canonical_id(values.get('requestID'))
            key, fingerprint, existing = self.operation(operation, ['resolve', cid, values])
            if not existing:
                current = []
                for identifier, field, state in [(cid, 'memoryRevision', 'pending'), (target, 'targetRevision', 'confirmed')]:
                    row = self.db.execute('SELECT * FROM candidates WHERE id=?', (identifier,)).fetchone()
                    if not row:self.error(404, '找不到这条记忆。')
                    value = self.data(row)
                    revision = values.get(field)
                    if not isinstance(revision, int) or isinstance(revision, bool) or revision != value['memoryRevision'] or value['state'] != state:
                        self.error(409, '新旧记忆已被修改，请重新读取并核对后再处理。')
                    self.s.catalog.index_memory(identifier)
                    if self.s.catalog.detail('memory', identifier)['organization']['archived']:
                        self.error(409, '记忆已归档，请重新选择。')
                    current.append(value)
                now = stamp()
                with self.db:
                    self.db.execute('INSERT INTO memory_relations VALUES (?,?,?,?,?,?,?)',
                                    (key, cid, target, kind, current[0]['memoryRevision'], current[1]['memoryRevision'], now))
                    self.db.execute('UPDATE candidates SET state=?,updated_at=? WHERE id=?',
                                    ('rejected' if kind == 'duplicate' else 'confirmed', now, cid))
                    self.db.execute('UPDATE memory_details SET revision=revision+1 WHERE id=?', (cid,))
                    self.record_version(cid, {'duplicate': 'duplicate', 'supplement': 'supplemented', 'replace': 'replacement'}[kind], now)
                    if kind == 'replace':
                        self.db.execute("UPDATE candidates SET state='inactive',updated_at=? WHERE id=?", (now, target))
                        self.db.execute('UPDATE memory_details SET revision=revision+1 WHERE id=?', (target,))
                        self.record_version(target, 'superseded', now)
                    self.s.catalog.index_memory(cid, commit=False)
                    self.s.catalog.index_memory(target, commit=False)
                    self.save_operation(key, fingerprint, cid)
            # Retrying after an export/index error replays only these recoverable projections.
            self.s.catalog.index_memory(cid); self.s.catalog.index_memory(target)
            exported = self.s.export_memory()
            return {**self.detail(cid), **exported}

    def history(self, cid, before=None):
        from store import canonical_id
        with self.s.lock:
            cid = canonical_id(cid)
            if not self.db.execute('SELECT 1 FROM candidates WHERE id=?', (cid,)).fetchone():
                self.error(404, '找不到这条记忆。')
            try:
                cursor = int(before) if before is not None else 2**63 - 1
                if cursor < 1 or cursor >= 2**63:
                    raise ValueError()
            except (ValueError, TypeError):
                self.error(400, '历史读取位置不正确。')
            rows = self.db.execute('SELECT * FROM memory_versions WHERE id=? AND revision<? ORDER BY revision DESC LIMIT 41',
                                   (cid, cursor)).fetchall()
            return {'items': [{**json.loads(r['document']), 'action': r['action'], 'changedAt': r['created_at']}
                              for r in rows[:40]], 'nextBefore': rows[39]['revision'] if len(rows) > 40 else None}

    def update(self, cid, values, legacy=False):
        from store import canonical_id, stamp
        with self.s.lock:
            cid = canonical_id(cid)
            row = self.db.execute('SELECT * FROM candidates WHERE id=?', (cid,)).fetchone()
            if not row:
                self.error(404, '找不到这条记忆。')
            key, fingerprint, existing = self.operation(values.get('requestID'), ['update', cid, values])
            if existing:
                self.s.export_memory()
                return self.detail(cid)
            current = self.data(row)
            state, text = values.get('state', row['state']), values.get('text', row['text'])
            if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                self.error(400, '请填写记忆内容，最多 10000 个字符。')
            text = text.strip()
            if not isinstance(state, str):
                self.error(400, '无效的记忆状态变更。')
            if legacy:
                if state not in ('confirmed', 'rejected') or row['state'] not in ('pending', state):
                    self.error(409, '这条建议已处理，请重新读取。')
            else:
                revision = values.get('memoryRevision')
                if not isinstance(revision, int) or isinstance(revision, bool) or revision != current['memoryRevision']:
                    self.error(409, '记忆已被修改，请重新读取后再保存。')
                allowed = {'pending': {'confirmed', 'rejected'}, 'confirmed': {'inactive'},
                           'inactive': {'confirmed'}, 'rejected': {'pending'}}
                if state != row['state'] and state not in allowed.get(row['state'], set()):
                    self.error(400, '无效的记忆状态变更。')
            changed = text != row['text'] or state != row['state']
            if changed:
                action = ('edited' if state == row['state'] else
                          {'confirmed': 'confirmed' if row['state'] == 'pending' else 'restored',
                           'inactive': 'disabled', 'pending': 'reopened', 'rejected': 'rejected'}[state])
                now = stamp()
                with self.db:
                    self.db.execute('UPDATE candidates SET text=?,state=?,updated_at=? WHERE id=?', (text, state, now, cid))
                    self.db.execute('UPDATE memory_details SET revision=revision+1 WHERE id=?', (cid,))
                    self.record_version(cid, action, now)
                    self.save_operation(key, fingerprint, cid)
                self.s.catalog.index_memory(cid)
            elif key:
                with self.db:
                    self.save_operation(key, fingerprint, cid)
            exported = self.s.export_memory()
            return {**self.detail(cid), **exported}

    def export_history(self):
        results = []
        for row in self.db.execute('SELECT id FROM candidates').fetchall():
            versions = [dict(r) for r in self.db.execute('SELECT revision,action,document,created_at FROM memory_versions WHERE id=? ORDER BY revision', (row['id'],))]
            for version in versions:
                version['document'] = json.loads(version['document'])
            text = json.dumps({'schemaVersion': 1, 'id': row['id'], 'versions': versions,
                               'relations': self.relations(row['id'])}, ensure_ascii=False, indent=2) + '\n'
            results.append(self.s.export('memories/' + row['id'] + '.json', text, hashlib.sha256(text.encode()).hexdigest()))
        return results
