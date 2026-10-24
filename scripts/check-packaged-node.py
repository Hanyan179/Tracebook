#!/usr/bin/env python3
"""Boot the actual app-bundled node and exercise a synthetic memory round trip."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    script = args.bundle.resolve() / 'Contents/Resources/server.py'
    artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='packaged-node-', dir=artifacts) as temporary:
        root = Path(temporary)
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0)); port = reserved.getsockname()[1]
        with (root / 'runtime.log').open('wb') as log:
            process = subprocess.Popen([sys.executable, '-B', str(script), '--data', str(root / 'node'), '--port', str(port)],
                                       stdout=log, stderr=log)
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
                print('Packaged node started; source, memory history, files and bundled text segmentation passed.')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)


if __name__ == '__main__':
    main()
