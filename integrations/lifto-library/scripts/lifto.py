#!/usr/bin/env python3
"""Read-only Tracebook client. No dependencies, secret output, or redirect following."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def configuration():
    url = os.environ.get('LIFTO_URL', '').rstrip('/')
    parsed = urllib.parse.urlsplit(url)
    try:
        loopback = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        loopback = False
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('LIFTO_URL must be a base address without credentials, path, query or fragment.')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and loopback):
        raise ValueError('Use HTTPS, or HTTP only for a loopback address on this computer.')
    path = Path(os.environ.get('LIFTO_TOKEN_FILE', ''))
    if not path.is_absolute():
        raise ValueError('Set LIFTO_TOKEN_FILE to an absolute private-file path.')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 512 or (os.name != 'nt' and info.st_mode & 0o077):
        raise ValueError('The token file must be a private regular file (mode 600 on macOS/Linux).')
    token = path.read_text().strip()
    if not token.startswith('pl_') or len(token) > 256 or any(c.isspace() for c in token):
        raise ValueError('Use a scoped read credential from External access, not the device connection token.')
    return url, token

def main(argv=None):
    parser = argparse.ArgumentParser(description='Read authorized Tracebook sources. Credentials are never command-line arguments.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('capabilities')
    for name in ('search', 'query'):
        command = commands.add_parser(name)
        command.add_argument('query')
        command.add_argument('--scope', action='append', choices=['notes', 'transcripts', 'daily', 'analyses', 'confirmedMemory', 'localInputs', 'documents'])
        command.add_argument('--from', dest='date_from')
        command.add_argument('--to', dest='date_to')
        command.add_argument('--limit', type=int, default=8)
        command.add_argument('--language', choices=['en', 'zh'], default='en')
    command = commands.add_parser('read')
    command.add_argument('kind', choices=['note', 'recording', 'daily', 'analysis', 'memory', 'input', 'document'])
    command.add_argument('id')
    command.add_argument('--offset', type=int, default=0)
    command.add_argument('--limit', type=int, default=8000)
    command.add_argument('--version')
    args = parser.parse_args(argv)
    try:
        url, token = configuration()
        data = None
        if args.command == 'capabilities':
            route = '/v1/assistant/capabilities'
        elif args.command == 'read':
            params = {'offset': args.offset, 'limit': args.limit}
            if args.version: params['version'] = args.version
            route = '/v1/assistant/items/' + args.kind + '/' + urllib.parse.quote(args.id, safe='') + '?' + urllib.parse.urlencode(params)
        else:
            body = {'query': args.query, 'limit': args.limit, 'language': args.language}
            for key, value in [('scopes', args.scope), ('dateFrom', args.date_from), ('dateTo', args.date_to)]:
                if value is not None: body[key] = value
            data = json.dumps(body).encode()
            route = '/v1/assistant/' + args.command
        request = urllib.request.Request(url + route, data=data, headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        # Do not forward private context or credentials through environment-configured proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024: raise ValueError('Response exceeded the read limit.')
        result = json.loads(raw)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except urllib.error.HTTPError as error:
        messages = {401: 'Credential expired, revoked or invalid.', 403: 'Access is outside the granted read scope.',
                    404: 'Source is unavailable.', 409: 'Source changed; restart reading at offset zero.',
                    429: 'Rate limit reached; retry later.'}
        print(json.dumps({'error': messages.get(error.code, 'Request failed or redirect was refused.'), 'status': error.code}), file=sys.stderr)
    except (ValueError, OSError, urllib.error.URLError):
        # Do not echo exceptions: they can contain local paths or response details.
        print(json.dumps({'error': 'Connection or configuration failed. Check the base address and private token file.'}), file=sys.stderr)
    return 1

if __name__ == '__main__':
    sys.exit(main())
