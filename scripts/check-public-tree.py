#!/usr/bin/env python3
"""Check the Git index, never print matching secrets or inspect ignored data."""
from pathlib import PurePosixPath
import re
import subprocess
import sys
import urllib.parse


def git(*args):
    return subprocess.check_output(['git', *args])


def main():
    paths = [p.decode() for p in git('ls-files', '-z').split(b'\0') if p]
    if not paths:
        print('No files in the Git index; stage the intended public files first.', file=sys.stderr)
        return 1
    names = set(paths)
    failures = []
    private_names = {'AGENTS.md', 'design-qa.md', 'docs/HANDOFF.md', 'Config/Local.xcconfig'}
    public_docs = {'docs/architecture.md', 'docs/development.md', 'docs/open-source-research.md',
                   'docs/auday-experience.md', 'docs/website-brief.md'}
    private_suffixes = {'.db', '.sqlite', '.sqlite3', '.mobileprovision', '.p12', '.p8', '.key',
                        '.caf', '.m4a', '.wav', '.mp3'}
    patterns = {
        'private absolute path': re.compile(r'/(?:Users|home)/[^/\s]+/|/var/(?:folders)/'),
        'private key material': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
        'credential-shaped text': re.compile(r'(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|'
                                            r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|'
                                            r'AKIA[0-9A-Z]{16})'),
    }
    for name in paths:
        path = PurePosixPath(name)
        if (name in private_names or (name.startswith('docs/') and name not in public_docs)
                or any(p in {'DerivedData', '.build', '.venv', '__pycache__', 'xcuserdata'} for p in path.parts)
                or (name.startswith('artifacts/') and name != 'artifacts/.gitkeep')
                or (path.name.startswith('.env') and path.name != '.env.example')
                or path.suffix in private_suffixes or path.name in {'connection-token', 'auth.json'}
                or path.name.endswith(('.sqlite3-wal', '.sqlite3-shm'))):
            failures.append((name, 'private/runtime file'))
        data = git('show', ':' + name)
        if len(data) > 10 * 1024 * 1024:
            failures.append((name, 'unexpected large file'))
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            if path.suffix != '.png':
                failures.append((name, 'unexpected binary file'))
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for label, expression in patterns.items():
                for match in expression.finditer(line):
                    # Deliberately invalid alphabetical fixture for credential-filter tests.
                    if (name in {'node/tests/test_local_sources.py', 'node/tests/test_documents.py'}
                            and match.group() == 'sk-proj-' + 'abcdefghijklmnopqrstuv'):
                        continue
                    failures.append((name + ':' + str(line_number), label))
        if path.suffix == '.md':
            # Only inline Markdown links, excluding fenced source examples.
            prose = re.sub(r'```[^\n]*\n.*?```', '', text, flags=re.S)
            for target in re.findall(r'\]\(([^)]+)\)', prose):
                target = target.split(' ', 1)[0].strip('<>')
                if not target or target.startswith('#') or urllib.parse.urlsplit(target).scheme:
                    continue
                relative = urllib.parse.unquote(target.split('#', 1)[0])
                parts = []
                for part in (path.parent / relative).parts:
                    if part == '..':
                        if parts:
                            parts.pop()
                    elif part != '.':
                        parts.append(part)
                resolved = '/'.join(parts)
                if resolved not in names and not any(p.startswith(resolved + '/') for p in names):
                    failures.append((name, 'link target absent from public tree: ' + relative))
    for name, reason in failures:
        print(name + ': ' + reason, file=sys.stderr)
    if failures:
        print(str(len(failures)) + ' public-tree findings. No matching values were printed.', file=sys.stderr)
        return 1
    print('Public tree checked: ' + str(len(paths)) + ' indexed files; no known private files, paths or credentials found.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
