"""Local, file-first storage. Python 3.9+, no model or cloud dependency."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import threading
import time
import uuid
from zoneinfo import ZoneInfo
from catalog import Catalog
from knowledge import Knowledge
from exchange import DataExchange
from analysis import DailyAnalysis, DEFAULT_RULES
from local_sources import LocalSources
from documents import DocumentImports
from memories import Memories
from memory_files import MemoryFiles
from memory_extraction import MemoryExtraction
from memory_reviews import MemoryReviews
from memory_segments import MemorySegments
from memory_batches import MemoryBatches
from answers import Answers
from transcription import Transcription

MAX_AUDIO = 2 * 1024 * 1024 * 1024
PROMPTS = {
    'daily-summary': DEFAULT_RULES,
    'memory-extraction': '# 记忆提取规则\n\n仅提出有来源的长期事实建议。区分本人和他人的表述，不把会议发言自动作为用户事实。写入前由用户确认。\n',
}

def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def canonical_id(value):
    try:
        return str(uuid.UUID(str(value))).upper()
    except (ValueError, TypeError, AttributeError):
        raise StoreError(400, '记录编号不正确。')

class StoreError(Exception):
    def __init__(self, status, message, path=None):
        super().__init__(message)
        self.status = status
        self.path = path

def validate_manifest(manifest, expected_id=None):
    if not isinstance(manifest, dict) or type(manifest.get('schemaVersion')) is not int or manifest['schemaVersion'] not in (1,2):
        raise StoreError(400, '不支持这个录音格式版本。')
    r = manifest.get('recording', {})
    if not isinstance(r, dict):
        raise StoreError(400, '录音信息不完整。')
    rid = canonical_id(r.get('id'))
    if expected_id and rid != canonical_id(expected_id):
        raise StoreError(400, '记录编号与请求不一致。')
    if manifest.get('audioFilename') != rid + '.caf':
        raise StoreError(400, '音频文件名不正确。')
    checksum = manifest.get('audioSHA256', '')
    if not isinstance(checksum, str) or not re.fullmatch(r'[0-9a-f]{64}', checksum):
        raise StoreError(400, '缺少有效的音频校验值。')
    duration, size = r.get('duration'), r.get('byteCount')
    if (not isinstance(duration, (int, float)) or isinstance(duration, bool)
        or not math.isfinite(duration) or duration <= 0
        or not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size > MAX_AUDIO):
        raise StoreError(400, '音频时长或大小不正确。')
    if r.get('status') not in ('saved', 'interrupted', 'recovered') or r.get('trashedAt') is not None:
        raise StoreError(400, '这条录音尚未完成保存。')
    try:
        parsed = dt.datetime.fromisoformat(r['startedAt'].replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        ZoneInfo(r.get('timeZone', 'UTC'))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise StoreError(400, '录音时间或时区不正确。')
    if r.get('sourceDevice') not in (None, 'phone', 'watch'):
        raise StoreError(400, '录音来源不正确。')
    for key, limit in [('title', 512), ('note', 8192), ('trigger', 64)]:
        value = r.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise StoreError(400, '录音文字信息过长。')
    markers=r.get('markers') or []
    if (not isinstance(markers,list) or len(markers)>1000
            or (r.get('markers') is not None and not isinstance(r['markers'],list))
            or (manifest['schemaVersion']==1 and markers) or (manifest['schemaVersion']==2 and not markers)):
        raise StoreError(400, '录音重点标记格式不正确，请更新接收端后重试。')
    seen=set();previous=-1
    for marker in markers:
        if not isinstance(marker,dict) or set(marker)!={'id','offsetMilliseconds'}:
            raise StoreError(400, '录音重点标记格式不正确，请更新接收端后重试。')
        mid=canonical_id(marker['id']);offset=marker['offsetMilliseconds']
        if mid in seen or type(offset) is not int or not 0<=offset<=31536000000 or offset<=previous:
            raise StoreError(400, '录音重点标记格式不正确，请更新接收端后重试。')
        marker['id']=mid;seen.add(mid);previous=offset
    r['id'] = rid
    return rid

def markers_checksum(recording):
    markers=recording.get('markers') or []
    return digest(''.join(m['id']+':'+str(m['offsetMilliseconds'])+'\n' for m in markers).encode()) if markers else None

class Store:
    def __init__(self, data_dir, output_dir=None):
        self.data = Path(data_dir).expanduser().resolve()
        existing_library = (self.data/'node.sqlite3').is_file()
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.recordings = self.data / 'recordings'
        self.recordings.mkdir(exist_ok=True)
        self.staging = self.data / 'staging'
        self.staging.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.data / 'node.sqlite3'), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recordings (id TEXT PRIMARY KEY, checksum TEXT NOT NULL, manifest TEXT NOT NULL, received_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY, text TEXT NOT NULL, sources TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS exports (root TEXT NOT NULL, path TEXT NOT NULL, last_hash TEXT, semantic_hash TEXT, state TEXT NOT NULL, generated_path TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(root,path));
        CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        ''')
        with self.db:
            for key, value in [('nodeID', str(uuid.uuid4())), ('timeZone', 'Asia/Shanghai'), ('outputDirectory', str(self.data / 'Library')),
                               ('dailyEnabled', 'true'), ('dailyTime', '00:15'), ('dailyLastRun', '')]:
                self.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, value))
            if output_dir:
                self.db.execute('UPDATE settings SET value=? WHERE key=?', (str(Path(output_dir).expanduser().resolve()), 'outputDirectory'))
        self.output = Path(self.setting('outputDirectory'))
        from output_root import OutputRoot, OutputRootError, saved_identity, bind_root
        try:
            if not existing_library or output_dir:
                self.output.mkdir(parents=True, exist_ok=True)
            with self.db:
                identity = bind_root(self.db, self.output, None if output_dir else saved_identity(self.db))
            self.output_root = OutputRoot(self.output, identity)
        except OutputRootError as error:
            self.db.close();raise StoreError(503, str(error))
        self._initialize_files()
        self._recover_received()
        for row in self.db.execute('SELECT id FROM recordings'):
            self._ensure_transcript(row['id'])
        self.local_sources = LocalSources(self)
        self.documents = DocumentImports(self)
        self.memory_files = MemoryFiles(self)
        self.memories = Memories(self)
        self.memory_reviews = MemoryReviews(self)
        self.catalog = Catalog(self)
        self.memories.migrate()
        self.knowledge = Knowledge(self)
        from source_history import SourceHistory
        self.source_history = SourceHistory(self)
        self.exchange = DataExchange(self)
        self.analysis = DailyAnalysis(self)
        self.answers = Answers(self)
        self.memory_segments = MemorySegments(self)
        self.memory_extraction = MemoryExtraction(self)
        self.memory_batches = MemoryBatches(self)
        from memory_review_batches import MemoryReviewBatches
        self.memory_review_batches = MemoryReviewBatches(self)
        self.transcription = Transcription(self)
        from transcript_reviews import TranscriptReviews
        self.transcript_reviews = TranscriptReviews(self)
        self.transcript_reviews.recover()
        from recording_repairs import RecordingRepairs
        self.recording_repairs = RecordingRepairs(self)
        self.recording_repairs.recover()

    def _ensure_transcript(self, rid):
        try:
            path = self._path('transcripts/' + rid + '.md')
            if not path.exists():
                self._atomic(path, b'')
        except (OSError, StoreError):
            # The receive receipt covers durable audio, independently of the output folder.
            # Reading or editing this document will report the folder error to the client.
            pass

    def close(self):
        self.source_history.close()
        documents_closed = self.documents.close()
        answers_closed = self.answers.close()
        batches_closed = self.memory_batches.close()
        sources_closed = self.local_sources.close()
        speech_closed = self.transcription.close()
        memories_closed = self.memory_extraction.close()
        review_batches_closed = self.memory_review_batches.close()
        reviews_closed = self.memory_reviews.close()
        if sources_closed and speech_closed and memories_closed and reviews_closed and batches_closed and answers_closed and documents_closed and review_batches_closed:
            self.db.close()
            return True
        return False

    def setting(self, key):
        # The shared connection's statement cache must not be stepped by two
        # threads at once. Workers and HTTP identity checks both read settings.
        with self.lock:
            return self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()[0]

    def _path(self, relative):
        self.require_output()
        root = self.output.resolve()
        path = (root / relative).resolve()
        if root not in path.parents:
            raise StoreError(400, '文件位置不在归档目录中。')
        return path

    def require_output(self, writable=False):
        from output_root import OutputRootError
        try:self.output_root.check(writable)
        except OutputRootError as error:raise StoreError(503,str(error))

    def _initialize_files(self):
        for name, text in PROMPTS.items():
            p = self._path('prompts/' + name + '.md')
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                self._atomic(p, text.encode())
            elif name=='daily-summary' and p.read_text()=='# 每日摘要规则\n\n围绕提供的记录整理事实、待办和想法，标注来源。没有依据的内容不补充。\n\n当前未启用文字模型，系统只生成按来源组织的草稿。\n':
                self._history('prompts/'+name+'.md',p.read_bytes())
                self._atomic(p,text.encode())
        for name in ['memory.md', 'memory-inbox.md']:
            if not self._path(name).exists():
                self.export(name, '# 长期记忆\n' if name == 'memory.md' else '# 待确认记忆\n', 'empty')

    def _atomic(self, path, data):
        from output_root import OutputRootError
        try:relative = path.relative_to(self.output)
        except ValueError:relative = None
        if relative is not None:
            try:return self.output_root.atomic(relative, data)
            except OutputRootError as error:raise StoreError(503,str(error))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name('.' + path.name + '.' + secrets.token_hex(6) + '.tmp')
        try:
            with open(temp, 'xb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, path)
            # Persist the directory entry before issuing a receive acknowledgement.
            fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temp.exists():
                temp.unlink()

    def _recover_received(self):
        for folder in self.recordings.iterdir():
            if not folder.is_dir() or self.db.execute('SELECT 1 FROM recordings WHERE id=?', (folder.name,)).fetchone():
                continue
            try:
                m = json.loads((folder / 'manifest.json').read_text())
                rid = validate_manifest(m, folder.name)
                audio = folder / (rid + '.caf')
                if audio.stat().st_size != m['recording']['byteCount'] or file_hash(audio) != m['audioSHA256']:
                    continue
                with self.db:
                    self.db.execute('INSERT INTO recordings VALUES (?,?,?,?)', (rid, m['audioSHA256'], json.dumps(m), stamp()))
            except (OSError, ValueError, StoreError):
                # Keep unreadable packages for repair; never erase their audio.
                continue

    def accept(self, manifest, audio_path):
        rid = validate_manifest(manifest)
        path = Path(audio_path)
        if path.stat().st_size != manifest['recording']['byteCount'] or file_hash(path) != manifest['audioSHA256']:
            raise StoreError(422, '音频校验失败，请重新传输。')
        with self.lock:
            existing = self.db.execute('SELECT checksum,manifest FROM recordings WHERE id=?', (rid,)).fetchone()
            if existing:
                if existing['checksum'] != manifest['audioSHA256']:
                    raise StoreError(409, '相同编号对应不同音频，原有录音未被覆盖。')
                if markers_checksum(json.loads(existing['manifest'])['recording']) != markers_checksum(manifest['recording']):
                    raise StoreError(409, '接收方的重点标记不一致或尚未确认，原音与标记仍保留在发送设备。')
                return self.receipt(rid, verify_files=True)
            folder = self.recordings / rid
            if folder.exists():
                cached = folder / (rid + '.caf')
                if not cached.exists() or file_hash(cached) != manifest['audioSHA256']:
                    raise StoreError(409, '存在需要恢复的同名录音。')
                try:
                    previous=json.loads((folder/'manifest.json').read_text())
                    validate_manifest(previous,rid)
                    if markers_checksum(previous['recording'])!=markers_checksum(manifest['recording']):
                        raise ValueError()
                except (OSError,ValueError,StoreError):
                    raise StoreError(409,'存在需要恢复的同名录音。')
            else:
                prepared = self.staging / str(uuid.uuid4())
                prepared.mkdir()
                try:
                    target = prepared / (rid + '.caf')
                    shutil.copyfile(path, target)
                    with open(target, 'rb') as f:
                        os.fsync(f.fileno())
                    self._atomic(prepared / 'manifest.json', json.dumps(manifest, ensure_ascii=False).encode())
                    os.rename(prepared, folder)
                    fd = os.open(str(self.recordings), os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                finally:
                    if prepared.exists():
                        shutil.rmtree(prepared)
            with self.db:
                self.db.execute('INSERT INTO recordings VALUES (?,?,?,?)', (rid, manifest['audioSHA256'], json.dumps(manifest), stamp()))
            self._ensure_transcript(rid)
            self.catalog.index_recording(rid)
            try:self.catalog.index_daily(self.day_for(manifest['recording']))
            except (OSError, StoreError):pass  # Durable audio does not depend on the text folder.
            self.transcription.wake.set()
            return self.receipt(rid)

    def prepare_recording_upload(self, rid, manifest):
        validate_manifest(manifest,rid)
        raw=json.dumps(manifest,ensure_ascii=False,separators=(',',':'),sort_keys=True).encode()
        if len(raw)>256*1024:raise StoreError(413,'录音信息过大。')
        key=digest(raw);directory=self.staging/'recording-manifests'
        with self.lock:
            directory.mkdir(exist_ok=True,mode=0o700)
            now=time.time()
            for path in directory.glob('*.json'):
                if now-path.stat().st_mtime>24*60*60:path.unlink()
            target=directory/(key+'.json')
            if not target.exists() and sum(1 for _ in directory.glob('*.json'))>=64:
                raise StoreError(503,'录音上传准备已满，请稍后重试；原音与标记仍保留。')
            self._atomic(target,raw)
        return {'manifestID':key,'nodeID':self.setting('nodeID')}

    def prepared_recording_upload(self, rid, key):
        import stat
        if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{64}',key):
            raise StoreError(400,'录音上传准备信息不正确。')
        path=self.staging/'recording-manifests'/(key+'.json')
        try:
            if path.is_symlink():raise FileNotFoundError()
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or time.time()-info.st_mtime>24*60*60:raise FileNotFoundError()
                raw=stream.read(256*1024+1)
        except (FileNotFoundError,IsADirectoryError):
            raise StoreError(409,'录音上传准备已失效，请重新发送；原音与标记仍保留。')
        if len(raw)>256*1024 or digest(raw)!=key:raise StoreError(409,'录音上传准备已变化，请重新发送。')
        try:manifest=json.loads(raw)
        except (ValueError,UnicodeError):raise StoreError(400,'录音上传准备信息不正确。')
        validate_manifest(manifest,rid)
        return manifest

    def receipt(self, rid, verify_files=False):
        with self.lock:
            row = self.db.execute('SELECT checksum,manifest FROM recordings WHERE id=?', (canonical_id(rid),)).fetchone()
            if not row:
                raise StoreError(404, '找不到这条录音。')
            manifest=json.loads(row['manifest'])
            if verify_files:
                # A receipt recheck must not confuse a database row with durable files.
                rid=canonical_id(rid);folder=self.recordings/rid
                try:
                    directory=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                    try:
                        fd=os.open('manifest.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
                        with os.fdopen(fd,'rb') as stream:
                            metadata=os.fstat(stream.fileno())
                            if not stat.S_ISREG(metadata.st_mode):raise ValueError()
                            raw=stream.read(256*1024+1)
                        if len(raw)>256*1024 or json.loads(raw)!=manifest:raise ValueError()
                        fd=os.open(rid+'.caf',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
                        with os.fdopen(fd,'rb') as stream:
                            before=os.fstat(stream.fileno())
                            if not stat.S_ISREG(before.st_mode) or before.st_size!=manifest['recording']['byteCount']:raise ValueError()
                            checksum=hashlib.sha256()
                            remaining=before.st_size
                            while remaining:
                                block=stream.read(min(remaining,1024*1024))
                                if not block:raise ValueError()
                                checksum.update(block);remaining-=len(block)
                            after=os.fstat(stream.fileno())
                            if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns) or checksum.hexdigest()!=row['checksum']:raise ValueError()
                            current=os.stat(rid+'.caf',dir_fd=directory,follow_symlinks=False)
                            if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError()
                        current=os.stat('manifest.json',dir_fd=directory,follow_symlinks=False)
                        if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns,current.st_ctime_ns)!=(metadata.st_dev,metadata.st_ino,metadata.st_size,metadata.st_mtime_ns,metadata.st_ctime_ns):raise ValueError()
                        actual=os.stat(folder,follow_symlinks=False);opened=os.fstat(directory)
                        if (actual.st_dev,actual.st_ino)!=(opened.st_dev,opened.st_ino):raise ValueError()
                    finally:os.close(directory)
                except (OSError,ValueError,UnicodeError):
                    raise StoreError(409,'电脑上的接收文件缺失或已变化，暂不能确认接收；请在电脑检查，发送设备的原音仍保留。')
            result={'recordingID': canonical_id(rid), 'audioSHA256': row['checksum'], 'nodeID': self.setting('nodeID'), 'durable': True,
                    'markersSHA256':markers_checksum(manifest['recording'])}
            if hasattr(self,'recording_repairs'):
                repair=self.db.execute("SELECT id FROM recording_repairs WHERE recording_id=? AND state='done' ORDER BY rowid DESC LIMIT 1",(canonical_id(rid),)).fetchone()
                if repair:result['repairRequestID']=repair['id']
            return result

    def manifest(self, rid):
        row = self.db.execute('SELECT manifest FROM recordings WHERE id=?', (canonical_id(rid),)).fetchone()
        if not row:
            raise StoreError(404, '找不到这条录音。')
        return json.loads(row['manifest'])

    def audio_path(self, rid):
        rid = canonical_id(rid)
        self.receipt(rid)
        return self.recordings / rid / (rid + '.caf')

    def list_recordings(self):
        if hasattr(self, 'catalog'):
            return self.catalog.all_records()
        with self.lock:
            result = []
            for row in self.db.execute('SELECT * FROM recordings ORDER BY received_at DESC'):
                m = json.loads(row['manifest']);r = m['recording'];rid = r['id']
                text = self.read_document('transcripts/' + rid + '.md')
                result.append({'id': rid, 'title': r.get('title') or '语音记录', 'startedAt': r['startedAt'],
                               'duration': r['duration'], 'byteCount': r['byteCount'], 'sourceDevice': r.get('sourceDevice'), 'markers':r.get('markers') or [],
                               'audioSHA256': m['audioSHA256'], 'textState': 'ready' if text['text'].strip() else 'awaitingText'})
            return sorted(result, key=lambda r: dt.datetime.fromisoformat(r['startedAt'].replace('Z', '+00:00')), reverse=True)

    def read_document(self, relative):
        path = self._path(relative)
        raw = path.read_bytes() if path.exists() else b''
        return {'text': raw.decode('utf-8'), 'etag': digest(raw) if path.exists() else '', 'path': relative}

    def edit_document(self, relative, text, expected, maximum=1024 * 1024):
        if not isinstance(text, str) or len(text.encode()) > maximum:
            raise StoreError(400, '文字过长。')
        with self.lock:
            current = self.read_document(relative)
            if expected != current['etag']:
                raise StoreError(409, '文件已被其他地方修改，请重新读取后再保存。')
            path = self._path(relative)
            if path.exists() and current['text'] != text:
                self._history(relative, path.read_bytes())
            self._atomic(path, text.encode())
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO exports VALUES (?,?,?,?,?,?,?)',
                    (str(self.output), relative, digest(text.encode()), None, 'manual', None, stamp()))
            if hasattr(self, 'catalog') and relative.startswith('daily/'):
                self.catalog.index_daily(Path(relative).stem)
            return self.read_document(relative)

    def _history(self, relative, raw):
        name = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-' + digest(raw)[:10] + '.md'
        self._atomic(self._path('.history/' + relative + '/' + name), raw)

    def export(self, relative, text, semantic):
        with self.lock:
            path = self._path(relative);raw = text.encode();sha = digest(raw)
            row = self.db.execute('SELECT * FROM exports WHERE root=? AND path=?', (str(self.output), relative)).fetchone()
            current = path.read_bytes() if path.exists() else None
            if (row and row['state'] == 'reviewed' and row['semantic_hash'] == semantic
                    and current is not None and digest(current) == row['last_hash']):
                return dict(row)
            if (row and row['state'] == 'conflict' and row['semantic_hash'] == semantic
                and row['generated_path'] and self._path(row['generated_path']).exists()
                and current is not None and digest(current) != row['last_hash']):
                return dict(row)
            if current == raw:
                state, generated = 'current', None
            elif current is not None and (not row or row['state'] in ('manual', 'reviewed') or digest(current) != row['last_hash']):
                generated = '.generated/' + relative + '/' + sha[:16] + '.md'
                self._atomic(self._path(generated), raw)
                state = 'conflict'
            elif row and row['semantic_hash'] == semantic and current is not None:
                return dict(row)
            else:
                if current is not None:
                    self._history(relative, current)
                self._atomic(path, raw)
                state, generated = 'current', None
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO exports VALUES (?,?,?,?,?,?,?)',
                    (str(self.output), relative, sha if state == 'current' else (row['last_hash'] if row else None), semantic, state, generated, stamp()))
            return {'state': state, 'path': relative, 'generated_path': generated}

    def day_for(self, record):
        return dt.datetime.fromisoformat(record['startedAt'].replace('Z', '+00:00')).astimezone(ZoneInfo(self.setting('timeZone'))).date().isoformat()

    def build_daily(self, day):
        try:
            if dt.date.fromisoformat(day).isoformat() != day:
                raise ValueError()
        except ValueError:
            raise StoreError(400, '日期不正确。')
        with self.lock:
            records = self.catalog.all_records(day)
            sections = [];signature = []
            for r in reversed(records):
                document = self.read_document('transcripts/' + r['id'] + '.md')
                signature.append((r['id'], document['etag']))
                title = r['title'].replace('\n', ' ')
                sections.append('## ' + title + '\n\n来源：[' + r['id'] + '](../transcripts/' + r['id'] + '.md) · ' + r['startedAt'] + '\n\n' + (document['text'].strip() or '_原音已接收，等待添加文字。_'))
            notes = self.db.execute("SELECT id,title FROM library_items WHERE kind='note' AND day=? ORDER BY sort_date,id", (day,)).fetchall()
            for note in notes:
                document = self.read_document('notes/' + note['id'] + '.md')
                signature.append(('note', note['id'], note['title'], document['etag']))
                sections.append('## ' + note['title'].replace('\n', ' ') + '\n\n来源：[' + note['id'] + '](../notes/' + note['id'] + '.md)\n\n' + document['text'].strip())
            semantic = digest(json.dumps([day, self.setting('timeZone'), signature], ensure_ascii=False).encode())
            ready = sum(bool(self.read_document('transcripts/' + r['id'] + '.md')['text'].strip()) for r in records)
            text = '# ' + day + ' · 每日草稿\n\n覆盖 ' + str(len(records)) + ' 条录音，其中 ' + str(ready) + ' 条已有文字；另含 ' + str(len(notes)) + ' 篇笔记。时区：' + self.setting('timeZone') + '。\n\n更新时间：' + stamp() + '。\n\n按来源整理的原文，不代表模型分析。\n\n' + '\n\n'.join(sections) + '\n'
            result = self.export('daily/' + day + '.md', text, semantic)
            self.catalog.index_daily(day)
            return result

    def daily_list(self):
        with self.lock:
            days = {r[0] for r in self.db.execute("SELECT DISTINCT day FROM library_items WHERE kind IN ('recording','note')")}
            days.update(p.stem for p in self._path('daily/index').parent.glob('????-??-??.md'))
            result = []
            for day in sorted(days, reverse=True):
                relative = 'daily/' + day + '.md'
                row = self.db.execute('SELECT state,generated_path FROM exports WHERE root=? AND path=?', (str(self.output), relative)).fetchone()
                result.append({'id': day, 'state': row['state'] if row else 'pending', 'generatedPath': row['generated_path'] if row else None})
            return result

    def daily_settings(self, now=None):
        with self.lock:
            zone=ZoneInfo(self.setting('timeZone'))
            local=(now or dt.datetime.now(dt.timezone.utc)).astimezone(zone)
            hour,minute=map(int,self.setting('dailyTime').split(':'))
            next_run=local.replace(hour=hour,minute=minute,second=0,microsecond=0)
            if next_run<=local:next_run+=dt.timedelta(days=1)
            enabled=self.setting('dailyEnabled')=='true'
            return {'enabled':enabled,'time':self.setting('dailyTime'),'timeZone':self.setting('timeZone'),
                    'nextRun':next_run.isoformat() if enabled else None,'lastRun':self.setting('dailyLastRun') or None}

    def configure_daily(self, values):
        if not isinstance(values.get('enabled'),bool) or not isinstance(values.get('time'),str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',values['time']):
            raise StoreError(400,'请填写有效的整理时间，格式为 HH:mm。')
        timezone=values.get('timeZone')
        try:ZoneInfo(timezone)
        except (KeyError,ValueError,TypeError):raise StoreError(400,'时区名称不正确。')
        with self.lock:
            with self.db:
                for key,value in [('dailyEnabled','true' if values['enabled'] else 'false'),('dailyTime',values['time']),('timeZone',timezone)]:
                    self.db.execute('UPDATE settings SET value=? WHERE key=?',(value,key))
            self.catalog.reconcile()
            return self.daily_settings()

    def catch_up(self, now=None):
        with self.lock:
            self.catalog.reconcile()
            if self.setting('dailyEnabled')!='true':return
            local=(now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(self.setting('timeZone')))
            today=local.date().isoformat()
            hour,minute=map(int,self.setting('dailyTime').split(':'))
            cutoff=local.replace(hour=hour,minute=minute,second=0,microsecond=0)
            last_day=(local.date()-dt.timedelta(days=1 if local>=cutoff else 2)).isoformat()
            for day in {r[0] for r in self.db.execute("SELECT DISTINCT day FROM library_items WHERE kind IN ('recording','note')")}:
                if day <= last_day or self._path('daily/' + day + '.md').exists():
                    self.build_daily(day)
            with self.db:
                self.db.execute('UPDATE settings SET value=? WHERE key=?',(local.isoformat(timespec='seconds'),'dailyLastRun'))

    def save_transcript(self, rid, text, expected):
        with self.lock:
            m = self.manifest(rid)
            result = self.edit_document('transcripts/' + canonical_id(rid) + '.md', text, expected)
            self.catalog.index_recording(canonical_id(rid), force=True)
            self.build_daily(self.day_for(m['recording']))
            return result

    def candidates(self):
        with self.lock:
            return [self.memories.data(r) for r in self.db.execute('SELECT * FROM candidates ORDER BY created_at DESC')]

    def add_candidate(self, text, sources=None, evidence=None, request_id=None):
        return self.memories.add(text, sources, evidence, request_id)

    def decide_candidate(self, cid, decision):
        if decision not in ('confirmed', 'rejected'):
            raise StoreError(400, '无效的记忆操作。')
        result = self.memories.update(cid, {'state': decision}, legacy=True)
        return {'exports': result['exports']}

    def export_memory(self):
        with self.lock:
            with self.db:
                self.memories.mark_delivery_pending()
                if self.memories.delivery_paused():
                    return {'exports': []}  # Save the decision, but leave restored files untouched until explicit resume.
                self.db.execute('UPDATE memory_delivery SET last_attempt=? WHERE id=1', (stamp(),))
            try:
                result = self._export_memory()
            except Exception:
                # Exception text may contain local paths or file contents. Keep a safe user message.
                with self.db:
                    self.db.execute('UPDATE memory_delivery SET problem=? WHERE id=1',
                                    ('记忆已保存在资料库，部分文字文件尚未写入。请检查电脑上的资料夹是否可用及剩余空间，然后重试。',))
                raise
            with self.db:
                self.db.execute('UPDATE memory_delivery SET pending=0,output_root=?,last_success=?,problem=NULL WHERE id=1',
                                (str(self.output), stamp()))
            return result

    def _export_memory(self):
        results = []
        names = {r['id']:r['name'] for r in self.catalog.categories('memory')} if hasattr(self, 'catalog') else {}
        for state, relative, title in [('confirmed', 'memory.md', '长期记忆'), ('pending', 'memory-inbox.md', '待确认记忆'),
                                       ('inactive', 'memory-inactive.md', '已停用记忆'), ('rejected', 'memory-rejected.md', '已忽略记忆'),
                                       ('archived', 'memory-archive.md', '已归档记忆')]:
            rows = [r for r in self.candidates() if r['state'] == state]
            if hasattr(self, 'catalog'):
                rows = []
                for candidate in self.candidates():
                    value = self.catalog.detail('memory', candidate['id'])
                    if (state == 'archived' and value['organization']['archived']) or (state == value['state'] and not value['organization']['archived']):
                        rows.append(value)
            text = '# ' + title + '\n\n'
            groups = {}
            for r in rows:
                groups.setdefault(r.get('organization', {}).get('categoryID', ''), []).append(r)
            for category, entries in groups.items():
                text += '## ' + names.get(category, '未分类') + '\n\n'
                for r in entries:
                    text += '### ' + r['id'] + '\n\n' + r['text'] + '\n\n'
                    if state == 'archived':
                        text += '确认状态：' + {'confirmed': '已确认', 'pending': '待确认', 'rejected': '已忽略', 'inactive': '已停用'}[r['state']] + '\n\n'
                    tags = r.get('organization', {}).get('tags', [])
                    if tags: text += '标签：' + '、'.join(tags) + '\n\n'
                    links = []
                    for evidence in r.get('evidence', []):
                        label = evidence['title'] or evidence['id']
                        label = label.replace('[', '').replace(']', '').replace('\n', ' ')
                        relative_source = evidence.get('path') or ({'recording': 'transcripts', 'note': 'notes', 'document': 'documents'}.get(evidence['kind'], 'sources') + '/' + evidence['id'] + '.md')
                        links.append('[' + label + '](' + relative_source + ')')
                        if evidence['quote']:
                            text += '> ' + evidence['quote'].replace('\n', '\n> ') + '\n\n'
                    text += '来源：' + '、'.join(links) + '\n\n'
                    text += '[依据与修订历史](memories/' + r['id'] + '.json)\n\n'
            results.append(self.export(relative, text, digest(json.dumps(rows, sort_keys=True).encode())))
        results += self.memories.export_history()
        return {'exports': results}

    def status(self):
        with self.lock:
            from output_root import issue
            conflicts = [dict(r) for r in self.db.execute("SELECT path,state,generated_path AS generatedPath FROM exports WHERE root=? AND state='conflict'", (str(self.output),))]
            return {'nodeID': self.setting('nodeID'), 'timeZone': self.setting('timeZone'), 'outputDirectory': str(self.output),
                    'outputIssue': issue(self.db,self.output),
                    'recognitionEnabled': self.transcription.state()['automatic'],
                    'transcriptionConfigured': self.transcription.configuration is not None,
                    'summaryMode': 'sourceDraft', 'conflicts': conflicts,
                    'memoryDelivery': self.memories.delivery_status(),
                    'archiveCopySupported':True,
                    'recordingIdentitySupported':True,
                    'recordingRepairSupported':True,
                    'sourceHistorySupported':True,
                    'noteIdentitySupported':True,
                    'analysisConfigured':hasattr(self,'analysis') and self.analysis.configuration is not None,
                    'answerConfigured':self.answers.configuration is not None}

    def configure(self, values):
        with self.lock:
            self.require_output(True)
            if values.get('archiveSourceNodeID',self.setting('nodeID'))!=self.setting('nodeID'):
                raise StoreError(409,'资料库已变化，未复制。请重新连接并核对。')
            previous_timezone=self.setting('timeZone')
            timezone=values.get('timeZone',previous_timezone)
            try:ZoneInfo(timezone)
            except (KeyError,ValueError,TypeError):raise StoreError(400,'时区名称不正确。')
            raw_value=values.get('outputDirectory',str(self.output))
            if not isinstance(raw_value,str) or not raw_value or not Path(raw_value).expanduser().is_absolute():
                raise StoreError(400,'请选择有效的完整归档目录。')
            raw=Path(raw_value).expanduser().absolute()
            if raw.is_symlink():raise StoreError(400,'目标资料夹是文件链接，未复制。请选择实际资料夹。')
            output=raw.resolve();receipt=None
            request_id=canonical_id(values['archiveCopyID'] if values.get('archiveCopyID') is not None else uuid.uuid4())
            if values.get('archiveCopyID') is not None:
                if not isinstance(values.get('archiveSourceNodeID'),str):
                    raise StoreError(400,'请先核对资料库身份与原归档位置。')
                saved_row=self.db.execute('SELECT value FROM settings WHERE key=?',('archiveCopy:'+request_id,)).fetchone()
                if saved_row:
                    saved=json.loads(saved_row[0])
                    if (saved['outputDirectory']!=str(output) or saved['sourceDirectory']!=values.get('archiveSourceDirectory')
                            or ('timeZone' in values and saved['timeZone']!=timezone)):
                        raise StoreError(409,'这次归档复制的参数已变化，请重新核对并使用新的操作。')
                    if self.output!=output:raise StoreError(409,'这次复制已经完成，但当前目录已变更。请刷新后再操作。')
                    return {**self.status(),'archiveTransfer':saved}
                if values.get('archiveSourceDirectory')!=str(self.output):
                    raise StoreError(409,'原归档位置已变化，未复制。请刷新后重新选择。')
            if output!=self.output:
                from archive_copy import copy_archive
                from backup import BackupError
                try:receipt=copy_archive(self,raw,request_id,timezone)
                except BackupError as error:raise StoreError(409,str(error),getattr(error,'path',None))
                except OSError:raise StoreError(409,'复制未完成。原目录仍在使用，已完成的副本保留，可重新选择同一目录重试。')
                self.output=output
                from output_root import OutputRoot, saved_identity
                self.output_root=OutputRoot(self.output,saved_identity(self.db))
            else:
                try:
                    row=self.db.execute("SELECT value FROM settings WHERE key='archiveCopyReceipt'").fetchone()
                    saved=json.loads(row[0]) if row else {}
                    if isinstance(saved,dict) and saved.get('id')==request_id and saved.get('outputDirectory')==str(output):receipt=saved
                except (ValueError,TypeError):pass
                if not output.is_dir():raise StoreError(409,'当前资料夹不可用，请先连接存储位置或重新关联已有资料夹。')
            with self.db:self.db.execute('UPDATE settings SET value=? WHERE key=?',(timezone,'timeZone'))
            # A byte-identical copy does not regenerate projections, request a
            # model, or change the user's file-delivery pause and decisions.
            if timezone!=previous_timezone:self.catch_up()
            result=self.status()
            if receipt:result['archiveTransfer']=receipt
            return result
