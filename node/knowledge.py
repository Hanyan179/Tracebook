"""Personal text, source-backed retrieval, local conversations and scoped readers.

No model calls. Credentials are hashed; query text is never written to access logs.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from zoneinfo import ZoneInfo
from catalog import normalized

SCOPES = {'notes': 'note', 'transcripts': 'recording', 'daily': 'daily', 'confirmedMemory': 'memory', 'analyses': 'analysis', 'localInputs':'input', 'documents':'document'}


class Knowledge:
    def __init__(self, store):
        self.s = store
        self.db = store.db
        self.rate = {}
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS reader_grants (
            id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,scopes TEXT NOT NULL,
            created_at TEXT NOT NULL,expires_at TEXT NOT NULL,revoked_at TEXT,last_used_at TEXT);
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversation_turns (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,conversation_id TEXT NOT NULL,
            request_id TEXT NOT NULL,question TEXT NOT NULL,response TEXT NOT NULL,created_at TEXT NOT NULL,
            UNIQUE(conversation_id,request_id));
        CREATE INDEX IF NOT EXISTS conversation_order ON conversations(updated_at DESC,id DESC);
        CREATE INDEX IF NOT EXISTS turn_order ON conversation_turns(conversation_id,sequence);
        ''')

    def error(self, status, text):
        from store import StoreError
        raise StoreError(status, text)

    @staticmethod
    def stamp():
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

    def note(self, identifier):
        from store import canonical_id
        with self.s.lock:
            row = self.db.execute('SELECT * FROM notes WHERE id=?', (canonical_id(identifier),)).fetchone()
            if not row:
                self.error(404, '找不到这条笔记。')
            document = self.s.read_document('notes/' + row['id'] + '.md')
            etag = hashlib.sha256((row['title'] + '\0' + document['etag']).encode()).hexdigest()
            return {**dict(row), **document, 'etag': etag}

    def save_note(self, values, identifier=None):
        from store import canonical_id
        title, text = values.get('title'), values.get('text')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
            self.error(400, '笔记标题请使用 1 至 160 个字符。')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 200000:
            self.error(400, '笔记正文不能为空，最多 200000 个字符。')
        with self.s.lock:
            now = self.stamp()
            if identifier:
                rid = canonical_id(identifier)
                current = self.note(rid)
                if values.get('etag') != current['etag']:
                    self.error(409, '笔记已变化，请重新打开后编辑。')
                document = self.s.read_document('notes/' + rid + '.md')
                self.s.edit_document('notes/' + rid + '.md', text, document['etag'])
                with self.db:
                    self.db.execute('UPDATE notes SET title=?,updated_at=? WHERE id=?', (title.strip(), now, rid))
            else:
                rid = canonical_id(values.get('id') or str(uuid.uuid4()))
                existing = self.db.execute('SELECT id FROM notes WHERE id=?', (rid,)).fetchone()
                if existing:
                    current = self.note(rid)
                    if current['title'] != title.strip() or current['text'] != text:
                        self.error(409, '这个笔记编号已被使用。')
                    return current
                self.s.edit_document('notes/' + rid + '.md', text, '')
                with self.db:
                    self.db.execute('INSERT INTO notes VALUES (?,?,?,?)', (rid, title.strip(), now, now))
            self.s.catalog.index_note(rid)
            day = self.db.execute("SELECT day FROM library_items WHERE kind='note' AND id=?",(rid,)).fetchone()[0]
            self.s.build_daily(day)
            return self.note(rid)

    def list_grants(self):
        with self.s.lock:
            return [self.grant_data(r) for r in self.db.execute(
                'SELECT * FROM reader_grants ORDER BY (revoked_at IS NULL AND expires_at>?) DESC,created_at DESC,id DESC LIMIT 100', (self.stamp(),))]

    @staticmethod
    def grant_data(row):
        return {key: json.loads(row[key]) if key == 'scopes' else row[key]
                for key in ('id', 'name', 'scopes', 'created_at', 'expires_at', 'revoked_at', 'last_used_at')}

    def create_grant(self, values):
        name, scopes, days = values.get('name'), values.get('scopes'), values.get('days', 7)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            self.error(400, '请填写接入名称。')
        if not isinstance(scopes, list) or not scopes or any(s not in SCOPES for s in scopes if isinstance(s, str)) or any(not isinstance(s, str) for s in scopes):
            self.error(400, '请选择有效的读取范围。')
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 90:
            self.error(400, '授权有效期为 1 至 90 天。')
        with self.s.lock:
            now = dt.datetime.now(dt.timezone.utc)
            count = self.db.execute('SELECT COUNT(*) FROM reader_grants WHERE revoked_at IS NULL AND expires_at>?',
                                    (now.isoformat(),)).fetchone()[0]
            if count >= 25:
                self.error(400, '请先撤销不再使用的接入。')
            token = 'pl_' + secrets.token_urlsafe(32)
            rid = str(uuid.uuid4()).upper()
            with self.db:
                self.db.execute('INSERT INTO reader_grants VALUES (?,?,?,?,?,?,NULL,NULL)',
                                (rid, name.strip(), hashlib.sha256(token.encode()).hexdigest(),
                                 json.dumps(sorted(set(scopes))), now.isoformat(timespec='seconds'),
                                 (now + dt.timedelta(days=days)).isoformat(timespec='seconds')))
            row = self.db.execute('SELECT * FROM reader_grants WHERE id=?', (rid,)).fetchone()
            return {**self.grant_data(row), 'token': token}

    def revoke_grant(self, rid):
        from store import canonical_id
        with self.s.lock:
            with self.db:
                result = self.db.execute('UPDATE reader_grants SET revoked_at=COALESCE(revoked_at,?) WHERE id=?',
                                         (self.stamp(), canonical_id(rid)))
                if result.rowcount != 1:
                    self.error(404, '找不到这个接入。')
            return {'revoked': True}

    def authorize(self, token):
        if not isinstance(token, str) or len(token) > 256:
            self.error(401, '读取凭证无效。')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.s.lock:
            row = self.db.execute('SELECT * FROM reader_grants WHERE token_hash=?', (digest,)).fetchone()
            if not row or not hmac.compare_digest(row['token_hash'], digest) or row['revoked_at']:
                self.error(401, '读取凭证无效或已撤销。')
            if dt.datetime.fromisoformat(row['expires_at']) <= dt.datetime.now(dt.timezone.utc):
                self.error(401, '读取凭证已过期。')
            minute = int(time.monotonic() // 60)
            previous, count = self.rate.get(row['id'], (minute, 0))
            count = count + 1 if previous == minute else 1
            if count > 60:
                self.error(429, '读取过于频繁，请稍后重试。')
            self.rate[row['id']] = (minute, count)
            with self.db:
                self.db.execute('UPDATE reader_grants SET last_used_at=? WHERE id=?', (self.stamp(), row['id']))
            return json.loads(row['scopes'])

    def requested_scopes(self, values, granted):
        scopes = values.get('scopes', list(granted))
        if not isinstance(scopes, list) or not scopes or any(not isinstance(s, str) or s not in SCOPES for s in scopes):
            self.error(400, '读取范围不正确。')
        if not set(scopes).issubset(granted):
            self.error(403, '这个接入没有所请求的读取权限。')
        return sorted(set(scopes))

    def source_row(self, kind, identifier, granted):
        from store import canonical_id
        if kind not in SCOPES.values() or kind not in [SCOPES[s] for s in granted]:
            self.error(403, '这个接入没有此类资料的读取权限。')
        if kind in ('daily','analysis'):
            try:
                if dt.date.fromisoformat(identifier).isoformat() != identifier:
                    raise ValueError()
            except (TypeError, ValueError):
                self.error(400, '日期不正确。')
        else:
            identifier = canonical_id(identifier)
        row = self.db.execute('SELECT * FROM library_items WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        if not row or row['archived'] or (kind == 'memory' and row['state'] != 'confirmed'):
            self.error(404, '这条资料未开放读取。')
        return row

    def source_data(self, row, body):
        title = row['title'] or body[:70].split('\n')[0]
        result = {'id': row['id'], 'kind': row['kind'], 'title': title, 'date': row['day'],
                'state': row['state'], 'textAvailable': bool(body.strip()),
                'contentVersion': hashlib.sha256(body.encode()).hexdigest(),
                'uri': 'library://' + row['kind'] + '/' + row['id']}
        result['evidenceType']={'analysis':'model_analysis','memory':'user_confirmed','input':'local_user_input','document':'imported_document',
                                'daily':'source_collection'}.get(row['kind'],'original_text')
        if row['kind']=='analysis':
            result['analysisFreshness']=self.s.analysis.status(row['id'])['freshness']
        return result

    def read_source(self, kind, identifier, granted, parameters):
        try:
            offset, limit = int(parameters.get('offset', 0)), int(parameters.get('limit', 8000))
        except (ValueError, TypeError):
            self.error(400, '读取位置不正确。')
        if offset < 0 or not 1 <= limit <= 16000:
            self.error(400, '每次最多读取 16000 个字符。')
        with self.s.lock:
            row = self.source_row(kind, identifier, granted)
            if kind == 'memory':
                body = row['body']
            elif kind in ('input', 'document'):
                body = self.s.read_document(json.loads(row['data'])['path'])['text']
            else:
                directory = {'recording': 'transcripts', 'note': 'notes', 'daily': 'daily', 'analysis':'analysis'}[kind]
                body = self.s.read_document(directory + '/' + row['id'] + '.md')['text']
            result = self.source_data(row, body)
            if parameters.get('version') and parameters['version'] != result['contentVersion']:
                self.error(409, '资料已变化，请从头重新读取。')
            return {**result, 'text': body[offset:offset + limit], 'offset': offset,
                    'totalCharacters': len(body), 'nextOffset': offset + limit if offset + limit < len(body) else None}

    def terms(self, query):
        text = normalized(query)
        text = re.sub(r'已经确认的长期记忆|已确认的长期记忆|长期记忆|请帮我|帮我|请问|我想知道|总结一下|查询一下|搜索一下|有哪些|是什么|最近|今天|昨天|本周|上周|资料|记录|关于|什么|一下', ' ', text)
        stops = {'what', 'when', 'where', 'did', 'does', 'about', 'with', 'the', 'and', 'for', 'are', 'was', 'were',
                 'from', 'show', 'find', 'search', 'recent', 'latest', 'today', 'yesterday', 'this', 'last', 'week',
                 'my', 'me', 'we', 'our', 'please', 'notes', 'records', 'summarize', 'confirmed', 'memories', 'capture', 'captured', 'i'}
        terms = []
        for term in re.findall(r'[a-z0-9][a-z0-9_-]{1,39}|[\u3400-\u9fff]{2,}', text):
            if term in stops:
                continue
            terms.append(term)
            if re.fullmatch(r'[\u3400-\u9fff]{4,}', term):
                terms.extend(term[i:i + 2] for i in range(len(term) - 1))
        return list(dict.fromkeys(terms))[:16]

    def date_range(self, values, query):
        today = dt.datetime.now(ZoneInfo(self.s.setting('timeZone'))).date()
        start, end = values.get('dateFrom'), values.get('dateTo')
        lowered = query.lower()
        if not start and not end:
            if '昨天' in query or 'yesterday' in lowered:
                start = end = (today - dt.timedelta(days=1)).isoformat()
            elif '今天' in query or 'today' in lowered:
                start = end = today.isoformat()
            elif '上周' in query or 'last week' in lowered:
                start = (today - dt.timedelta(days=today.weekday() + 7)).isoformat()
                end = (today - dt.timedelta(days=today.weekday() + 1)).isoformat()
            elif '本周' in query or 'this week' in lowered:
                start, end = (today - dt.timedelta(days=today.weekday())).isoformat(), today.isoformat()
        for value in (start, end):
            if value is not None:
                try:
                    if dt.date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                except (ValueError, TypeError):
                    self.error(400, '日期范围不正确。')
        if start and end and start > end:
            self.error(400, '开始日期不能晚于结束日期。')
        return start, end

    def query(self, values, granted=None):
        granted = list(SCOPES) if granted is None else granted
        query = values.get('query', '')
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            self.error(400, '请输入 1 至 500 个字符的问题或关键词。')
        scopes = self.requested_scopes(values, granted)
        limit = values.get('limit', 8)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            self.error(400, '每次最多返回 20 条资料。')
        start, end = self.date_range(values, query)
        terms = self.terms(query)
        kinds = [SCOPES[s] for s in scopes]
        clauses = ["kind IN (" + ','.join('?' for _ in kinds) + ")", "archived=0", "(kind!='memory' OR state='confirmed')"]
        args = list(kinds)
        if start:
            clauses.append('day>=?');args.append(start)
        if end:
            clauses.append('day<=?');args.append(end)
        where = ' AND '.join(clauses)
        score = '+'.join('CASE WHEN instr(search_text,?)>0 THEN ? ELSE 0 END' for _ in terms) or '0'
        score_args = [value for term in terms for value in (term, min(6, max(1, len(term) - 1)))]
        with self.s.lock:
            coverage = self.db.execute('SELECT COUNT(*) FROM library_items WHERE ' + where, args).fetchone()[0]
            rows = self.db.execute('SELECT *,(' + score + ') AS relevance FROM library_items WHERE ' + where
                                   + (' AND (' + ' OR '.join('instr(search_text,?)>0' for _ in terms) + ')' if terms else '')
                                   + ' ORDER BY relevance DESC,'
                                   + ("CASE WHEN kind IN ('daily','analysis') THEN 1 ELSE 0 END," if terms else '')
                                   + 'sort_date DESC,id DESC LIMIT ?',
                                   score_args + args + terms + [limit + 1]).fetchall()
            sources = []
            remaining = 12000
            for row in rows[:limit]:
                body = row['body']
                text = body.strip()
                positions = [normalized(text).find(term) for term in terms if term in normalized(text)]
                position = max(0, min(positions, default=0) - 100)
                if position > 0:
                    position = text.rfind('\n', 0, position) + 1
                excerpt = text[position:position + min(1400, remaining)]
                remaining -= len(excerpt)
                sources.append({**self.source_data(row, body), 'excerpt': excerpt,
                                'excerptTruncated': position > 0 or len(text) > len(excerpt)})
                if remaining <= 0:
                    break
        english = values.get('language') == 'en'
        message = ('Found source material. These are excerpts, not a model-generated answer.' if english else
                   '找到了相关资料。以下为原文片段，尚未生成模型回答。') if sources else (
                   'No matching material found in the selected scope. Try a keyword or a different date range.' if english else
                   '在所选范围内没有找到匹配资料。可以换个关键词或日期范围。')
        return {'mode': 'retrieval', 'generatedAnswer': False, 'message': message, 'sources': sources,
                'coverage': {'scopes': scopes, 'dateFrom': start, 'dateTo': end, 'eligibleItems': coverage,
                             'returned': len(sources), 'hasMore': len(rows) > len(sources), 'indexRefreshSeconds': 60},
                'trust': 'source_data', 'createdAt': self.stamp()}

    def create_conversation(self):
        with self.s.lock:
            rid, now = str(uuid.uuid4()).upper(), self.stamp()
            with self.db:
                self.db.execute('INSERT INTO conversations VALUES (?,?,?,?)', (rid, '', now, now))
            return {'id': rid, 'title': '', 'created_at': now, 'updated_at': now}

    def conversations(self, before=None):
        from store import canonical_id
        with self.s.lock:
            clause, args = '', []
            if before:
                previous=self.db.execute('SELECT updated_at,id FROM conversations WHERE id=?',(canonical_id(before),)).fetchone()
                if not previous:self.error(400, '对话分页位置不正确。')
                clause=' WHERE updated_at<? OR (updated_at=? AND id<?)'
                args=[previous['updated_at'],previous['updated_at'],previous['id']]
            return [dict(row) for row in self.db.execute('SELECT * FROM conversations'+clause+' ORDER BY updated_at DESC,id DESC LIMIT 50',args)]

    def turns(self, cid, before=None):
        from store import canonical_id
        cid = canonical_id(cid)
        with self.s.lock:
            if not self.db.execute('SELECT id FROM conversations WHERE id=?', (cid,)).fetchone():
                self.error(404, '找不到这段对话。')
            args = [cid]
            clause = ''
            if before is not None:
                try:
                    before = int(before)
                    if before < 1:raise ValueError()
                except (ValueError, TypeError):self.error(400, '对话分页位置不正确。')
                clause = ' AND sequence<?';args.append(before)
            rows = self.db.execute('SELECT * FROM conversation_turns WHERE conversation_id=?' + clause
                                   + ' ORDER BY sequence DESC LIMIT 31', args).fetchall()
            shown = rows[:30]
            return {'items': [self.turn_data(row) for row in reversed(shown)],
                    'nextBefore': shown[-1]['sequence'] if len(rows) > 30 else None}

    @staticmethod
    def turn_data(row):
        return {key: json.loads(row[key]) if key == 'response' else row[key]
                for key in ('id', 'sequence', 'question', 'response', 'created_at')}

    def ask(self, cid, values):
        from store import canonical_id
        cid, request = canonical_id(cid), canonical_id(values.get('requestID'))
        with self.s.lock:
            conversation = self.db.execute('SELECT id FROM conversations WHERE id=?', (cid,)).fetchone()
            if not conversation:
                self.error(404, '找不到这段对话。')
            old = self.db.execute('SELECT * FROM conversation_turns WHERE conversation_id=? AND request_id=?', (cid, request)).fetchone()
            if old:
                if old['question'] != values.get('query'):
                    self.error(409, '这个请求编号已被使用。')
                return self.turn_data(old)
            response = self.query(values)
            rid, now = str(uuid.uuid4()).upper(), self.stamp()
            with self.db:
                self.db.execute('INSERT INTO conversation_turns(id,conversation_id,request_id,question,response,created_at) VALUES (?,?,?,?,?,?)',
                                (rid, cid, request, values['query'], json.dumps(response, ensure_ascii=False), now))
                self.db.execute("UPDATE conversations SET updated_at=?,title=CASE WHEN title='' THEN ? ELSE title END WHERE id=?",
                                (now, values['query'][:70], cid))
            return self.turn_data(self.db.execute('SELECT * FROM conversation_turns WHERE id=?', (rid,)).fetchone())
