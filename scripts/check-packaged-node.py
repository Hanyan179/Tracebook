#!/usr/bin/env python3
"""Boot the actual app-bundled node and exercise a synthetic memory round trip."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid


def synthetic_pdf():
    stream=b'BT /F1 12 Tf 40 740 Td (Synthetic PDF project evidence.) Tj ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data=b'%PDF-1.4\n';offsets=[0]
    for index,obj in enumerate(objects,1):
        offsets.append(len(data));data+=str(index).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start=len(data);data+=b'xref\n0 7\n0000000000 65535 f \n'
    data+=b''.join(('%010d 00000 n \n'%offset).encode() for offset in offsets[1:])
    return data+b'trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    script = args.bundle.resolve() / 'Contents/Resources/server.py'
    runtime = script.parent / 'PythonRuntime/interpreters' / ('macos-' + platform.machine())
    runtime_info = json.loads((script.parent/'PythonRuntime/runtime.json').read_text())
    assert runtime_info['version'] == '3.12.15' and platform.machine() in runtime_info['architectures']
    executable = runtime / 'bin/python3.12'
    assert executable.is_file() and os.access(executable, os.X_OK), 'Missing app-bundled interpreter'
    python = [str(executable), '-E', '-s', '-B', '-X', 'utf8']
    artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='packaged-node-', dir=artifacts) as temporary:
        root = Path(temporary)
        # A developer's Python setup must not change the installed application's runtime.
        poison = root / 'foreign-python'
        poison.mkdir()
        (poison/'sitecustomize.py').write_text('raise RuntimeError("Foreign Python startup was loaded")\n')
        child_environment = os.environ.copy()
        child_environment.update(PYTHONHOME=str(poison), PYTHONPATH=str(poison), PYTHONUSERBASE=str(poison))
        probe = subprocess.run(python + ['-c',
            'import sys,ssl,json;sys.path.insert(0,sys.argv[1]);import compressed_io;'
            'assert compressed_io.pyzstd;data=b"synthetic packaged decoder";'
            'assert compressed_io.pyzstd.decompress(compressed_io.pyzstd.compress(data))==data;'
            'assert ssl.create_default_context().check_hostname;'
            'assert ssl.create_default_context().verify_mode==ssl.CERT_REQUIRED;'
            'assert sys.flags.ignore_environment and sys.flags.no_user_site and sys.flags.dont_write_bytecode;'
            'print(json.dumps({"prefix":sys.prefix,"version":list(sys.version_info[:3])}))', str(script.parent)],
            env=child_environment, capture_output=True, text=True, timeout=15)
        assert probe.returncode == 0, 'Bundled interpreter isolation check failed: ' + probe.stderr[:1000]
        details = json.loads(probe.stdout)
        assert Path(details['prefix']).resolve() == runtime.resolve()
        assert '.'.join(map(str, details['version'])) == runtime_info['version']
        print('Bundled interpreter, isolated imports, compressed reading and TLS validation defaults passed.')
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0)); port = reserved.getsockname()[1]
        with (root / 'runtime.log').open('wb') as log:
            process = subprocess.Popen(python + [str(script), '--data', str(root / 'node'), '--port', str(port)],
                                       stdout=log, stderr=log, env=child_environment)
            try:
                deadline = time.monotonic() + 15
                token = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError('Packaged node exited during startup: ' + (root / 'runtime.log').read_text()[-2500:])
                    try:
                        token = (root / 'node/connection-token').read_text().strip()
                        with urllib.request.urlopen('http://127.0.0.1:' + str(port) + '/health', timeout=1) as response:
                            assert json.load(response)['service'] == 'voice-memory'
                        break
                    except (OSError, ValueError):
                        time.sleep(0.2)
                else:
                    raise RuntimeError('Packaged node did not become ready.')
                def request(path, body=None, method=None):
                    req = urllib.request.Request('http://127.0.0.1:' + str(port) + path,
                        data=json.dumps(body).encode() if body is not None else None, method=method,
                        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return json.load(response)
                note = request('/v1/notes', {'title': 'Synthetic package check', 'text': 'Synthetic source text.'})
                source = request('/v1/sources/note/' + note['id'])
                extraction = request('/v1/memory-extractions/note/' + note['id'] + '?version=' + source['contentVersion'])
                assert extraction['status'] == 'idle' and not extraction['configured']
                created = request('/v1/candidates', {'text': 'Synthetic proposal.', 'evidence': [{
                    'kind': 'note', 'id': note['id'], 'contentVersion': source['contentVersion'], 'quote': source['text']}]})
                cid = created['id']
                candidate = request('/v1/candidates/' + cid)
                assert candidate['state'] == 'pending' and candidate['memoryRevision'] == 1
                updated = request('/v1/candidates/' + cid, {'memoryRevision': 1, 'state': 'confirmed'}, 'PUT')
                assert updated['state'] == 'confirmed'
                assert len(request('/v1/candidates/' + cid + '/history')['items']) == 2
                assert (root / 'node/Library/memories' / (cid + '.json')).is_file()
                long_text = 'Synthetic paragraph with source context.\n' * 1500
                long_note = request('/v1/notes', {'title': 'Synthetic long package check', 'text': long_text})
                scope = request('/v1/memory-segments/note/' + long_note['id'])
                assert len(scope['units']) > 1
                assert sum(unit['focusCharacters'] for unit in scope['units']) == len(long_text)
                assert all(unit['characters'] <= 32000 for unit in scope['units'])
                assert all(not unit['reusable'] for unit in scope['units'])
                chat = request('/v1/conversations', {})
                turn = request('/v1/conversations/' + chat['id'], {
                    'query': 'Synthetic source', 'requestID': str(uuid.uuid4()), 'scopes': ['notes']})
                answer_path = '/v1/conversations/' + chat['id'] + '/turns/' + turn['id'] + '/'
                preview = request(answer_path + 'answer-preview', {'sources': ['note/' + note['id']]})
                assert not preview['configured'] and preview['items'][0]['source']['excerpt'] == note['text']
                assert request(answer_path + 'answer')['job'] is None
                preparation=request('/v1/documents/prepare',{'requestID':str(uuid.uuid4()),'files':[
                    {'name':'synthetic-package.pdf','data':base64.b64encode(synthetic_pdf()).decode()}]})
                for _ in range(100):
                    state=request('/v1/documents/'+preparation['id']+'/preparation')
                    if state['state']!='preparing':break
                    time.sleep(.1)
                assert state['state']=='ready' and state['preview']['pdfs'][0]['emptyPages']==[2]
                assert request('/v1/documents/'+preparation['id']+'/commit',{})['added']==1
                pdf=request('/v1/documents')['items'][0]
                assert pdf['documentInfo']['rangesValid'] and pdf['documentInfo']['pageCount']==2
                if sys.platform=='darwin':assert pdf['documentInfo']['parser']=='apple-pdfkit'
                assert 'Synthetic PDF project evidence.' in request('/v1/sources/document/'+pdf['id'])['text']
                print('Packaged node started; memory, segmentation, answer preview and isolated PDF import passed.')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
        backup_cli=script.with_name('backup_cli.py')
        def backup_command(*arguments):
            result=subprocess.run(python + [str(backup_cli),*map(str,arguments)],
                                  capture_output=True,text=True,timeout=30,env=child_environment)
            assert result.returncode==0, 'Packaged backup command failed: '+result.stdout[:1000]
            reply=json.loads(result.stdout);assert reply['ok'];return reply
        bundle_file=root/'library-backup.zip'
        saved=backup_command('create','--data',root/'node','--destination',bundle_file)
        preview=backup_command('inspect','--file',bundle_file,'--scratch',root)
        assert preview['archiveSHA256']==saved['archiveSHA256']
        restored=root/'restored'
        result=backup_command('restore','--file',bundle_file,'--destination',restored,
                              '--sha256',preview['archiveSHA256'],'--scratch',root)
        assert result['receivingPaused'] and not (restored/'connection-token').exists()
        with sqlite3.connect(restored/'node.sqlite3') as db:
            assert db.execute('SELECT count(*) FROM candidates').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM documents').fetchone()[0]==1
            assert db.execute("SELECT value FROM settings WHERE key='memoryDeliveryPaused'").fetchone()[0]=='true'
        assert (restored/'Library/memory.md').read_bytes()==(root/'node/Library/memory.md').read_bytes()
        assert (restored/'Library/memories'/(cid+'.json')).read_bytes()==(root/'node/Library/memories'/(cid+'.json')).read_bytes()
        print('Packaged backup create, inspect and independent restore passed; credentials excluded and automatic work paused.')
        location_cli=script.with_name('library_location_cli.py')
        def location_command(*arguments):
            result=subprocess.run(python + [str(location_cli),*map(str,arguments)],
                                  capture_output=True,text=True,timeout=30,env=child_environment)
            assert result.returncode==0, 'Packaged library selection failed: '+result.stdout[:1000]
            reply=json.loads(result.stdout);assert reply['ok'];return reply
        def text_hashes():
            return {str(path.relative_to(restored/'Library')):hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (restored/'Library').rglob('*') if path.is_file()}
        original_files=text_hashes()
        location=location_command('inspect','--data',restored)
        selected=location_command('prepare','--data',restored,'--snapshot',location['snapshot'])
        assert selected['nodeID']==location['nodeID']
        assert selected['outputDirectory']==str(restored/'Library')
        assert text_hashes()==original_files
        print('Packaged existing-library preview and selection passed; identity and text files preserved.')


if __name__ == '__main__':
    main()
