"""Loopback HTTPS fixture, never a real model or a production transport override."""
import json
import re
import email.parser
import email.policy
import hashlib
import io
import wave
from pathlib import Path
import ssl
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch


class LocalModelFixture:
    def __init__(self, directory, port=0):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.control = self.root/'response.json'
        if not self.control.exists():
            self.control.write_text(json.dumps({'mode': 'valid', 'text': '合成协议响应：计划下周五回顾，尚未完成。'}))
        self.requests = []
        self.lock = threading.Lock()
        config = self.root/'certificate.cnf'
        config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n'
                          '[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n'
                          'basicConstraints=critical,CA:TRUE\n')
        self.certificate, self.key = self.root/'certificate.pem', self.root/'fixture-private-key.pem'
        subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-days', '2', '-config', str(config), '-keyout', str(self.key),
                        '-out', str(self.certificate)], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        self.key.chmod(0o600)
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                raw_request = self.rfile.read(int(self.headers['Content-Length']))
                if self.path.endswith('/audio/transcriptions'):
                    return self.speech(raw_request)
                body = json.loads(raw_request)
                with fixture.lock:
                    fixture.requests.append({'path': self.path, 'body': body,
                                             'hasAuthorization': self.headers.get('Authorization') == 'Bearer synthetic-model-key'})
                control = json.loads(fixture.control.read_text())
                time.sleep(min(3, max(0, control.get('delay', 0))))
                mode = control.get('mode')
                code = {'unauthorized': 401, 'rate_limit': 429, 'unavailable': 503,
                        'redirect': 307}.get(mode, 200)
                content = json.loads(body['messages'][1]['content'])
                if 'memories' in content:
                    # Protocol-only fixture; no semantic judgment or real model inference.
                    result={'relation':'conflict','reason':control.get('text','合成核对：请检查新旧安排。'),
                            'citations':[{'evidence':side['evidence'][0]['key'],
                                          'quote':side['evidence'][0]['quote'] if mode!='bad_citation' else 'not-supplied'}
                                         for side in content['memories']]}
                    if mode=='empty':result={'relation':'uncertain','reason':'合成核对：依据不足。','citations':[]}
                    if mode=='uncited':result['citations']=result['citations'][:1]
                elif 'question' in content and 'sources' in content:
                    source=content['sources'][0]
                    quote=source['text'][:min(200,len(source['text']))]
                    result={'status':'answered','statements':[{
                        'text':control.get('text','合成回答：请核对所列原文。'),
                        'citations':[{'source':source['id'] if mode!='bad_citation' else 'note/not-supplied',
                                      'quote':quote}]}]}
                    if mode=='empty':result={'status':'insufficient','statements':[]}
                    if mode=='uncited':result['statements'][0]['citations']=[]
                elif 'sources' in content:
                    sources = content['sources']
                    source = next((s for s in sources if s['id'].startswith('note/')), sources[0])
                    result = {key: [] for key in ['overview', 'decisions', 'todos', 'questions', 'memoryCandidates']}
                    result['overview'] = [{'text': control.get('text', '合成协议响应。'),
                                           'sources': [source['id'] if mode != 'bad_citation' else 'note/not-supplied']}]
                    result['memoryCandidates'] = [{'text': '合成待核对建议，不是已确认事实。', 'sources': [source['id']]}]
                elif mode == 'segments':
                    # Deterministic synthetic protocol fixture, not a semantic model evaluation.
                    matches=list(re.finditer(r'本人陈述：[^\r\n]+',content['text']))[:8]
                    result={'candidates': [{
                        'text':'合成分段结果：'+match[0][5:], 'quote':match[0],
                        'subject':'user','basis':'explicit'} for match in matches]}
                else:
                    result = {'candidates': [] if mode == 'empty' else [{
                        'text': control.get('text', '合成待核对建议。'),
                        'quote': content['text'] if mode != 'bad_citation' else 'not-supplied',
                        'subject': 'user', 'basis': 'explicit'}]}
                payload = {'choices': [{'finish_reason': 'length' if mode == 'incomplete' else 'stop',
                                        'message': {'content': json.dumps(result, ensure_ascii=False)}}]}
                raw = b'not-json' if mode == 'malformed' else json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(code)
                if mode == 'redirect':
                    self.send_header('Location', fixture.endpoint+'/must-not-follow')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def speech(self, raw_request):
                message = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                    ('Content-Type: ' + self.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + raw_request)
                body = {}
                for part in message.iter_parts():
                    name = part.get_param('name', header='content-disposition')
                    data = part.get_payload(decode=True)
                    if name == 'file':
                        with wave.open(io.BytesIO(data), 'rb') as audio:
                            body['audio'] = {'frames': audio.getnframes(), 'rate': audio.getframerate(),
                                             'channels': audio.getnchannels(), 'width': audio.getsampwidth(),
                                             'sha256': hashlib.sha256(audio.readframes(audio.getnframes())).hexdigest()}
                        body['filename'] = part.get_filename()
                    else:
                        body[name] = data.decode()
                with fixture.lock:
                    fixture.requests.append({'path': self.path, 'body': body,
                        'hasAuthorization': self.headers.get('Authorization') == 'Bearer synthetic-model-key'})
                control = json.loads(fixture.control.read_text())
                time.sleep(min(3, max(0, control.get('delay', 0))))
                mode = control.get('mode')
                code = {'unauthorized': 401, 'rate_limit': 429, 'unavailable': 503, 'redirect': 307}.get(mode, 200)
                text = '' if mode == 'empty' else control.get('text', 'Synthetic transcript.')
                raw = b'not-json' if mode == 'malformed' else json.dumps({'text': text}).encode()
                self.send_response(code)
                if mode == 'redirect': self.send_header('Location', fixture.endpoint + '/must-not-follow')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.certificate, self.key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.endpoint = 'https://localhost:'+str(self.server.server_address[1])+'/v1'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        # Only this test process trusts its temporary local certificate. Production stays unchanged.
        client_context = ssl.create_default_context(cafile=str(self.certificate))
        original = urllib.request.build_opener
        self.transport = patch('analysis.urllib.request.build_opener',
                               side_effect=lambda *handlers: original(*handlers, urllib.request.HTTPSHandler(context=client_context)))
        self.transport.start()

    def set_response(self, mode='valid', text='合成协议响应：计划下周五回顾，尚未完成。', delay=0):
        self.control.write_text(json.dumps({'mode': mode, 'text': text, 'delay': delay}, ensure_ascii=False))

    def close(self):
        self.transport.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
