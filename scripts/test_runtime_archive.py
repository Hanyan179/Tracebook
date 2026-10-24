"""Interpreter downloads must not write outside their disposable build directory."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_node_runtime', ROOT/'scripts/prepare-node-runtime.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class RuntimeArchiveTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'artifacts').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='runtime-archive-test-', dir=ROOT/'artifacts')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root/'output'
        self.output.mkdir()

    def archive(self, entries):
        file = self.root/'fixture.tar.gz'
        with tarfile.open(file, 'w:gz') as archive:
            for name, kind, content in entries:
                info = tarfile.TarInfo(name)
                info.mode = 0o755 if name.endswith('python3.12') else 0o644
                if kind == 'file':
                    data = content.encode(); info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
                else:
                    info.type = tarfile.SYMTYPE if kind == 'link' else tarfile.FIFOTYPE
                    info.linkname = content
                    archive.addfile(info)
        return file

    def rejected(self, entries):
        with self.assertRaises(RuntimeError):
            prepare.unpack_interpreter(self.archive(entries), self.output)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertFalse((self.root/'escaped').exists())

    def test_executable_and_internal_link_survive(self):
        prepare.unpack_interpreter(self.archive([
            ('python/bin/python3.12', 'file', 'synthetic executable'),
            ('python/bin/python3', 'link', 'python3.12'),
        ]), self.output)
        self.assertEqual((self.output/'python/bin/python3').read_text(), 'synthetic executable')
        self.assertEqual((self.output/'python/bin/python3.12').stat().st_mode & 0o777, 0o755)

    def test_traversal_and_absolute_paths_rejected_before_writes(self):
        for path in ('../escaped', '/escaped', 'python/../escaped'):
            with self.subTest(path=path):
                self.rejected([('python/safe', 'file', 'safe'), (path, 'file', 'bad')])

    def test_external_link_and_write_beneath_link_rejected(self):
        self.rejected([('python/link', 'link', '../../escaped')])
        self.rejected([('python/safe', 'file', 'safe'), ('python/link', 'link', 'safe'),
                       ('python/link/nested', 'file', 'bad')])

    def test_duplicate_and_case_colliding_paths_rejected(self):
        self.rejected([('python/FILE', 'file', 'one'), ('python/FILE', 'file', 'two')])
        self.rejected([('python/FILE', 'file', 'one'), ('python/file', 'file', 'two')])

    def test_special_files_rejected(self):
        self.rejected([('python/pipe', 'fifo', '')])


if __name__ == '__main__':
    unittest.main()
