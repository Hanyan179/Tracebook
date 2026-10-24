#!/usr/bin/env python3
"""Fetch pinned, hash-verified dependencies into the ignored Mac build runtime."""
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1] / 'DerivedData' / 'PythonRuntime'
WHEELS = {
    'arm64': ('https://files.pythonhosted.org/packages/1d/71/ce855ceff6f0f211102bd728862a13bcdb591b05024ce7aa62c6abcc156d/pyzstd-0.18.0-cp39-cp39-macosx_11_0_arm64.whl',
              'c7ee6747541594a5851bae720d5ab070ba9ef644df779507f35819ea61fd83fd'),
    'x86_64': ('https://files.pythonhosted.org/packages/bb/5f/62ffaa3b85d584ff7517780c186ed90112846b72d160c23e98063eb44093/pyzstd-0.18.0-cp39-cp39-macosx_10_9_x86_64.whl',
               '9bccd16621016b83c2d5d40408806a841bbca2860370dca5ef0e3db005417aca'),
}


COMMON = ('https://files.pythonhosted.org/packages/80/6c/9eb6d93ad8b9ac96cba13e6d2f419c316d8fdb91013e12dd478a99501699/uniseg-0.10.1-py3-none-any.whl',
          '74fe3f7b4ee46bbd703a70ea1e5cd28e04f63eee5af9a10388d93316c554e358')


def prepare(target, url, digest, package, label):
    marker = target / '.verified-wheel'
    if marker.exists() and marker.read_text() == digest and (target / package).is_dir():
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
    prepare(ROOT / 'common', *COMMON, 'uniseg', 'Unicode segmentation')


if __name__ == '__main__':
    main()
