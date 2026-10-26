"""Durable, opt-in speech jobs. Provider secrets exist only in this process.

Remote services cannot promise exactly-once billing after an ambiguous timeout.
Completed chunks are committed before continuing; uncertain requests retry at
most three times. Only complete results become the editable transcript.
"""
import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from analysis import NoRedirect
from speech_audio import AudioError, chunks, wav_part


class SpeechFailure(Exception):
    def __init__(self, message, retry=False):
        super().__init__(message)
        self.retry = retry


class Transcription:
    def __init__(self, store):
        self.s = store
        self.configuration = None
        self.worker = None
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.run_lock = threading.Lock()
        with store.lock, store.db:
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS speech_jobs(
                    id TEXT PRIMARY KEY, checksum TEXT NOT NULL, service TEXT NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_run REAL NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
                    baseline TEXT NOT NULL, output_root TEXT NOT NULL,
                    problem TEXT, generated_path TEXT, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS speech_parts(
                    id TEXT NOT NULL, part INTEGER NOT NULL, start_frame INTEGER NOT NULL,
                    end_frame INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(id,part));
            ''')
            for key, value in [('speechAutomatic', ''), ('speechAfter', '0')]:
                store.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, value))
            store.db.execute("UPDATE speech_jobs SET status='queued',problem='上次转写被中断，将从未完成片段继续。' WHERE status='running'")

    @staticmethod
    def error(status, message):
        from store import StoreError
        raise StoreError(status, message)

    def configure(self, values):
        with self.s.lock:
            if values.get('clear') is True:
                self.configuration = None
                return self.state()
            endpoint, model, key = (values.get(k) for k in ('endpoint', 'model', 'key'))
            if not all(isinstance(v, str) and v.strip() for v in (endpoint, model, key)):
                self.error(400, '语音服务、模型和密钥不完整。')
            try:
                url = urllib.parse.urlsplit(endpoint)
                valid = url.scheme == 'https' and url.hostname and not (url.username or url.password or url.query or url.fragment)
            except ValueError:
                valid = False
            if not valid or len(endpoint) > 2048 or any(ord(c) < 32 for c in endpoint + model + key):
                self.error(400, '语音服务请使用不含密钥的 HTTPS 地址。')
            if len(model) > 200 or len(key) > 4096:
                self.error(400, '语音服务配置格式不正确。')
            endpoint, model = endpoint.rstrip('/'), model.strip()
            signature = hashlib.sha256(json.dumps([endpoint, model]).encode()).hexdigest()
            self.configuration = {'endpoint': endpoint, 'model': model, 'key': key.strip(), 'service': signature}
        self.wake.set()
        return self.state()

    def state(self):
        with self.s.lock:
            config = self.configuration
            return {'configured': config is not None,
                    'endpoint': config['endpoint'] if config else '', 'model': config['model'] if config else '',
                    'automatic': bool(config and self.s.setting('speechAutomatic') == config['service'])}

    def set_automatic(self, values):
        with self.s.lock, self.s.db:
            if not isinstance(values.get('automatic'), bool):
                self.error(400, '自动转写设置不正确。')
            if values['automatic'] and not self.configuration:
                self.error(409, '请先在电脑配置语音服务和密钥。')
            if values['automatic'] and not self.state()['automatic']:
                last = self.s.db.execute('SELECT COALESCE(MAX(rowid),0) FROM recordings').fetchone()[0]
                self.s.db.execute("UPDATE settings SET value=? WHERE key='speechAfter'", (str(last),))
            self.s.db.execute("UPDATE settings SET value=? WHERE key='speechAutomatic'",
                              (self.configuration['service'] if values['automatic'] else '',))
            if not values['automatic']:
                self.s.db.execute("UPDATE speech_jobs SET status='paused' WHERE status IN ('queued','running','retry')")
        self.wake.set()
        return self.state()

    def status(self, rid):
        from store import canonical_id
        rid = canonical_id(rid)
        with self.s.lock:
            self.s.receipt(rid)
            row = self.s.db.execute('SELECT * FROM speech_jobs WHERE id=?', (rid,)).fetchone()
            result = {'id': rid, 'status': 'idle', 'attempts': 0, 'total': 0, 'completed': 0,
                      'configured': self.configuration is not None, 'problem': None, 'generatedPath': None, 'reviewSupported': True}
            if row:
                result.update({k: row[k] for k in ('status', 'attempts', 'total', 'problem')})
                result['generatedPath'] = row['generated_path']
                result['completed'] = self.s.db.execute('SELECT COUNT(*) FROM speech_parts WHERE id=?', (rid,)).fetchone()[0]
                result['serviceChanged'] = bool(self.configuration and row['service'] != self.configuration['service'])
                result['nextRetryAt'] = row['next_run'] if row['status'] == 'retry' else None
                result['fileReview'] = self.s.transcript_reviews.last_decision(rid)
                result['pendingReview'] = self.s.transcript_reviews.pending(rid)
            return result

    def enqueue(self, rid):
        from store import canonical_id, stamp
        rid = canonical_id(rid)
        with self.s.lock, self.s.db:
            if not self.configuration:
                self.error(409, '请先在电脑配置语音服务和密钥。')
            row = self.s.db.execute('SELECT * FROM speech_jobs WHERE id=?', (rid,)).fetchone()
            if row:
                if row['service'] != self.configuration['service'] and row['status'] not in ('ready', 'conflict'):
                    completed = self.s.db.execute('SELECT COUNT(*) FROM speech_parts WHERE id=?', (rid,)).fetchone()[0]
                    if row['status'] == 'running':
                        self.error(409, '上一段请求正在结束，请稍后继续。')
                    if completed:
                        self.error(409, '服务或模型已更换，请恢复原配置后继续，避免混合转写结果。')
                    self.s.db.execute("UPDATE speech_jobs SET service=?,status='queued',attempts=0,next_run=0,problem=NULL,updated_at=? WHERE id=?",
                                      (self.configuration['service'], stamp(), rid))
                    self.wake.set()
                    return self.status(rid)
                if row['status'] in ('ready', 'conflict', 'queued', 'running', 'retry'):
                    return self.status(rid)
                self.s.db.execute("UPDATE speech_jobs SET status='queued',attempts=0,next_run=0,problem=NULL,updated_at=? WHERE id=?", (stamp(), rid))
            else:
                receipt = self.s.receipt(rid)
                document = self.s.read_document('transcripts/' + rid + '.md')
                self.s.db.execute('''INSERT INTO speech_jobs(id,checksum,service,status,baseline,output_root,updated_at)
                    VALUES (?,?,?,'queued',?,?,?)''', (rid, receipt['audioSHA256'], self.configuration['service'],
                                                     document['etag'], str(self.s.output), stamp()))
        self.wake.set()
        return self.status(rid)

    def pause(self, rid):
        from store import canonical_id, stamp
        rid = canonical_id(rid)
        with self.s.lock, self.s.db:
            self.s.receipt(rid)
            self.s.db.execute("UPDATE speech_jobs SET status='paused',problem=NULL,updated_at=? WHERE id=? AND status IN ('queued','running','retry')", (stamp(), rid))
        return self.status(rid)

    def generated(self, rid):
        result = self.status(rid)
        if not result['generatedPath']:
            self.error(404, '还没有另存的转写版本。')
        return self.s.read_document(result['generatedPath'])

    def start_worker(self):
        if self.worker is None:
            self.worker = threading.Thread(target=self._loop, daemon=True)
            self.worker.start()

    def close(self):
        self.stopped.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=2)
        return not self.worker or not self.worker.is_alive()

    def _loop(self):
        while not self.stopped.is_set():
            try:
                if self.run_once():
                    continue
            except Exception:
                pass  # No exception text: providers may echo credentials or original content.
            self.wake.wait(1)
            self.wake.clear()

    def _queue_new(self):
        if not self.state()['automatic']:
            return
        rows = self.s.db.execute('''SELECT r.id FROM recordings r LEFT JOIN speech_jobs j ON j.id=r.id
            JOIN library_items l ON l.id=r.id AND l.kind='recording'
            WHERE r.rowid>? AND j.id IS NULL AND l.archived=0 AND l.state='awaitingText'
            ORDER BY r.rowid LIMIT 50''', (int(self.s.setting('speechAfter')),)).fetchall()
        for row in rows:
            self.enqueue(row['id'])

    def run_once(self):
        from store import stamp
        if self.stopped.is_set() or not self.run_lock.acquire(blocking=False):
            return False
        try:
            with self.s.lock:
                if not self.configuration:
                    return False
                self._queue_new()
                config = dict(self.configuration)
                job = self.s.db.execute('''SELECT * FROM speech_jobs WHERE service=? AND
                    status IN ('queued','retry') AND next_run<=? ORDER BY updated_at,id LIMIT 1''',
                    (config['service'], time.time())).fetchone()
                if not job:
                    return False
                with self.s.db:
                    self.s.db.execute("UPDATE speech_jobs SET status='running',attempts=attempts+1,problem=NULL,updated_at=? WHERE id=?", (stamp(), job['id']))
            self._run(dict(job), config)
            return True
        finally:
            self.run_lock.release()

    def _allowed(self, rid, config):
        with self.s.lock:
            return (not self.stopped.is_set() and self.configuration == config and
                    self.s.db.execute('SELECT status FROM speech_jobs WHERE id=?', (rid,)).fetchone()[0] == 'running')

    def _run(self, job, config):
        from store import file_hash, stamp
        rid = job['id']
        try:
            path = self.s.audio_path(rid)
            if file_hash(path) != job['checksum']:
                raise SpeechFailure('原音校验失败，尚未发送。请检查原文件。')
            parts = list(chunks(path))
            with self.s.lock, self.s.db:
                self.s.db.execute('UPDATE speech_jobs SET total=? WHERE id=?', (len(parts), rid))
            for index, (layout, start, end) in enumerate(parts):
                if not self._allowed(rid, config):
                    return
                with self.s.lock:
                    previous = self.s.db.execute('SELECT * FROM speech_parts WHERE id=? AND part=?', (rid, index)).fetchone()
                if previous:
                    if (previous['start_frame'], previous['end_frame']) != (start, end):
                        raise SpeechFailure('音频分段已变化，原有结果保留，请检查应用版本。')
                    continue
                text = self.request(config, wav_part(path, layout, start, end))
                with self.s.lock, self.s.db:
                    self.s.db.execute('INSERT INTO speech_parts VALUES (?,?,?,?,?)', (rid, index, start, end, text))
            if self._allowed(rid, config):
                self._finish(job, config)
        except (SpeechFailure, AudioError, OSError) as error:
            retry = isinstance(error, SpeechFailure) and error.retry and job['attempts'] + 1 < 3
            message = str(error) if isinstance(error, (SpeechFailure, AudioError)) else '无法读取原音或写入文字，已完成片段保留。'
            with self.s.lock, self.s.db:
                self.s.db.execute("UPDATE speech_jobs SET status=?,problem=?,next_run=?,updated_at=? WHERE id=? AND status='running'",
                                  ('retry' if retry else 'failed', message, time.time() + 30 * (4 ** job['attempts']), stamp(), rid))
        except Exception:
            with self.s.lock, self.s.db:
                self.s.db.execute("UPDATE speech_jobs SET status='failed',problem='转写未完成，原音与已完成片段保留。',updated_at=? WHERE id=? AND status='running'", (stamp(), rid))
        finally:
            with self.s.lock, self.s.db:
                # Removing/changing the service or shutdown stops further requests.
                self.s.db.execute("UPDATE speech_jobs SET status='queued',updated_at=? WHERE id=? AND status='running'", (stamp(), rid))

    @staticmethod
    def request(config, audio):
        boundary = 'Tracebook' + uuid.uuid4().hex
        fields = b''
        for name, value in [('model', config['model']), ('response_format', 'json')]:
            fields += ('--' + boundary + '\r\nContent-Disposition: form-data; name="' + name + '"\r\n\r\n' + value + '\r\n').encode()
        body = fields + ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="recording.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode() + audio + ('\r\n--' + boundary + '--\r\n').encode()
        request = urllib.request.Request(config['endpoint'] + '/audio/transcriptions', data=body, method='POST',
            headers={'Authorization': 'Bearer ' + config['key'], 'Content-Type': 'multipart/form-data; boundary=' + boundary})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=90) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise SpeechFailure('语音服务返回内容过大，未写入文字。')
            payload = json.loads(raw)
            text = payload.get('text') if isinstance(payload, dict) else None
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > 512000 or '\x00' in text:
                raise SpeechFailure('没有收到有效的转写文字，请检查音频与语音模型。')
            if config['key'] in text:
                raise SpeechFailure('语音服务返回内容包含凭证，已拒绝保存。')
            return text.strip()
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise SpeechFailure('语音服务拒绝访问，请检查密钥和模型权限。')
            if error.code == 429:
                raise SpeechFailure('语音服务额度或频率受限，稍后重试。', retry=True)
            raise SpeechFailure('语音请求失败，请检查服务地址和模型名称。', retry=error.code >= 500)
        except (urllib.error.URLError, OSError):
            raise SpeechFailure('语音服务连接超时或不可用，已完成片段保留。', retry=True)
        except (ValueError, TypeError):
            raise SpeechFailure('语音服务返回格式不正确，未写入文字。')

    def _finish(self, job, config):
        from store import digest, stamp
        rid = job['id']
        relative = 'transcripts/' + rid + '.md'
        with self.s.lock:
            parts = self.s.db.execute('SELECT text FROM speech_parts WHERE id=? ORDER BY part', (rid,)).fetchall()
            manifest = self.s.manifest(rid)['recording']
            text = '\n\n'.join(p['text'] for p in parts) + '\n\n---\n\n机器转写，尚未人工校对；说话者身份未识别。\n\n来源：' + rid + '\n\n录音时间：' + manifest['startedAt'] + '\n\n语音模型：' + config['model'] + '\n'
            if len(text.encode()) > 1024 * 1024:
                raise SpeechFailure('完整转写文字过长，片段已保留，尚未写入正文。')
            current = self.s.read_document(relative)
            row = self.s.db.execute('SELECT * FROM exports WHERE root=? AND path=?', (str(self.s.output), relative)).fetchone()
            # Only adopt the original empty placeholder; even an explicitly saved
            # empty manual edit is protected. A new output root requires review.
            if (not row and not current['text'] and current['etag'] == job['baseline'] and str(self.s.output) == job['output_root']):
                with self.s.db:
                    self.s.db.execute('INSERT INTO exports VALUES (?,?,?,?,?,?,?)',
                                      (str(self.s.output), relative, current['etag'], None, 'current', None, stamp()))
            semantic = digest(json.dumps([job['checksum'], job['service'], text]).encode())
            result = self.s.export(relative, text, semantic)
            with self.s.db:
                self.s.db.execute('UPDATE speech_jobs SET status=?,generated_path=?,problem=NULL,updated_at=? WHERE id=?',
                    ('conflict' if result['state'] == 'conflict' else 'ready', result['generated_path'], stamp(), rid))
            self.s.catalog.index_recording(rid, force=True)
            self.s.build_daily(self.s.day_for(manifest))
