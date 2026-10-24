#!/usr/bin/env python3
"""Fetch pinned, hash-verified dependencies into the ignored Mac build runtime."""
import hashlib
import io
import json
from pathlib import Path
import posixpath
import shutil
import tarfile
import tempfile
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1] / 'DerivedData' / 'PythonRuntime'
WHEELS = {
    'arm64': ('https://files.pythonhosted.org/packages/1d/71/ce855ceff6f0f211102bd728862a13bcdb591b05024ce7aa62c6abcc156d/pyzstd-0.18.0-cp39-cp39-macosx_11_0_arm64.whl',
              'c7ee6747541594a5851bae720d5ab070ba9ef644df779507f35819ea61fd83fd'),
    'x86_64': ('https://files.pythonhosted.org/packages/bb/5f/62ffaa3b85d584ff7517780c186ed90112846b72d160c23e98063eb44093/pyzstd-0.18.0-cp39-cp39-macosx_10_9_x86_64.whl',
               '9bccd16621016b83c2d5d40408806a841bbca2860370dca5ef0e3db005417aca'),
}

PYTHON_VERSION = '3.12.15'
PYTHON_RELEASE = '20261021'
INTERPRETERS = {
    'arm64': ('aarch64', '7b8d00b751db21cea87dc551a8f8165147c5fbc5251d5537d7e5497dde00a219'),
    'x86_64': ('x86_64', 'f12e0504397683d6be0745ffd4c39ef45a455cec169adf35c628840d06133773'),
}
CURRENT_WHEELS = {
    'arm64': ('https://files.pythonhosted.org/packages/24/97/b01f76d7a9d7237d9b1dee94469d638575268635ba22c1d07acc23ee3ead/pyzstd-0.18.0-cp312-cp312-macosx_11_0_arm64.whl',
              'f78d6ef80d2f355b5bc1a897e9aa58659e85170b3fa268f3211c4979c768264c'),
    'x86_64': ('https://files.pythonhosted.org/packages/8f/19/7c78cf4cedb812362bb77d0ad5c7e0fa843a344d5d5737a55dd1c1c2b987/pyzstd-0.18.0-cp312-cp312-macosx_10_13_x86_64.whl',
               '5eef13ee3e230e50c01b288d581664e8758f7b831271f6f32cfc29823a6ab365'),
}


def unpack_interpreter(archive_path, destination):
    """Validate the complete pinned archive before writing. Links stay inside it."""
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 20000 or sum(item.size for item in members) > 256 * 1024 * 1024:
            raise RuntimeError('Interpreter archive exceeds the supported size')
        names = {item.name.rstrip('/') for item in members}
        if len(names) != len(members) or len({name.casefold() for name in names}) != len(names):
            raise RuntimeError('Duplicate interpreter archive path')
        for item in members:
            name = item.name.rstrip('/')
            if (posixpath.normpath(name) != name or not (name == 'python' or name.startswith('python/'))
                    or not (item.isdir() or item.isfile() or item.issym()) or item.issparse()):
                raise RuntimeError('Unsafe interpreter archive path or entry')
            if item.issym():
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), item.linkname))
                if item.linkname.startswith('/') or target not in names or not target.startswith('python/'):
                    raise RuntimeError('Interpreter link escapes the runtime')
                if any(other.startswith(name + '/') for other in names):
                    raise RuntimeError('Interpreter archive writes beneath a link')
        for item in members:
            if item.issym():
                continue
            target = destination / item.name
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(item.mode & 0o777)
        for item in members:
            if item.issym():
                (destination / item.name).symlink_to(item.linkname)


def prepare_interpreter(architecture, triple, digest):
    target = ROOT / 'interpreters' / ('macos-' + architecture)
    marker = target / '.verified-interpreter'
    licenses = Path(__file__).resolve().parents[1] / 'vendor/python-licenses' / PYTHON_RELEASE
    if not (licenses / 'LICENSE.cpython.txt').is_file():
        raise RuntimeError('Missing interpreter redistribution notices')
    if marker.exists() and marker.read_text() == digest and (target / 'bin/python3.12').is_file():
        shutil.copytree(licenses, target / 'licenses', dirs_exist_ok=True)
        print(architecture + ': interpreter ready')
        return
    cache = ROOT.parent / 'PythonDownloads'
    cache.mkdir(parents=True, exist_ok=True)
    name = 'cpython-' + PYTHON_VERSION + '+' + PYTHON_RELEASE + '-' + triple + '-apple-darwin-install_only_stripped.tar.gz'
    downloaded = cache / name
    if not downloaded.is_file() or hashlib.sha256(downloaded.read_bytes()).hexdigest() != digest:
        url = 'https://github.com/astral-sh/python-build-standalone/releases/download/' + PYTHON_RELEASE + '/' + name
        temporary = downloaded.with_suffix('.download')
        try:
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
                size = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 128 * 1024 * 1024:
                        raise RuntimeError('Interpreter download exceeds the supported size')
                    output.write(chunk)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                raise RuntimeError('Interpreter checksum mismatch')
            temporary.replace(downloaded)
        finally:
            temporary.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='interpreter-', dir=target.parent) as temporary:
        work = Path(temporary)
        unpack_interpreter(downloaded, work)
        prepared = work / 'python'
        shutil.copytree(licenses, prepared / 'licenses', dirs_exist_ok=True)
        (prepared / '.verified-interpreter').write_text(digest)
        old = target.with_name(target.name + '.previous-' + uuid.uuid4().hex)
        if target.exists():
            target.rename(old)
        try:
            prepared.rename(target)
        except BaseException:
            if old.exists():
                old.rename(target)
            raise
        if old.exists():
            shutil.rmtree(old)
    print(architecture + ': verified interpreter prepared with redistribution notices')


COMMON = ('https://files.pythonhosted.org/packages/80/6c/9eb6d93ad8b9ac96cba13e6d2f419c316d8fdb91013e12dd478a99501699/uniseg-0.10.1-py3-none-any.whl',
          '74fe3f7b4ee46bbd703a70ea1e5cd28e04f63eee5af9a10388d93316c554e358')
PDF = ('https://files.pythonhosted.org/packages/1b/4d/c9d2d67f204eff463237a1a8f5e25a6923b7b1043982b91fcaf104c8ef60/pypdf-6.21.2-py3-none-any.whl',
       '1d44d1a3a200f25062adff0085b02040e4646431ac2ccac22e42679e91466084')
TYPING = ('https://files.pythonhosted.org/packages/18/67/36e9267722cc04a6b9f15c7f3441c2363321a3ea07da7ae0c0707beb2a9c/typing_extensions-4.15.0-py3-none-any.whl',
          'f0fa19c6845758ab08074a0cfa8b7aecb71c999ca73d62883bc25cc018c4e548')


def prepare(target, url, digest, package, label):
    marker = target / ('.verified-wheel-' + package)
    if marker.exists() and marker.read_text() == digest and (target / package).exists():
        print(label + ': dependency ready')
        return
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(16 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError('Dependency wheel checksum mismatch')
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            path = target / entry.filename
            if target.resolve() not in path.resolve().parents:
                raise RuntimeError('Unsafe wheel member')
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(entry))
    marker.write_text(digest)
    print(label + ': prepared with bundled license notices')


def main():
    for architecture, (url, digest) in WHEELS.items():
        prepare(ROOT / ('macos-cp39-' + architecture), url, digest, 'pyzstd', architecture)
    for architecture, (url, digest) in CURRENT_WHEELS.items():
        prepare(ROOT / ('macos-cp312-' + architecture), url, digest, 'pyzstd', architecture + ' CPython 3.12')
    prepare(ROOT / 'common', *COMMON, 'uniseg', 'Unicode segmentation')
    prepare(ROOT / 'common', *PDF, 'pypdf', 'PDF text extraction')
    prepare(ROOT / 'common', *TYPING, 'typing_extensions.py', 'Python 3.9 typing compatibility')
    for architecture, (triple, digest) in INTERPRETERS.items():
        prepare_interpreter(architecture, triple, digest)
    (ROOT / 'runtime.json').write_text(json.dumps({'version': PYTHON_VERSION, 'release': PYTHON_RELEASE,
        'executable': 'bin/python3.12', 'architectures': list(INTERPRETERS)}, sort_keys=True))


if __name__ == '__main__':
    main()
