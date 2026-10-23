"""Read-only local assistant input collection with bounded, resumable JSONL scans."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid
from zoneinfo import ZoneInfo
from compressed_io import SourceReaders, SOURCE_ERRORS, logical_size, pyzstd

NAMESPACE = uuid.UUID('ba66e4b3-5f85-482b-9cf5-649ff5abe7a5')
SCAN_BYTES = 32 * 1024 * 1024
LINE_BYTES = 2 * 1024 * 1024
MAX_FILES = 4000
MAX_INPUT = 160000
PROVIDERS = {'codex': 'Codex', 'claude': 'Claude Code'}
CONTEXT_PREFIXES = ('<environment_context>', '<external_codex_apps', '<INSTRUCTIONS>',
                    '# AGENTS.md instructions', '<user_instructions>', '<permissions instructions>',
                    '<skills_instructions>', '<turn_aborted>', '<system-reminder>', '<local-command',
                    '<recommended_plugins>', '## Referenced ChatGPT conversation:',
                    '<app-context>', '<codex_apps_client_time_context>', '<image_resize_notice>',
                    '<subagent_notification>', '<collaboration_mode>', '<tools>')


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def identifier(value):
    return str(uuid.uuid5(NAMESPACE, value)).upper()


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()

def session_identity(value):
    try:
        return str(uuid.UUID(str(value))).upper()
    except (ValueError, TypeError, AttributeError):
        return sha(str(value))[:32]

def file_marker(path, offset):
    length = min(offset, 4096)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        head = stream.read(length)
        stream.seek(max(0, offset-length))
        tail = stream.read(length)
    return hashlib.sha256(head+b'\0'+tail).hexdigest()

def redact(text):
    """Known credential patterns only; never a guarantee that prose is non-sensitive."""
    patterns = [
        r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----',
        r'\bsk-(?:ant-|proj-|svcacct-)?[A-Za-z0-9_-]{12,}',
        r'\b(?:ghp_|github_pat_|glpat-)[A-Za-z0-9_-]{16,}',
        r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}',
        r'(?i)(?:Bearer\s+)[A-Za-z0-9._~+/-]{8,}=*',
        r'(?i)(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password|密码|密钥)\s*["\x27]?\s*[:=：]\s*["\x27]?[^\s"\x27,;}{]{4,}',
        r'(?i)https?://[^/\s:@]+:[^/\s@]+@[^/\s]+',
    ]
    result = text
    for pattern in patterns:
        result = re.sub(pattern, '[凭证已隐藏]', result)
    return result, result != text


class LocalSources:
    def __init__(self, store):
        self.s = store
        self.lock = threading.Lock()
        self.readers = SourceReaders()
        self.worker_lock = threading.Lock()
        self.stopping = threading.Event()
        self.worker = None
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS local_sources(
                id TEXT PRIMARY KEY,provider TEXT NOT NULL,directory TEXT NOT NULL,
                automatic INTEGER NOT NULL DEFAULT 0,last_scan TEXT,problem TEXT,summary TEXT NOT NULL DEFAULT '{}',
                config TEXT NOT NULL DEFAULT '{}',revision INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS local_source_files(
                source_id TEXT NOT NULL,path TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(source_id,path));
            CREATE TABLE IF NOT EXISTS local_inputs(
                id TEXT PRIMARY KEY,source_id TEXT NOT NULL,session_id TEXT NOT NULL,message_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,day TEXT NOT NULL,text TEXT NOT NULL,source_path TEXT NOT NULL,
                format TEXT NOT NULL,signature TEXT NOT NULL,redacted INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS local_input_source_day ON local_inputs(source_id,day,timestamp,id);
            CREATE INDEX IF NOT EXISTS local_input_mirror ON local_inputs(source_id,session_id,signature);
            CREATE INDEX IF NOT EXISTS local_input_time ON local_inputs(timestamp DESC,id DESC);
            CREATE TABLE IF NOT EXISTS local_source_previews(
                id TEXT PRIMARY KEY,created_at TEXT NOT NULL,state TEXT NOT NULL,report TEXT);
            CREATE TABLE IF NOT EXISTS local_source_jobs(
                source_id TEXT PRIMARY KEY,state TEXT NOT NULL,started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,batches INTEGER NOT NULL DEFAULT 0,problem TEXT);
        ''')
        with store.db:
            store.db.execute("UPDATE local_source_jobs SET state='queued' WHERE state='running'")
            store.db.execute("UPDATE local_source_jobs SET state='paused' WHERE state='pausing'")

    def close(self):
        self.stopping.set()
        worker = self.worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=5)
            if worker.is_alive():
                return False
        self.readers.close()
        return True

    def error(self, status, message):
        from store import StoreError
        raise StoreError(status, message)

    def validate_root(self, provider, directory):
        if provider not in PROVIDERS or not isinstance(directory, str):
            self.error(400, '请选择支持的本地工具目录。')
        path = Path(directory).expanduser()
        if path.is_symlink():
            self.error(400, '请选择真实目录，不能使用符号链接。')
        path = path.resolve()
        if not path.is_dir() or path in (Path('/'), Path.home()):
            self.error(400, '请选择工具自己的资料目录。')
        marker = ['sessions', 'archived_sessions'] if provider == 'codex' else ['projects']
        if not any((path/name).is_dir() and not (path/name).is_symlink() for name in marker):
            self.error(400, '目录中没有可识别的会话资料。')
        return path

    def discover(self):
        roots = [('codex', os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))),
                 ('claude', os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home()/'.claude')))]
        return [{'provider': p, 'directory': str(Path(root).expanduser()),
                 'available': Path(root).is_dir() and not Path(root).is_symlink()} for p, root in roots]

    def config_summary(self, provider, root):
        name = 'config.toml' if provider == 'codex' else 'settings.json'
        path = root/name
        result = {'file': name, 'present': path.is_file() and not path.is_symlink()}
        if not result['present'] or path.stat().st_size > 1024*1024:
            return result
        try:
            raw = path.read_text()
            if provider == 'claude':
                parsed = json.loads(raw)
                model = parsed.get('model') if isinstance(parsed, dict) else None
            else:
                # Read only a top-level model string; never copy provider, env, command or auth tables.
                top = re.split(r'(?m)^\s*\[', raw, maxsplit=1)[0]
                match = re.search(r'(?m)^\s*model\s*=\s*["\x27]([A-Za-z0-9._:/+-]{1,120})["\x27]\s*(?:#.*)?$', top)
                model = match.group(1) if match else None
            if isinstance(model, str) and re.fullmatch(r'[A-Za-z0-9._:/+-]{1,120}', model) and not redact(model)[1]:
                result['model'] = model
        except (ValueError, OSError, UnicodeError):
            result['unreadable'] = True
        return result

    def files(self, provider, root):
        found = []
        names = ['sessions', 'archived_sessions'] if provider == 'codex' else ['projects']
        limited = False
        for name in names:
            parent = root/name
            if not parent.is_dir() or parent.is_symlink():
                continue
            for current, directories, files in os.walk(parent, followlinks=False):
                directories[:] = sorted(d for d in directories
                                        if not (Path(current)/d).is_symlink() and d not in ('subagents', 'tool-results'))
                for filename in sorted(files):
                    if not filename.endswith(('.jsonl', '.jsonl.zst')):
                        continue
                    path = Path(current)/filename
                    try:
                        info = path.lstat()
                        if not stat.S_ISREG(info.st_mode) or root not in path.resolve().parents:
                            continue
                    except OSError:
                        continue
                    found.append((path.relative_to(root).as_posix(), info))
                    # MAX_FILES bounds a processing batch, not the inventory.
                    # Returning the first N names here permanently hid later files.
        return sorted(found, key=lambda item: item[1].st_mtime_ns, reverse=True), limited

    def event(self, provider, record, state, relative, source_id):
        if not isinstance(record, dict):
            return None
        payload = record.get('payload') if isinstance(record.get('payload'), dict) else {}
        if provider == 'codex' and record.get('type') == 'session_meta':
            state['session'] = session_identity(payload.get('id') or payload.get('session_id') or state['session'])
            return None
        moment = record.get('timestamp')
        message_id = None
        text = ''
        form = ''
        if provider == 'codex':
            if record.get('type') == 'event_msg' and payload.get('type') == 'user_message':
                text = payload.get('message', '')
                form = 'codex_event'
                message_id = payload.get('id')
            elif record.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user':
                metadata = record.get('metadata') or {}
                if isinstance(metadata, dict) and metadata.get('client_authored') is False:
                    return None
                content = payload.get('content')
                if isinstance(content, list):
                    state['context'] = state.get('context', 0)+sum(
                        isinstance(x,dict) and x.get('type')=='input_text' and isinstance(x.get('text'),str)
                        and x['text'].lstrip().startswith(CONTEXT_PREFIXES) for x in content)
                    text = '\n'.join(x['text'] for x in content if isinstance(x, dict) and
                                     x.get('type') == 'input_text' and isinstance(x.get('text'), str) and
                                     not x['text'].lstrip().startswith(CONTEXT_PREFIXES))
                message_id = payload.get('id')
                retained = metadata.get('retained_source', {}) if isinstance(metadata, dict) else {}
                if isinstance(retained, dict) and isinstance(retained.get('id'), dict):
                    message_id = retained['id'].get('message_id') or message_id
                form = 'codex_response'
            else:
                return None
        else:
            if record.get('type') != 'user' or record.get('isSidechain') or record.get('isMeta') or record.get('sourceToolAssistantUUID'):
                return None
            message = record.get('message')
            if not isinstance(message, dict) or message.get('role') != 'user':
                return None
            state['session'] = session_identity(record.get('sessionId') or state['session'])
            content = message.get('content')
            text = content if isinstance(content, str) else '\n'.join(
                x['text'] for x in content if isinstance(x, dict) and x.get('type') == 'text' and isinstance(x.get('text'), str)
            ) if isinstance(content, list) else ''
            message_id = record.get('uuid')
            form = 'claude_user'
        if isinstance(text,str) and text.lstrip().startswith(CONTEXT_PREFIXES):
            state['context'] = state.get('context', 0)+1
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        if len(text) > MAX_INPUT:
            state['skipped'] += 1
            state['oversized'] = state.get('oversized', 0)+1
            return None
        try:
            parsed = dt.datetime.fromisoformat(moment.replace('Z', '+00:00'))
            if parsed.tzinfo is None or not 1970 <= parsed.year <= 2200:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            state['invalidDates'] += 1
            return None
        # Retain only the actual answer from a structured user reply, not its transport wrapper.
        if text.strip().startswith('<send_user_message_question_reply>'):
            try:
                replies = json.loads(re.sub(r'</?send_user_message_question_reply>', '', text).strip())
                text = '\n\n'.join('提问：'+str(x.get('question', ''))+'\n本人回答：'+str(x['answer']) for x in replies if isinstance(x, dict) and 'answer' in x)
            except (ValueError, TypeError):
                state['skipped'] += 1
                return None
        text, hidden = redact(text.strip())
        timestamp = parsed.astimezone(dt.timezone.utc).isoformat()
        session = state['session']
        message_id = sha(str(message_id or session+'\0'+timestamp+'\0'+text))
        return {'id': identifier(source_id+'\0'+session+'\0'+message_id), 'source_id': source_id,
                'session_id': session, 'message_id': message_id, 'timestamp': timestamp,
                'day': parsed.astimezone(ZoneInfo(self.s.setting('timeZone'))).date().isoformat(),
                'text': text, 'source_path': relative, 'format': form, 'signature': sha(text), 'redacted': int(hidden)}

    def scan(self, provider, directory, retry_path=None, expected_state=None):
        root = self.validate_root(provider, directory)
        source_id = identifier(provider+'\0'+str(root))
        with self.s.lock:
            old = {r['path']: json.loads(r['state']) for r in self.s.db.execute(
                'SELECT path,state FROM local_source_files WHERE source_id=?', (source_id,))}
            source = self.s.db.execute('SELECT revision,automatic FROM local_sources WHERE id=?', (source_id,)).fetchone()
            base_revision = source['revision'] if source else 0
            automatic = bool(source['automatic']) if source else False
        files, limited = self.files(provider, root)
        if retry_path is not None:
            # A retry can only revisit a file already inventoried in this source.
            # Keep every other file's saved progress, even if it has since changed.
            if retry_path not in old or sha(json.dumps(old[retry_path], sort_keys=True)) != expected_state:
                self.error(409, '文件扫描状态已变化，请刷新后重试。')
            files = [pair for pair in files if pair[0] == retry_path]
            if not files:
                self.error(409, '来源文件已移走或不可读取，请恢复文件后再试。')
        # Make small/recent conversations available before a multi-gigabyte tool log.
        files.sort(key=lambda pair: (max(0, old.get(pair[0], {}).get('size', pair[1].st_size)-old.get(pair[0], {}).get('offset', 0)) > SCAN_BYTES,
                                     -pair[1].st_mtime_ns))
        budget = SCAN_BYTES
        visited = 0
        messages = {}
        states = dict(old) if retry_path is not None else {}
        checks = {}
        for relative, info in files:
            state = dict(old.get(relative, {}))
            if retry_path is not None and self.file_complete(state):
                state = {}
                self.readers.discard(root/relative)
            compressed = relative.endswith('.jsonl.zst')
            identity = str(info.st_dev)+':'+str(info.st_ino)
            rewritten = False
            if not compressed and state.get('marker') and state.get('mtime') != info.st_mtime_ns and info.st_size >= state.get('offset', 0):
                try:rewritten = state['marker'] != file_marker(root/relative, state['offset'])
                except OSError:rewritten = True
            changed_compressed = compressed and (state.get('mtime') != info.st_mtime_ns or state.get('storedSize') != info.st_size)
            if state.get('identity') != identity or changed_compressed or (not compressed and (
                state.get('offset', 0) > info.st_size or
                (state.get('size') == info.st_size and state.get('mtime') != info.st_mtime_ns) or rewritten)):
                state = {'offset': 0, 'discarding': False, 'session': sha(relative)[:32],
                         'skipped': 0, 'malformed': 0, 'invalidDates': 0}
                self.readers.discard(root/relative)
            state.update({'identity': identity, 'storedSize': info.st_size, 'mtime': info.st_mtime_ns, 'compressed': compressed})
            try:
                size = (None if state.get('unknownSize') else state['size']) if compressed and 'size' in state else logical_size(root/relative, info)
                state['unknownSize'] = size is None
                state['size'] = size if size is not None else state['offset']
            except SOURCE_ERRORS:
                state.update({'size': state['offset'], 'unknownSize': True, 'unreadable': True})
                states[relative] = state
                continue
            done = state.get('eof', False) if compressed else state['offset'] >= state['size']
            if budget <= 0 or done or visited >= MAX_FILES:
                limited = limited or (visited >= MAX_FILES and not done)
                states[relative] = state
                continue
            visited += 1
            path = root/relative
            stream = None
            close_stream = False
            before_read = dict(state)
            file_events = {}
            try:
                if root not in path.resolve().parents:
                    raise OSError()
                stream, close_stream = self.readers.open(path, info, state['offset'])
                if stream:
                    state.pop('unreadable', None)
                    state.pop('awaitingTail', None)
                    read_start = state['offset']
                    digest = hashlib.sha256()
                    while budget > 0:
                        start = stream.tell()
                        # Finish this bounded line even at a batch boundary. A
                        # compressed stream must not replay its prefix per batch.
                        line = stream.readline(LINE_BYTES+1)
                        if not line:
                            state['eof'] = True
                            if compressed:
                                state['size'] = state['offset'];state['unknownSize'] = False
                            break
                        budget -= len(line)
                        if state['discarding']:
                            digest.update(line)
                            state['offset'] = stream.tell()
                            state['discarding'] = not line.endswith(b'\n')
                            continue
                        if len(line) > LINE_BYTES:
                            digest.update(line)
                            state['skipped'] += 1
                            state['oversized'] = state.get('oversized', 0)+1
                            state['offset'] = stream.tell()
                            state['discarding'] = not line.endswith(b'\n')
                            continue
                        try:
                            value = json.loads(line)
                        except (ValueError, UnicodeError):
                            if not line.endswith(b'\n') and not compressed:
                                # A partly written record is retried from its start, never counted twice.
                                state['offset'] = start
                                state['awaitingTail'] = True
                                break
                            if line.strip():
                                state['malformed'] += 1
                            digest.update(line)
                            state['offset'] = stream.tell()
                            continue
                        state['offset'] = stream.tell()
                        digest.update(line)
                        event = self.event(provider, value, state, relative, source_id)
                        if event:
                            file_events[event['id']] = event
                    read_end = state['offset']
                    checks[relative] = {'start': read_start, 'end': read_end, 'digest': digest.hexdigest()}
                    if not compressed:
                        state['marker'] = file_marker(path, state['offset'])
                    if compressed and state.get('eof'):
                        self.readers.discard(path)
                    for key, event in file_events.items():
                        messages.setdefault(key, event)
            except SOURCE_ERRORS:
                state = before_read
                state['unreadable'] = True
                self.readers.discard(path)
            finally:
                if close_stream and stream:
                    stream.close()
            states[relative] = state
            state['size'] = max(state['size'], state['offset'])
        # Canonicalize mirrored user events, while retaining repeated submissions of the same text.
        with self.s.lock:
            existing = [dict(r) for r in self.s.db.execute(
                'SELECT id,session_id,timestamp,day,length(text) AS characters,format,signature,redacted FROM local_inputs WHERE source_id=?', (source_id,))]
        mirrors = {}
        for item in existing:
            mirrors.setdefault((item['session_id'], item['signature']), []).append(item)
        clean = []
        for item in messages.values():
            mirror = any(x['format'] != item['format'] and x['id'] != item['id'] and
                         abs((dt.datetime.fromisoformat(x['timestamp'])-dt.datetime.fromisoformat(item['timestamp'])).total_seconds()) <= 2
                         for x in mirrors.get((item['session_id'], item['signature']), []))
            if not mirror:
                clean.append(item)
                mirrors.setdefault((item['session_id'], item['signature']), []).append(item)
        merged = {i['id']: i for i in existing}
        added = sum(i['id'] not in merged for i in clean)
        updated = sum(i['id'] in merged and merged[i['id']]['signature'] != i['signature'] for i in clean)
        merged.update({i['id']: i for i in clean})
        summary = self.summarize(list(merged.values()), states, limited)
        # A full reread can end at the same checkpoint. Still verify its read bytes
        # at commit, rather than letting an in-place edit race the archive write.
        changed_states = {path: state for path, state in states.items() if old.get(path) != state or path == retry_path}
        return {'sourceID': source_id, 'provider': provider, 'directory': str(root), 'states': changed_states,
                'events': clean, 'summary': summary, 'added': added, 'updated': updated,
                'checks': checks, 'baseRevision': base_revision, 'automatic': automatic,
                'config': self.config_summary(provider, root),
                'samples': [{'text': i['text'][:240], 'timestamp': i['timestamp']} for i in clean[:5]]}

    def summarize(self, inputs, states, limited=False):
        days = {}
        for item in inputs:
            days[item['day']] = days.get(item['day'], 0)+1
        return {'inputs': len(inputs), 'sessions': len({i['session_id'] for i in inputs}), 'activeDays': len(days),
                'characters': sum(i.get('characters', len(i.get('text', ''))) for i in inputs), 'redactedInputs': sum(bool(i['redacted']) for i in inputs),
                'files': len(states), 'completedFiles': sum(self.file_complete(s) for s in states.values()),
                'pendingFiles': sum(not self.file_complete(s) for s in states.values()),
                'compressedFiles': sum(bool(s.get('compressed')) for s in states.values()),
                'unknownSizeFiles': sum(bool(s.get('unknownSize')) for s in states.values()),
                'waitingFiles': sum(bool(s.get('awaitingTail')) and not s.get('unreadable') for s in states.values()),
                'compressionAvailable': pyzstd is not None,
                'pendingBytes': sum(max(0, s['size']-s['offset']) for s in states.values()),
                'processedBytes': sum(s['offset'] for s in states.values()),
                'skippedRecords': sum(s.get('skipped', 0)+s.get('malformed', 0)+s.get('invalidDates', 0) for s in states.values()),
                'excludedContextBlocks': sum(s.get('context', 0) for s in states.values()),
                'unreadableFiles': sum(bool(s.get('unreadable')) for s in states.values()), 'fileLimitReached': limited,
                'days': [{'day': day, 'count': count} for day, count in sorted(days.items(), reverse=True)[:30]]}

    @staticmethod
    def file_complete(state):
        return not state.get('unreadable') and (state.get('eof', False) if state.get('compressed') else state['offset'] >= state['size'])

    def preview(self, values, retry_path=None, expected_state=None):
        if not self.lock.acquire(blocking=False):
            self.error(409, '正在扫描本地资料，请稍后再试。')
        try:
            batch = self.scan(values.get('provider'), values.get('directory'), retry_path, expected_state)
            rid = str(uuid.uuid4()).upper()
            folder = self.s.data/'local-previews';folder.mkdir(exist_ok=True, mode=0o700)
            self.s._atomic(folder/(rid+'.json'), json.dumps(batch, ensure_ascii=False).encode())
            with self.s.lock, self.s.db:
                self.s.db.execute('INSERT INTO local_source_previews VALUES (?,?,?,?)', (rid, stamp(), 'preview', None))
            return {'id': rid, 'changedFiles': len(batch['states']), **{k: batch[k] for k in ['sourceID','provider','directory','summary','added','updated','config','samples','automatic']}}
        finally:
            self.lock.release()

    def document_path(self, item, provider):
        return 'sources/'+provider+'/'+item['source_id']+'/inputs/'+item['id']+'.md'

    def render(self, item, provider):
        return ('# '+PROVIDERS[provider]+' · 本人输入\n\n时间：'+item['timestamp']+'\n日期：'+item['day']+
                '\n来源会话：'+item['session_id']+'\n角色：用户\n\n仅归档本人输入。内容不自动成为已确认事实。\n\n'+item['text']+'\n')

    def commit(self, rid, automatic=False):
        from store import canonical_id
        rid = canonical_id(rid)
        if not isinstance(automatic, bool):
            self.error(400, '自动归档设置不正确。')
        with self.s.lock:
            preview = self.s.db.execute('SELECT * FROM local_source_previews WHERE id=?', (rid,)).fetchone()
            if not preview:
                self.error(404, '找不到这次扫描，请重新预览。')
            if preview['state'] == 'complete':
                return json.loads(preview['report'])
            if (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(preview['created_at'])).total_seconds() > 1800:
                self.error(409, '预览已过期，请重新扫描。')
            batch = json.loads((self.s.data/'local-previews'/(rid+'.json')).read_text())
        root = self.validate_root(batch['provider'], batch['directory'])
        for relative, expected in batch['states'].items():
            path = root/relative
            if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
                self.error(409, '来源文件已变化，请重新预览。')
            info = path.stat()
            compressed = expected.get('compressed', False)
            if str(info.st_dev)+':'+str(info.st_ino) != expected['identity'] or (
                compressed and (info.st_size != expected['storedSize'] or info.st_mtime_ns != expected['mtime'])) or (
                not compressed and info.st_size < expected['offset']):
                self.error(409, '来源文件已变化，请重新预览。')
            check = batch['checks'].get(relative)
            if check and not compressed:
                digest = hashlib.sha256()
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
                    stream.seek(check['start']);remaining = check['end']-check['start']
                    while remaining > 0:
                        block = stream.read(min(1024*1024, remaining))
                        if not block:
                            self.error(409, '来源文件已变化，请重新预览。')
                        digest.update(block);remaining -= len(block)
                if digest.hexdigest() != check['digest']:
                    self.error(409, '来源文件已变化，请重新预览。')
        with self.s.lock:
            current = self.s.db.execute('SELECT revision FROM local_sources WHERE id=?', (batch['sourceID'],)).fetchone()
            if (current['revision'] if current else 0) != batch['baseRevision']:
                self.error(409, '来源已被另一轮扫描更新，请重新预览。')
            with self.s.db:
                self.s.db.execute('''INSERT INTO local_sources(id,provider,directory,automatic,last_scan,config)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET automatic=excluded.automatic,
                    last_scan=excluded.last_scan,problem=NULL,config=excluded.config''',
                    (batch['sourceID'], batch['provider'], batch['directory'], int(automatic), stamp(), json.dumps(batch['config'])))
            conflicts = 0
            for item in batch['events']:
                path = self.document_path(item, batch['provider'])
                output = self.s.export(path, self.render(item, batch['provider']), sha(json.dumps(item, sort_keys=True)))
                if output['state'] == 'conflict':
                    conflicts += 1
                with self.s.db:
                    self.s.db.execute('''INSERT OR REPLACE INTO local_inputs
                        (id,source_id,session_id,message_id,timestamp,day,text,source_path,format,signature,redacted)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)''', tuple(item[k] for k in ['id','source_id','session_id','message_id','timestamp','day','text','source_path','format','signature','redacted']))
                self.s.catalog.index_input(item['id'])
            with self.s.db:
                for relative, state in batch['states'].items():
                    self.s.db.execute('INSERT OR REPLACE INTO local_source_files VALUES (?,?,?)', (batch['sourceID'], relative, json.dumps(state)))
                self.s.db.execute('UPDATE local_sources SET summary=?,revision=revision+1 WHERE id=?', (json.dumps(batch['summary']), batch['sourceID']))
            index = self.s.export('sources/'+batch['provider']+'/'+batch['sourceID']+'/index.json',
                          json.dumps({'provider': batch['provider'], 'statistics': batch['summary'], 'configuration': batch['config'],
                                      'includesAssistantText': False, 'includesCredentialFiles': False,
                                      'credentialRedaction': 'known-patterns'}, ensure_ascii=False, indent=2)+'\n',
                          sha(json.dumps([batch['summary'], batch['config']], sort_keys=True)))
            conflicts += int(index['state'] == 'conflict')
            report = {'sourceID': batch['sourceID'], 'added': batch['added'], 'updated': batch['updated'],
                      'conflicts': conflicts, 'summary': batch['summary']}
            with self.s.db:
                self.s.db.execute('UPDATE local_source_previews SET state=?,report=? WHERE id=?', ('complete', json.dumps(report), rid))
            (self.s.data/'local-previews'/(rid+'.json')).unlink(missing_ok=True)
            return report

    def cancel(self, rid):
        from store import canonical_id
        rid = canonical_id(rid)
        with self.s.lock:
            row = self.s.db.execute('SELECT state FROM local_source_previews WHERE id=?', (rid,)).fetchone()
            if row and row['state'] == 'preview':
                (self.s.data/'local-previews'/(rid+'.json')).unlink(missing_ok=True)
                with self.s.db:self.s.db.execute('DELETE FROM local_source_previews WHERE id=?', (rid,))
        return {'cancelled': True}

    def listing(self):
        with self.s.lock:
            rows = self.s.db.execute('SELECT * FROM local_sources ORDER BY provider,id').fetchall()
            return [{**dict(r), 'automatic': bool(r['automatic']), 'summary': json.loads(r['summary']) or self.summarize([], {}),
                     'config': json.loads(r['config']),
                     'job': self.job(r['id']),
                     'conflicts': [dict(c) for c in self.s.db.execute(
                         "SELECT path,state,generated_path AS generatedPath FROM exports WHERE root=? AND state='conflict' AND path LIKE ?",
                         (str(self.s.output), 'sources/'+r['provider']+'/'+r['id']+'/%'))]} for r in rows]

    def configure(self, rid, values):
        if not isinstance(values.get('automatic'), bool):
            self.error(400, '自动归档设置不正确。')
        with self.s.lock, self.s.db:
            changed = self.s.db.execute('UPDATE local_sources SET automatic=?,revision=revision+1 WHERE id=?', (int(values['automatic']), rid))
            if changed.rowcount != 1:
                self.error(404, '找不到这个资料来源。')
        return {'saved': True}

    def file_coverage(self, rid, parameters):
        """Local-only diagnostics contain paths and counts, never raw source contents."""
        try:
            limit = min(80, max(1, int(parameters.get('limit', 40))))
            offset = max(0, int(parameters.get('offset', 0)))
        except (ValueError, TypeError):
            self.error(400, '读取位置不正确。')
        with self.s.lock:
            source = self.s.db.execute('SELECT summary FROM local_sources WHERE id=?', (rid,)).fetchone()
            if source is None:self.error(404, '找不到这个资料来源。')
            items = []
            for row in self.s.db.execute('SELECT path,state FROM local_source_files WHERE source_id=? ORDER BY path', (rid,)):
                state = json.loads(row['state'])
                skipped = state.get('skipped', 0)
                issues = {'oversized': state.get('oversized', 0), 'malformed': state.get('malformed', 0),
                          'invalidDates': state.get('invalidDates', 0), 'other': max(0, skipped-state.get('oversized', 0))}
                status = ('unreadable' if state.get('unreadable') else 'waiting' if state.get('awaitingTail') else
                          'pending' if not self.file_complete(state) else 'skipped' if any(issues.values()) else 'complete')
                if parameters.get('attention', 'true') != 'false' and status == 'complete':continue
                items.append({'path': row['path'], 'stateVersion': sha(json.dumps(state, sort_keys=True)),
                              'status': status, 'issues': issues, 'processedBytes': state['offset'],
                              'totalBytes': None if state.get('unknownSize') else state['size']})
            job = self.job(rid)
            return {'items': items[offset:offset+limit], 'total': len(items),
                    'nextOffset': offset+limit if len(items) > offset+limit else None,
                    'summary': json.loads(source['summary']) or self.summarize([], {}),
                    'importActive': bool(job and job['state'] in ('queued', 'running', 'pausing'))}

    def retry_file(self, rid, values):
        path, version = values.get('path'), values.get('stateVersion')
        if not isinstance(path, str) or not isinstance(version, str):
            self.error(400, '请选择需要重新检查的会话文件。')
        with self.s.lock:
            source = self.s.db.execute('SELECT * FROM local_sources WHERE id=?', (rid,)).fetchone()
            if source is None:self.error(404, '找不到这个资料来源。')
            job = self.job(rid)
            if job and job['state'] in ('queued', 'running', 'pausing'):
                self.error(409, '请先暂停历史导入，再单独检查文件。')
            source = dict(source)
        preview = self.preview(source, retry_path=path, expected_state=version)
        try:
            report = self.commit(preview['id'], preview['automatic'])
            summary = report['summary']
            if summary['pendingFiles'] == 0 or summary['pendingFiles'] == summary.get('waitingFiles', 0):
                state = 'complete' if summary['pendingFiles'] == 0 else 'waiting'
                with self.s.lock, self.s.db:
                    self.s.db.execute("UPDATE local_source_jobs SET state=?,problem=NULL,updated_at=? WHERE source_id=? AND state NOT IN ('queued','running','pausing','paused')",
                                      (state, stamp(), rid))
            return report
        except Exception:
            self.cancel(preview['id'])
            raise

    def inputs(self, rid, parameters):
        try:
            limit = min(80, max(1, int(parameters.get('limit', 40))))
            offset = max(0, int(parameters.get('offset', 0)))
        except (ValueError, TypeError):
            self.error(400, '读取位置不正确。')
        with self.s.lock:
            where = ' WHERE source_id=?' if rid is not None else ''
            arguments = [rid] if rid is not None else []
            rows = self.s.db.execute('SELECT id FROM local_inputs'+where+' ORDER BY timestamp DESC,id DESC LIMIT ? OFFSET ?',
                                     arguments+[limit+1, offset]).fetchall()
            items = []
            for value in rows[:limit]:
                self.s.catalog.index_input(value['id'])
                row = self.s.db.execute("SELECT * FROM library_items WHERE kind='input' AND id=?", (value['id'],)).fetchone()
                items.append({**self.s.knowledge.source_data(row, row['body']), 'excerpt': row['body'][:240],
                              'provider': json.loads(row['data'])['provider']})
            return {'items': items, 'nextOffset': offset+limit if len(rows)>limit else None}

    def refresh(self, rid):
        with self.s.lock:
            row = self.s.db.execute('SELECT * FROM local_sources WHERE id=?', (rid,)).fetchone()
            if not row:
                self.error(404, '找不到这个资料来源。')
            values = dict(row)
        preview = self.preview(values)
        if preview['changedFiles'] == 0 and preview['added'] == 0 and preview['updated'] == 0:
            self.cancel(preview['id'])
            with self.s.lock, self.s.db:
                self.s.db.execute('UPDATE local_sources SET last_scan=?,problem=NULL WHERE id=?', (stamp(), rid))
            return {'sourceID': rid, 'added': 0, 'updated': 0, 'conflicts': 0, 'summary': preview['summary']}
        try:
            return self.commit(preview['id'], preview['automatic'])
        except Exception:
            self.cancel(preview['id'])
            raise

    def job(self, rid):
        with self.s.lock:
            row = self.s.db.execute('SELECT * FROM local_source_jobs WHERE source_id=?', (rid,)).fetchone()
            return dict(row) if row else None

    def start_import(self, rid):
        with self.s.lock, self.s.db:
            if not self.s.db.execute('SELECT 1 FROM local_sources WHERE id=?', (rid,)).fetchone():
                self.error(404, '请先预览并连接这个资料来源。')
            current = self.job(rid)
            if not current or current['state'] not in ('queued', 'running'):
                self.s.db.execute('''INSERT INTO local_source_jobs VALUES (?,?,?,?,0,NULL)
                    ON CONFLICT(source_id) DO UPDATE SET state='queued',updated_at=excluded.updated_at,problem=NULL''',
                    (rid, 'queued', stamp(), stamp()))
        self.resume_jobs()
        return self.job(rid)

    def pause_import(self, rid):
        with self.s.lock, self.s.db:
            self.s.db.execute("UPDATE local_source_jobs SET state=CASE WHEN state='running' THEN 'pausing' ELSE 'paused' END,updated_at=? WHERE source_id=?", (stamp(), rid))
        return self.job(rid) or {'state': 'paused'}

    def resume_jobs(self):
        with self.worker_lock:
            if self.stopping.is_set() or (self.worker and self.worker.is_alive()):
                return
            self.worker = threading.Thread(target=self._run_jobs, name='local-history-import', daemon=True)
            self.worker.start()

    def _run_jobs(self):
        while not self.stopping.is_set():
            with self.s.lock:
                row = self.s.db.execute("SELECT source_id FROM local_source_jobs WHERE state IN ('queued','running') ORDER BY updated_at LIMIT 1").fetchone()
            if row is None:
                # A new request cannot race worker shutdown and remain stranded.
                with self.worker_lock:
                    with self.s.lock:
                        pending = self.s.db.execute("SELECT 1 FROM local_source_jobs WHERE state IN ('queued','running') LIMIT 1").fetchone()
                    if not pending:
                        self.worker = None
                        return
                continue
            rid = row['source_id']
            with self.s.lock, self.s.db:
                changed = self.s.db.execute("UPDATE local_source_jobs SET state='running',updated_at=? WHERE source_id=? AND state IN ('queued','running')", (stamp(), rid))
                if not changed.rowcount:
                    self.s.db.execute("UPDATE local_source_jobs SET state='paused' WHERE source_id=? AND state='pausing'", (rid,))
                    continue
                before = json.loads(self.s.db.execute('SELECT summary FROM local_sources WHERE id=?', (rid,)).fetchone()[0])
            state, problem = 'queued', None
            try:
                report = self.refresh(rid)
                after = report['summary']
                if after.get('pendingFiles', 0) == 0:
                    state = 'complete'
                elif after.get('pendingFiles') == after.get('waitingFiles'):
                    state = 'waiting'
                elif all(before.get(k) == after.get(k) for k in ('processedBytes', 'completedFiles', 'inputs')):
                    state, problem = 'blocked', '等待来源写入完成或修复不可读文件，可稍后继续导入。'
            except Exception as error:
                if getattr(error, 'status', None) == 409:
                    state = 'queued'
                else:
                    state, problem = 'failed', '历史导入未完成，请检查目录与可用空间后继续。'
            with self.s.lock, self.s.db:
                self.s.db.execute("UPDATE local_source_jobs SET state=CASE WHEN state='pausing' THEN 'paused' ELSE ? END,problem=?,batches=batches+1,updated_at=? WHERE source_id=? AND state IN ('running','pausing')",
                                  (state, problem, stamp(), rid))
            self.stopping.wait(0.05)

    def catch_up(self):
        for source in self.listing():
            if not source['automatic']:
                continue
            if source['job'] and source['job']['state'] in ('queued', 'running', 'pausing', 'paused'):
                continue
            try:
                report = self.refresh(source['id'])
                pending = report['summary'].get('pendingFiles', 0)
                waiting = report['summary'].get('waitingFiles', 0)
                if pending > waiting:
                    self.start_import(source['id'])
                else:
                    with self.s.lock, self.s.db:
                        self.s.db.execute("UPDATE local_source_jobs SET state=?,problem=NULL,updated_at=? WHERE source_id=? AND state NOT IN ('queued','running','pausing','paused')",
                                          ('waiting' if pending else 'complete', stamp(), source['id']))
            except Exception:
                with self.s.lock, self.s.db:
                    self.s.db.execute('UPDATE local_sources SET problem=? WHERE id=?',
                                      ('本地资料扫描未完成，请检查目录权限后重试。', source['id']))
