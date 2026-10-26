#!/usr/bin/env python3
"""Voice Memory receiver, file workspace and explicitly configured text analysis."""
from __future__ import annotations
import argparse
import base64
import faulthandler
import fcntl
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from store import Store, StoreError, MAX_AUDIO, validate_manifest, canonical_id
from knowledge import SCOPES

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, store, token):
        self.store, self.token = store, token
        self.upload_slots = threading.BoundedSemaphore(3)
        self.requests_condition = threading.Condition()
        self.active_requests = 0
        self.closing = False
        super().__init__(address, Handler)
        self.store.transcription.start_worker()

    def server_close(self):
        # Idle keep-alive sockets must not hold shutdown open, but a request
        # already writing library data must finish before the process exits.
        with self.requests_condition:
            self.closing = True
            while self.active_requests:self.requests_condition.wait()
        self.store.documents.close()
        self.store.transcription.close()
        super().server_close()

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'VoiceMemory/0.2'
    def log_message(self, *_):
        pass  # Never print headers, tokens, or recording content.

    def setup(self):
        super().setup()
        self.connection.settimeout(120)

    def send_json(self, code, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def body_json(self, max_size=2*1024*1024):
        try:
            size = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            raise StoreError(400, '请求长度不正确。')
        if size < 0 or size > max_size:
            raise StoreError(413, '请求内容过大。')
        try:
            value = json.loads(self.rfile.read(size))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise StoreError(400, '请求格式不正确。')

    def dispatch(self):
        path = urllib.parse.urlsplit(self.path).path
        if self.command == 'GET' and path == '/health':
            return self.send_json(200, {'service': 'voice-memory', 'version': 1})
        authorization = self.headers.get('Authorization', '')
        parts = [urllib.parse.unquote(p) for p in path.strip('/').split('/')]
        store = self.server.store
        if parts[:2] == ['v1', 'assistant']:
            scopes = store.knowledge.authorize(authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else '')
            if parts == ['v1','assistant','capabilities'] and self.command == 'GET':
                return self.send_json(200, {'version':1,'mode':'retrieval','readOnly':True,'scopes':scopes,
                    'limits':{'results':20,'charactersPerRead':16000,'requestsPerMinute':60}})
            if parts in (['v1','assistant','search'], ['v1','assistant','query']) and self.command == 'POST':
                return self.send_json(200, store.knowledge.query(self.body_json(), scopes))
            if len(parts)==5 and parts[:3]==['v1','assistant','items'] and self.command=='GET':
                parameters={k:v[-1] for k,v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
                return self.send_json(200, store.knowledge.read_source(parts[3],parts[4],scopes,parameters))
            raise StoreError(403, '只读接入不支持这个操作。')
        if not hmac.compare_digest(authorization, 'Bearer ' + self.server.token):
            self.close_connection = True
            raise StoreError(401, '连接口令不正确。')
        if parts[:2] == ['v1','recordings']:
            receiver = self.headers.get('X-Recording-Node-ID')
            if receiver is not None and receiver != store.setting('nodeID'):
                self.close_connection = True  # Do not parse a body meant for another library.
                raise StoreError(409,'接收资料库已变化，请在同步页核对后继续。原音仍保留在这台设备。')
        if parts[:2] == ['v1','local-sources']:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise StoreError(403,'请在资料库所在电脑管理本地来源。')
            if len(parts)==2 and self.command=='GET':
                return self.send_json(200,{'sources':store.local_sources.listing(),'discovered':store.local_sources.discover()})
            if parts==['v1','local-sources','recent-inputs'] and self.command=='GET':
                parameters={k:v[-1] for k,v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
                return self.send_json(200,store.local_sources.inputs(None,parameters))
            if parts==['v1','local-sources','preview'] and self.command=='POST':
                return self.send_json(200,store.local_sources.preview(self.body_json()))
            if len(parts)==5 and parts[2]=='previews' and self.command=='POST':
                values=self.body_json()
                if parts[4]=='commit':return self.send_json(200,store.local_sources.commit(parts[3],values.get('automatic',False)))
                if parts[4]=='cancel':return self.send_json(200,store.local_sources.cancel(parts[3]))
            if len(parts)==3 and self.command=='PUT':
                return self.send_json(200,store.local_sources.configure(parts[2],self.body_json()))
            if len(parts)==4 and parts[3]=='refresh' and self.command=='POST':
                self.body_json()
                return self.send_json(200,store.local_sources.refresh(parts[2]))
            if len(parts)==4 and parts[3] in ('import-history','pause-import') and self.command=='POST':
                self.body_json()
                action = store.local_sources.start_import if parts[3]=='import-history' else store.local_sources.pause_import
                return self.send_json(200,action(parts[2]))
            if len(parts)==4 and parts[3]=='inputs' and self.command=='GET':
                parameters={k:v[-1] for k,v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
                return self.send_json(200,store.local_sources.inputs(parts[2],parameters))
            if len(parts)==4 and parts[3]=='files' and self.command=='GET':
                parameters={k:v[-1] for k,v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
                return self.send_json(200,store.local_sources.file_coverage(parts[2],parameters))
            if len(parts)==4 and parts[3]=='file-issues' and self.command=='GET':
                parameters={k:v[-1] for k,v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
                return self.send_json(200,store.local_sources.file_issues(parts[2],parameters))
            if len(parts)==4 and parts[3]=='retry-file' and self.command=='POST':
                return self.send_json(200,store.local_sources.retry_file(parts[2],self.body_json()))
            raise StoreError(404,'找不到这个资料来源操作。')
        if len(parts)==4 and parts[:2]==['v1','documents'] and parts[3]=='text':
            if self.command=='GET':return self.send_json(200,store.documents.text_document(parts[2]))
            if self.command=='PUT':return self.send_json(200,store.documents.text_document(parts[2],self.body_json(12*1024*1024+65536)))
            raise StoreError(404,'找不到这次资料校对操作。')
        if parts[:2] == ['v1','documents']:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise StoreError(403,'请在资料库所在电脑导入资料。')
            if len(parts)==2 and self.command=='GET':
                parameters={k:v[-1] for k,v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
                return self.send_json(200,store.documents.listing(parameters))
            if len(parts)==3 and parts[2]=='preview' and self.command=='POST':
                from documents import MAX_REQUEST
                return self.send_json(200,store.documents.preview(self.body_json(MAX_REQUEST)))
            if parts==['v1','documents','prepare'] and self.command=='POST':
                from documents import MAX_REQUEST
                return self.send_json(202,store.documents.prepare(self.body_json(MAX_REQUEST)))
            if len(parts)==4 and parts[3]=='preparation' and self.command=='GET':
                return self.send_json(200,store.documents.preparation(parts[2]))
            if len(parts)==4 and parts[3]=='original' and self.command=='GET':
                return self.send_json(200,store.documents.original(parts[2]))
            if len(parts)==4 and self.command=='POST':
                self.body_json()
                if parts[3]=='commit':return self.send_json(200,store.documents.commit(parts[2]))
                if parts[3]=='cancel':return self.send_json(200,store.documents.cancel(parts[2]))
            raise StoreError(404,'找不到这次资料导入。')
        if parts in (['v1','transcription-session'], ['v1','transcription-settings']):
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise StoreError(403,'请在资料库所在电脑配置语音服务。')
            if self.command == 'GET':return self.send_json(200,store.transcription.state())
            if parts[1] == 'transcription-session' and self.command == 'POST':
                return self.send_json(200,store.transcription.configure(self.body_json()))
            if parts[1] == 'transcription-settings' and self.command == 'PUT':
                return self.send_json(200,store.transcription.set_automatic(self.body_json()))
        if len(parts)==4 and parts[:2]==['v1','recordings'] and parts[3]=='prepare' and self.command=='POST':
            return self.send_json(200,store.prepare_recording_upload(parts[2],self.body_json(256*1024)))
        if len(parts)>=4 and parts[:2]==['v1','recordings'] and parts[3] in ('repair-preview','repair'):
            if self.headers.get('X-Recording-Node-ID')!=store.setting('nodeID'):
                self.close_connection=True
                raise StoreError(409,'请先核对接收资料库，再修复原音。')
            if len(parts)==4 and parts[3]=='repair-preview' and self.command=='POST':
                return self.send_json(200,store.recording_repairs.preview(parts[2],self.body_json(16384)))
            if len(parts)==5 and parts[3]=='repair':
                if self.command=='POST':return self.send_json(200,store.recording_repairs.prepare(parts[2],parts[4],self.body_json(16384)))
                if self.command=='GET':return self.send_json(200,store.recording_repairs.status(parts[2],parts[4]))
            if len(parts)==6 and parts[3]=='repair' and parts[5]=='audio' and self.command=='PUT':
                return self.receive_audio(parts[2],repair=parts[4])
            raise StoreError(404,'找不到这次原音修复操作。')
        if len(parts) in (3,4) and parts[:2] == ['v1','transcriptions']:
            if len(parts) == 3:
                if self.command == 'GET':return self.send_json(200,store.transcription.status(parts[2]))
                if self.command == 'POST':
                    self.body_json()
                    return self.send_json(202,store.transcription.enqueue(parts[2]))
            if len(parts) == 4 and parts[3] == 'pause' and self.command == 'POST':
                self.body_json()
                return self.send_json(200,store.transcription.pause(parts[2]))
            if len(parts) == 4 and parts[3] == 'generated' and self.command == 'GET':
                return self.send_json(200,store.transcription.generated(parts[2]))
            if len(parts)==4 and parts[3] in ('review','review-retry'):
                try:
                    if parts[3]=='review':
                        if self.command=='GET':return self.send_json(200,store.transcript_reviews.preview(parts[2]))
                        if self.command=='POST':return self.send_json(200,store.transcript_reviews.resolve(parts[2],self.body_json(max_size=8*1024*1024)))
                    elif self.command=='POST':return self.send_json(200,store.transcript_reviews.retry(parts[2],self.body_json()))
                except OSError:
                    raise StoreError(503,'转写文件暂时无法读写。请检查资料夹连接和权限，再继续核对；已保存的备份仍保留。')
        if parts==['v1','memory-delivery'] and self.command=='GET':
            return self.send_json(200,store.memories.delivery_status())
        if parts[:2]==['v1','memory-files']:
            try:
                if parts==['v1','memory-files','preview'] and self.command=='GET':
                    values=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    return self.send_json(200,store.memory_files.preview(values.get('path',[''])[-1]))
                if parts==['v1','memory-files','resolve'] and self.command=='POST':
                    return self.send_json(200,store.memory_files.resolve(self.body_json()))
                if parts==['v1','memory-files','retry'] and self.command=='POST':
                    return self.send_json(200,store.memory_files.retry(self.body_json()))
            except OSError:
                raise StoreError(503,'记忆文件暂时无法读写。请检查资料夹连接和权限；备份与合并记录仍保留。')
        if parts==['v1','memory-delivery','retry'] and self.command=='POST':
            self.body_json()
            return self.send_json(200,store.memories.deliver_pending(force=True))
        if parts==['v1','model-session']:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:raise StoreError(403,'请在资料库所在电脑配置模型。')
            if self.command=='POST':return self.send_json(200,store.analysis.configure(self.body_json()))
            if self.command=='GET':return self.send_json(200,store.analysis.state())
        if parts==['v1','answer-session']:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:raise StoreError(403,'请在资料库所在电脑配置回答模型。')
            if self.command=='POST':return self.send_json(200,store.answers.configure(self.body_json()))
            if self.command=='GET':return self.send_json(200,store.answers.configuration_state())
        if parts==['v1','memory-review-batches']:
            if self.command=='GET':return self.send_json(200,store.memory_review_batches.listing())
            if self.command=='POST':return self.send_json(202,store.memory_review_batches.start(self.body_json()))
        if parts==['v1','memory-review-batches','preview'] and self.command=='POST':
            return self.send_json(200,store.memory_review_batches.preview(self.body_json().get('pairs')))
        if len(parts)==3 and parts[:2]==['v1','memory-review-batches'] and self.command=='GET':
            return self.send_json(200,store.memory_review_batches.status(parts[2]))
        if len(parts)==4 and parts[:2]==['v1','memory-review-batches']:
            if parts[3]=='preview' and self.command=='GET':return self.send_json(200,store.memory_review_batches.resume_preview(parts[2]))
            if self.command=='POST':
                values=self.body_json()
                if parts[3]=='pause':return self.send_json(200,store.memory_review_batches.pause(parts[2]))
                if parts[3]=='resume':return self.send_json(202,store.memory_review_batches.resume(parts[2],values))
        if parts==['v1','memory-reviews','preview'] and self.command=='POST':
            return self.send_json(200,store.memory_reviews.preview(self.body_json()))
        if parts==['v1','memory-reviews'] and self.command=='POST':
            return self.send_json(202,store.memory_reviews.start(self.body_json()))
        if len(parts)==3 and parts[:2]==['v1','memory-reviews'] and self.command=='GET':
            return self.send_json(200,store.memory_reviews.status(parts[2]))
        if len(parts)==4 and parts[:2]==['v1','memory-segments']:
            if self.command=='GET':
                parameters=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                return self.send_json(200,store.memory_segments.prepare(parts[2],parts[3],parameters.get('version',[None])[-1]))
            if self.command=='POST':return self.send_json(200,store.memory_segments.read(parts[2],parts[3],self.body_json()))
        if parts==['v1','memory-batches']:
            if self.command=='GET':return self.send_json(200,store.memory_batches.listing())
            if self.command=='POST':return self.send_json(202,store.memory_batches.start(self.body_json()))
        if parts==['v1','memory-batches','sources'] and self.command=='GET':
            parameters={k:v[-1] for k,v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
            return self.send_json(200,store.memory_batches.sources(parameters))
        if parts==['v1','memory-batches','preview'] and self.command=='POST':
            return self.send_json(200,store.memory_batches.preview(self.body_json().get('sources')))
        if len(parts)==3 and parts[:2]==['v1','memory-batches'] and self.command=='GET':
            return self.send_json(200,store.memory_batches.status(parts[2]))
        if len(parts)==4 and parts[:2]==['v1','memory-batches'] and self.command=='POST':
            values=self.body_json()
            if parts[3]=='pause':return self.send_json(200,store.memory_batches.pause(parts[2]))
            if parts[3]=='resume':return self.send_json(202,store.memory_batches.resume(parts[2],values))
        if len(parts)==4 and parts[:2]==['v1','memory-extractions']:
            if self.command=='GET':
                parameters=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                return self.send_json(200,store.memory_extraction.status(parts[2],parts[3],parameters.get('version',[None])[-1]))
            if self.command=='POST':
                return self.send_json(202,store.memory_extraction.start(parts[2],parts[3],self.body_json()))
        if parts==['v1','analysis-settings']:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:raise StoreError(403,'请在资料库所在电脑配置模型。')
            if self.command=='GET':return self.send_json(200,store.analysis.state())
            if self.command=='PUT':return self.send_json(200,store.analysis.set_automatic(self.body_json()))
        if len(parts)>=3 and parts[:2]==['v1','analysis']:
            import datetime as dt
            try:
                if dt.date.fromisoformat(parts[2]).isoformat()!=parts[2]:raise ValueError()
            except ValueError:raise StoreError(400,'日期不正确。')
            if len(parts)==3:
                if self.command=='GET':return self.send_json(200,store.analysis.status(parts[2]))
                if self.command=='POST':
                    if not ipaddress.ip_address(self.client_address[0]).is_loopback:raise StoreError(403,'请在资料库所在电脑启动模型分析。')
                    values=self.body_json()
                    return self.send_json(202,store.analysis.start(parts[2],values.get('force') is True))
            if len(parts)==4 and parts[3]=='document':
                relative='analysis/'+parts[2]+'.md'
                if self.command=='GET':return self.send_json(200,store.read_document(relative))
                if self.command=='PUT':
                    values=self.body_json();result=store.edit_document(relative,values.get('text'),values.get('etag'))
                    store.catalog.index_analysis(parts[2]);return self.send_json(200,result)
            if len(parts)==4 and parts[3]=='generated' and self.command=='GET':
                return self.send_json(200,store.analysis.generated_document(parts[2]))
        if parts[:2] == ['v1','imports']:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise StoreError(403,'请在资料库所在电脑导入聊天。')
            if parts==['v1','imports','preview'] and self.command=='POST':
                from exchange import MAX_IMPORT
                try:size=int(self.headers.get('Content-Length','-1'))
                except ValueError:raise StoreError(400,'请求长度不正确。')
                if not 0<size<=MAX_IMPORT:raise StoreError(413,'导入文件超过 64 MB，请分批导入。')
                return self.send_json(200,store.exchange.preview(self.rfile.read(size)))
            if len(parts)==4 and self.command=='POST':
                self.body_json()
                if parts[3]=='commit':return self.send_json(200,store.exchange.commit(parts[2]))
                if parts[3]=='cancel':return self.send_json(200,store.exchange.cancel(parts[2]))
            raise StoreError(404,'找不到这次导入。')
        if parts==['v1','export'] and self.command=='GET':
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise StoreError(403,'请在资料库所在电脑导出资料。')
            file=store.exchange.export()
            try:
                self.send_response(200)
                self.send_header('Content-Type','application/zip')
                self.send_header('Content-Disposition','attachment; filename="personal-library.zip"')
                self.send_header('Content-Length',str(file.stat().st_size))
                self.send_header('Cache-Control','no-store');self.end_headers()
                with file.open('rb') as stream:
                    while True:
                        block=stream.read(1024*1024)
                        if not block:break
                        self.wfile.write(block)
            finally:file.unlink(missing_ok=True)
            return
        if parts[:2] == ['v1','integrations']:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise StoreError(403, '请在资料库所在电脑管理接入。')
            if len(parts)==2:
                if self.command=='GET':return self.send_json(200,store.knowledge.list_grants())
                if self.command=='POST':return self.send_json(201,store.knowledge.create_grant(self.body_json()))
            if len(parts)==3 and self.command=='PUT':
                if self.body_json().get('revoked') is not True:raise StoreError(400,'只支持撤销接入。')
                return self.send_json(200,store.knowledge.revoke_grant(parts[2]))
        if parts==['v1','query'] and self.command=='POST':
            return self.send_json(200,store.knowledge.query(self.body_json()))
        if len(parts)==4 and parts[:2]==['v1','sources'] and self.command=='GET':
            parameters={k:v[-1] for k,v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
            return self.send_json(200,store.knowledge.read_source(parts[2],parts[3],list(SCOPES),parameters))
        if len(parts)==6 and parts[:2]==['v1','conversations'] and parts[3]=='turns':
            if parts[5]=='answer-preview' and self.command=='POST':
                return self.send_json(200,store.answers.preview(parts[2],parts[4],self.body_json()))
            if parts[5]=='answer':
                if self.command=='GET':return self.send_json(200,store.answers.status(parts[2],parts[4]))
                if self.command=='POST':return self.send_json(202,store.answers.start(parts[2],parts[4],self.body_json()))
        if parts==['v1','conversations']:
            if self.command=='GET':
                parameters=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                return self.send_json(200,store.knowledge.conversations(parameters.get('before',[None])[-1]))
            if self.command=='POST':self.body_json();return self.send_json(201,store.knowledge.create_conversation())
        if len(parts)==3 and parts[:2]==['v1','conversations']:
            if self.command=='GET':
                parameters=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                return self.send_json(200,store.knowledge.turns(parts[2],parameters.get('before',[None])[-1]))
            if self.command=='POST':return self.send_json(200,store.knowledge.ask(parts[2],self.body_json()))
        if parts==['v1','notes'] and self.command=='POST':
            return self.send_json(201,store.knowledge.save_note(self.body_json()))
        if len(parts)==3 and parts[:2]==['v1','notes']:
            if self.command=='GET':return self.send_json(200,store.knowledge.note(parts[2]))
            if self.command=='PUT':return self.send_json(200,store.knowledge.save_note(self.body_json(),parts[2]))
        if parts[:2] == ['v1', 'library']:
            query = {k:v[-1] for k,v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
            if parts == ['v1','library','overview'] and self.command == 'GET':
                return self.send_json(200, store.catalog.overview())
            if len(parts) == 3 and parts[2] in ('recording','memory','daily','note') and self.command == 'GET':
                return self.send_json(200, store.catalog.query(parts[2],query))
            if len(parts) == 4 and parts[2] in ('recording','memory','daily','note') and parts[3] == 'facets' and self.command == 'GET':
                return self.send_json(200, store.catalog.facets(parts[2]))
            if len(parts) == 4 and parts[2] in ('recording','memory','note') and parts[3] == 'categories' and self.command == 'POST':
                b=self.body_json()
                return self.send_json(201, store.catalog.save_category(parts[2],b.get('name')))
            if len(parts) == 5 and parts[2] in ('recording','memory','note') and parts[3] == 'categories' and self.command == 'PUT':
                b=self.body_json()
                return self.send_json(200, store.catalog.save_category(parts[2],b.get('name'),parts[4],b.get('revision')))
            if len(parts) == 4 and parts[2] in ('recording','memory','note'):
                if self.command == 'GET':return self.send_json(200, store.catalog.detail(parts[2],parts[3]))
                if self.command == 'PUT':return self.send_json(200, store.catalog.update(parts[2],parts[3],self.body_json()))
            raise StoreError(404, '找不到这个资料操作。')
        if parts == ['v1', 'status'] and self.command == 'GET':
            return self.send_json(200, store.status())
        if parts == ['v1', 'daily-settings']:
            if self.command == 'GET':
                return self.send_json(200, store.daily_settings())
            if self.command == 'PUT':
                if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                    raise StoreError(403, '请在节点所在电脑设置每日整理。')
                return self.send_json(200, store.configure_daily(self.body_json()))
        if parts == ['v1', 'settings'] and self.command == 'PUT':
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise StoreError(403, '请在节点所在电脑设置归档目录和时区。')
            return self.send_json(200, store.configure(self.body_json()))
        if parts == ['v1', 'recordings'] and self.command == 'GET':
            return self.send_json(200, store.list_recordings())
        if len(parts) >= 3 and parts[:2] == ['v1', 'recordings']:
            rid = canonical_id(parts[2])
            if len(parts) == 3 and self.command == 'PUT':
                return self.receive_audio(rid)
            if len(parts) == 4 and parts[3] == 'receipt' and self.command == 'GET':
                return self.send_json(200, store.receipt(rid, verify_files=self.headers.get('X-Recording-Node-ID') is not None))
            if len(parts) == 4 and parts[3] == 'audio' and self.command == 'GET':
                p = store.audio_path(rid)
                self.send_response(200)
                self.send_header('Content-Type', 'audio/x-caf')
                self.send_header('Content-Length', str(p.stat().st_size))
                self.send_header('Cache-Control', 'private, no-store')
                self.end_headers()
                with p.open('rb') as audio:
                    for block in iter(lambda: audio.read(1024 * 1024), b''):
                        self.wfile.write(block)
                return
            if len(parts) == 4 and parts[3] == 'transcript':
                store.receipt(rid)
                if self.command == 'GET':
                    return self.send_json(200, store.read_document('transcripts/' + rid + '.md'))
                if self.command == 'PUT':
                    b = self.body_json()
                    return self.send_json(200, store.save_transcript(rid, b.get('text'), b.get('etag')))
        if parts == ['v1', 'daily'] and self.command == 'GET':
            return self.send_json(200, store.daily_list())
        if len(parts) == 3 and parts[:2] == ['v1', 'daily']:
            day = parts[2]
            if not __import__('re').fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                raise StoreError(400, '日期不正确。')
            if self.command == 'POST':
                self.body_json()
                return self.send_json(200, store.build_daily(day))
            if self.command == 'GET':
                return self.send_json(200, store.read_document('daily/' + day + '.md'))
            if self.command == 'PUT':
                b = self.body_json()
                return self.send_json(200, store.edit_document('daily/' + day + '.md', b.get('text'), b.get('etag')))
        if parts == ['v1', 'memory']:
            if self.command == 'GET':
                return self.send_json(200, store.read_document('memory.md'))
            if self.command == 'PUT':
                b = self.body_json()
                return self.send_json(200, store.edit_document('memory.md', b.get('text'), b.get('etag')))
        if parts == ['v1', 'candidates']:
            if self.command == 'GET':
                return self.send_json(200, store.candidates())
            if self.command == 'POST':
                b = self.body_json()
                return self.send_json(201, store.add_candidate(b.get('text'), b.get('sources'), b.get('evidence'), b.get('requestID')))
        if len(parts) == 3 and parts[:2] == ['v1', 'candidates']:
            if self.command == 'GET':
                return self.send_json(200, store.memories.detail(parts[2]))
            if self.command == 'PUT':
                values = self.body_json()
                if 'memoryRevision' not in values and set(values) == {'state'}:
                    return self.send_json(200, store.decide_candidate(parts[2], values['state']))
                return self.send_json(200, store.memories.update(parts[2], values))
        if len(parts) == 4 and parts[:2] == ['v1', 'candidates'] and parts[3] == 'history' and self.command == 'GET':
            parameters = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return self.send_json(200, store.memories.history(parts[2], parameters.get('before', [None])[-1]))
        if len(parts) == 4 and parts[:2] == ['v1', 'candidates'] and parts[3] == 'resolve' and self.command == 'POST':
            return self.send_json(200, store.memories.resolve(parts[2], self.body_json()))
        if len(parts) == 4 and parts[:2] == ['v1', 'candidates'] and parts[3] == 'related' and self.command == 'GET':
            parameters = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, keep_blank_values=True)
            return self.send_json(200, store.memories.related(parts[2], parameters.get('cursor', [None])[-1]))
        if len(parts) == 3 and parts[:2] == ['v1', 'prompts'] and parts[2] in ('daily-summary', 'memory-extraction'):
            relative = 'prompts/' + parts[2] + '.md'
            if self.command == 'GET':
                return self.send_json(200, store.read_document(relative))
            if self.command == 'PUT':
                b = self.body_json()
                return self.send_json(200, store.edit_document(relative, b.get('text'), b.get('etag')))
        raise StoreError(404, '找不到这个操作。')

    def receive_audio(self, rid, repair=None):
        prepared = self.headers.get('X-Recording-Upload')
        encoded = self.headers.get('X-Recording-Manifest', '')
        if len(encoded) > 24000 or (prepared and encoded):
            raise StoreError(400, '录音信息过大。')
        try:
            if repair:
                row=self.server.store.recording_repairs.row(repair)
                document=json.loads(row['document'])
                if row['recording_id']!=canonical_id(rid) or document['nodeID']!=self.server.store.setting('nodeID'):
                    raise StoreError(409,'修复资料库已变化，未接收原音。')
                manifest=document['manifest']
            else:manifest = self.server.store.prepared_recording_upload(rid,prepared) if prepared else json.loads(base64.b64decode(encoded, validate=True))
            size = int(self.headers.get('Content-Length', '-1'))
        except (ValueError, UnicodeError):
            raise StoreError(400, '录音请求格式不正确。')
        validate_manifest(manifest, rid)
        if size != manifest['recording']['byteCount'] or size <= 0 or size > MAX_AUDIO:
            raise StoreError(413, '音频大小不正确或超过节点限制。')
        if not self.server.upload_slots.acquire(blocking=False):
            raise StoreError(503, '节点正在接收其他音频，请稍后重试。')
        temp = None
        try:
            def receive_into(out):
                remaining = size
                while remaining:
                    block = self.rfile.read(min(1024 * 1024, remaining))
                    if not block:raise StoreError(400, '音频未传输完整。')
                    out.write(block);remaining -= len(block)
                out.flush();os.fsync(out.fileno())
            if repair:
                try:
                    with self.server.store.recording_repairs.incoming() as source:
                        with os.fdopen(os.dup(source),'wb') as out:receive_into(out)
                        receipt=self.server.store.recording_repairs.accept(rid,repair,source)
                except OSError:raise StoreError(503,'接收文件尚未修复完成，请检查电脑存储后继续同次修复。')
            else:
                fd,temp=tempfile.mkstemp(dir=self.server.store.staging,prefix='upload-')
                with os.fdopen(fd,'wb') as out:receive_into(out)
                receipt=self.server.store.accept(manifest,temp)
            if prepared:
                with self.server.store.lock:
                    try:(self.server.store.staging/'recording-manifests'/(prepared+'.json')).unlink(missing_ok=True)
                    except OSError:pass  # A cache cleanup failure does not undo durable audio reception.
            self.send_json(200, receipt)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
            self.server.upload_slots.release()

    def route(self):
        with self.server.requests_condition:
            accepted = not self.server.closing
            if accepted:self.server.active_requests += 1
        if not accepted:
            self.close_connection = True
            try:self.send_json(503, {'error':'资料库正在停止，请稍后重试。'})
            except (OSError, TimeoutError):pass
            return
        try:
            self.dispatch()
        except StoreError as e:
            self.close_connection = True
            self.send_json(e.status, {'error': str(e), **({'path': e.path} if e.path else {})})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self.send_json(500, {'error': '节点暂时无法完成操作，原有资料保留。'})
        finally:
            with self.server.requests_condition:
                self.server.active_requests -= 1
                self.server.requests_condition.notify_all()

    do_GET = do_PUT = do_POST = route


def load_token(data):
    path = Path(data) / 'connection-token'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(32));f.flush();os.fsync(f.fileno())
    token = path.read_text().strip()
    if len(token) < 24:
        raise RuntimeError('节点口令文件无效。')
    return token


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', required=True)
    p.add_argument('--output')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8765)
    args = p.parse_args()
    data_dir = Path(args.data).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    instance_lock = open(data_dir / 'instance.lock', 'a')
    try:
        fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('This data directory already has a running Voice Memory node.')
    # A blocked local file read must leave a useful startup diagnostic. Tracebacks
    # contain code locations only, never document text, request bodies or keys.
    faulthandler.dump_traceback_later(10, repeat=False)
    try:
        store = Store(args.data, args.output)
    finally:
        faulthandler.cancel_dump_traceback_later()
    server = Server((args.host, args.port), store, load_token(store.data))
    store.local_sources.resume_jobs()
    stopped = threading.Event()
    def schedule():
        while not stopped.is_set():
            try:
                store.memories.deliver_pending()
            except Exception:
                pass  # File recovery is independent of analysis and source scanning.
            try:
                store.catch_up()
                store.analysis.catch_up()
                store.local_sources.catch_up()
            except Exception:
                pass  # Retry on the next pass; never discard recordings.
            stopped.wait(60)
    scheduler = threading.Thread(target=schedule, daemon=True);scheduler.start()
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print('Voice Memory node ready at %s:%s' % server.server_address, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set();server.server_close();scheduler.join()
        # close() asks every worker to stop and only closes SQLite once all have
        # left their write paths. Retain the instance lock until that is true.
        while not store.close():time.sleep(.1)

if __name__ == '__main__':
    main()
