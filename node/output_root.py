"""Bind archive writes to the selected directory, never recreate a lost mount."""
import json
import os
from pathlib import Path
import secrets
import stat

SETTING = 'outputRootIdentity'
UNAVAILABLE = '文字资料夹暂不可用。请连接原存储位置后重试；资料搬动后请重新关联，应用不会新建替代目录。'
CHANGED = '文字资料夹已被替换或指向其他位置。请重新关联并核对完整资料，应用未继续写入。'
READ_ONLY = '文字资料夹无法写入。请检查存储连接与文件夹权限，恢复后重试；已有资料保留。'
PARENT_CHANGED = '归档文件的位置在保存期间变化，请重新读取后重试。原文件和处理记录仍保留。'


class OutputRootError(OSError):
    pass


def saved_identity(db):
    row = db.execute('SELECT value FROM settings WHERE key=?', (SETTING,)).fetchone()
    if row is None:return None
    try:
        value = json.loads(row[0])
        if (not isinstance(value, dict) or set(value) != {'path','device','inode'}
                or not isinstance(value['path'], str)
                or any(type(value[k]) is not int for k in ('device','inode'))):raise ValueError()
        return value
    except (ValueError, TypeError):raise OutputRootError(CHANGED)


def inspect_root(directory, expected=None, writable=True):
    root = Path(directory).absolute()
    try:
        entry = root.lstat()
        if not stat.S_ISDIR(entry.st_mode):raise OutputRootError(CHANGED)
        value = {'path':str(root), 'device':entry.st_dev, 'inode':entry.st_ino}
        if expected is not None and expected != value:raise OutputRootError(CHANGED)
        if not os.access(root, os.R_OK | os.X_OK):raise OutputRootError(UNAVAILABLE)
        if writable and not os.access(root, os.W_OK):raise OutputRootError(READ_ONLY)
        return value
    except OutputRootError:raise
    except OSError:raise OutputRootError(UNAVAILABLE)


def bind_root(db, directory, expected=None):
    value = inspect_root(directory, expected)
    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (SETTING,json.dumps(value, sort_keys=True)))
    return value


def issue(db, directory, writable=True):
    try:inspect_root(directory, saved_identity(db), writable)
    except OutputRootError as error:return str(error)
    return None


class OutputRoot:
    def __init__(self, directory, identity):
        self.root = Path(directory).absolute()
        self.identity = identity

    def check(self, writable=False):
        return inspect_root(self.root, self.identity, writable)

    def check_parent(self, parts, held):
        self.check(True)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current = os.open(self.root, flags)
        try:
            for part in parts:
                child = os.open(part, flags, dir_fd=current)
                os.close(current);current = child
            a,b = os.fstat(current),os.fstat(held)
            if (a.st_dev,a.st_ino) != (b.st_dev,b.st_ino):raise OutputRootError(PARENT_CHANGED)
        except OSError as error:raise OutputRootError(PARENT_CHANGED) from error
        finally:os.close(current)

    def atomic(self, relative, data):
        """Resolve parents through a held directory descriptor, with no symlinks.

        A disconnect can interrupt a write, but cannot make mkdir recreate the
        missing root or redirect a replacement into a new directory at its path.
        """
        parts = Path(relative).parts
        if not parts or any(p in ('.','..','/') for p in parts):raise OutputRootError(CHANGED)
        self.check(True)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.root, flags)
        try:
            entry = os.fstat(fd)
            if (entry.st_dev,entry.st_ino) != (self.identity['device'],self.identity['inode']):raise OutputRootError(CHANGED)
            for part in parts[:-1]:
                self.check(True)
                try:os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:pass
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd);fd = child
            self.check(True)
            name = parts[-1];temporary = '.'+name+'.'+secrets.token_hex(6)+'.tmp'
            file = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(file, 'wb') as stream:
                    stream.write(data);stream.flush();os.fsync(stream.fileno())
                self.check_parent(parts[:-1],fd)
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
                self.check_parent(parts[:-1],fd)
            finally:
                try:os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:pass
        finally:os.close(fd)
