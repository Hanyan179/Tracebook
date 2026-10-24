"""Opt-in PDF/DOCX copies. Never replace an existing user-owned file."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import uuid

MAX_ORIGINAL = 2 * 1024 * 1024


class OriginalError(Exception):
    pass


def original_name(identifier, extension):
    try:
        if str(uuid.UUID(identifier)).upper() != identifier or extension not in ('pdf', 'docx'):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise OriginalError('原件编号或格式无效。')
    return identifier + '.' + extension


def original_metadata(identifier, extension, details):
    if not isinstance(details, dict):raise OriginalError('原件校验信息无效，请重新核对导入文件。')
    value = details.get('original')
    if value is None:return None
    original_name(identifier, extension)
    if (not isinstance(value, dict) or set(value) != {'sha256', 'byteCount'}
            or not isinstance(value.get('sha256'), str) or len(value['sha256']) != 64
            or any(c not in '0123456789abcdef' for c in value['sha256'])
            or value['sha256'] != details.get('originalFileSHA256')
            or type(value.get('byteCount')) is not int or not 0 < value['byteCount'] <= MAX_ORIGINAL):
        raise OriginalError('原件校验信息无效，请重新核对导入文件。')
    return value


@contextmanager
def original_directory(output, create=False):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(str(Path(output)), flags)
    try:
        for part in ('originals', 'documents'):
            if create:
                try:os.mkdir(part, 0o700, dir_fd=directory)
                except FileExistsError:pass
                os.fsync(directory)
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory);directory = child
        yield directory
    finally:
        os.close(directory)


def read_at(directory, name, expected):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected['byteCount']:
            raise OriginalError('原件副本已变化，未覆盖现有文件。')
        raw = source.read(MAX_ORIGINAL + 1)
        after = os.fstat(source.fileno())
        if (len(raw) != expected['byteCount'] or hashlib.sha256(raw).hexdigest() != expected['sha256']
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                   (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise OriginalError('原件副本已变化，未覆盖现有文件。')
        return raw


def read_original(output, identifier, extension, details):
    expected = original_metadata(identifier, extension, details)
    if expected is None:raise OriginalError('这份资料没有保留原文件副本。')
    try:
        with original_directory(output) as directory:
            return read_at(directory, original_name(identifier, extension), expected)
    except FileNotFoundError:
        raise OriginalError('原件副本缺失，请重新选择原文件并保留副本。')
    except OSError:
        raise OriginalError('原件副本不可读取，请核对资料夹及文件链接。')


def retain_original(output, identifier, extension, raw, digest):
    name = original_name(identifier, extension)
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_ORIGINAL or hashlib.sha256(raw).hexdigest() != digest:
        raise OriginalError('原件内容与预览不一致，请重新选择文件。')
    expected = {'sha256': digest, 'byteCount': len(raw)}
    try:
        with original_directory(output, create=True) as directory:
            try:
                read_at(directory, name, expected)
                return expected
            except FileNotFoundError:pass
            temporary = '.' + name + '.' + uuid.uuid4().hex + '.tmp'
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as target:
                    target.write(raw);target.flush();os.fsync(target.fileno())
                try:
                    os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                    os.fsync(directory)
                except FileExistsError:
                    read_at(directory, name, expected)
            finally:
                os.unlink(temporary, dir_fd=directory)
            return expected
    except OSError:
        raise OriginalError('原件副本未保存，请核对资料夹权限及文件链接后重试。')


def original_state(output, identifier, extension, details):
    if details.get('original') is None:return {'state': 'not_retained'}
    try:
        expected = original_metadata(identifier, extension, details)
        read_original(output, identifier, extension, details)
        return {'state': 'available', **expected}
    except OriginalError as error:
        return {'state': 'unavailable', 'problem': str(error)}
