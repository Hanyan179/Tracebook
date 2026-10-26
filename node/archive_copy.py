"""Copy a complete supported text archive, publishing files without replacement.

The current library pointer changes only after source and destination read-back.
A failed attempt leaves the previous directory active; matching finished copies
can be reused by a retry. Raw audio and credentials stay in the node directory.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from backup import BackupError
from library_relocation import output_snapshot, read_output_tree


class ArchiveCopyError(BackupError):
    def __init__(self, message, path=None):
        super().__init__(message);self.path=path


def require(condition, message, path=None):
    if not condition:raise ArchiveCopyError(message,path)


def identity(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


@contextmanager
def parent_at(root, relative, create=False):
    fd=os.dup(root)
    try:
        for part in relative.split('/')[:-1]:
            if create:
                try:os.mkdir(part,0o700,dir_fd=fd);os.fsync(fd)
                except FileExistsError:pass
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        yield fd,relative.split('/')[-1]
    finally:os.close(fd)


def read_checked(parent, name, info, expected, writer=None, expected_size=None):
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and (info is None or identity(before)==tuple(info)), '文件在复制期间变化，请重新核对。')
        require(expected_size is None or before.st_size==expected_size, '文件在复制期间变化，请重新核对。')
        digest=hashlib.sha256();remaining=before.st_size
        while remaining:
            chunk=stream.read(min(1024*1024,remaining));require(chunk,'文件在复制期间变化，请重新核对。')
            digest.update(chunk);remaining-=len(chunk)
            if writer:writer.write(chunk)
        require(not stream.read(1) and digest.hexdigest()==expected and identity(os.fstat(stream.fileno()))==identity(before)
                and identity(os.stat(name,dir_fd=parent,follow_symlinks=False))==identity(before),
                '文件在复制期间变化，请重新核对。')


def publish_file(source, destination, relative, entry):
    """Return True only for a newly published, fully verified file."""
    with parent_at(source,relative) as (src,name),parent_at(destination,relative,create=True) as (dst,_):
        try:
            read_checked(dst,name,None,entry[1],expected_size=entry[0][2]);return False
        except FileNotFoundError:pass
        temporary='.tracebook-copy-'+uuid.uuid4().hex+'.tmp'
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dst)
        owned=os.fstat(fd)
        try:
            with os.fdopen(fd,'wb') as writer:
                read_checked(src,name,entry[0],entry[1],writer)
                writer.flush();os.fsync(writer.fileno())
            read_checked(dst,temporary,None,entry[1],expected_size=entry[0][2])
            try:os.link(temporary,name,src_dir_fd=dst,dst_dir_fd=dst,follow_symlinks=False)
            except FileExistsError:
                read_checked(dst,name,None,entry[1],expected_size=entry[0][2]);return False
            os.fsync(dst)
            return True
        finally:
            try:
                value=os.stat(temporary,dir_fd=dst,follow_symlinks=False)
                if (value.st_dev,value.st_ino)==(owned.st_dev,owned.st_ino):os.unlink(temporary,dir_fd=dst);os.fsync(dst)
            except FileNotFoundError:pass


def copy_archive(store, destination, request_id, timezone):
    store.require_output(True)
    previous=str(store.output);output=Path(destination).absolute()
    require(not output.is_symlink(),'目标资料夹是文件链接，未复制。请选择实际资料夹。')
    output=output.resolve();source=store.output.resolve()
    require(str(output)!='/' and output!=source and not output.is_relative_to(source) and not source.is_relative_to(output)
            and output!=store.data, '目标不能是当前资料夹、它的父目录或子目录。请选择独立资料夹。')
    if output.is_relative_to(store.data):
        require(output.relative_to(store.data).parts[0] not in ('recordings','staging','imports','document-previews','RestoredLibraries','DisconnectedSources'),
                '目标不能放在资料库的内部工作目录，请选择独立资料夹。')
    objects={row[0]:(row[1],row[2] or '') for row in store.db.execute('SELECT name,type,sql FROM sqlite_master')}
    checked=output_snapshot(store.db,objects,source,previous,allow_registered=True)
    entries=checked['tree']['entries']
    output.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=read_output_tree(output)
    require(target['identity'][:2]!=checked['tree']['identity'][:2], '目标与当前资料夹相同，无需复制。')
    for path,entry in target['entries'].items():
        require(path in entries,'目标包含当前资料库以外的归档文件，未切换。请选择新的资料夹。',path)
        require(entry[1]==entries[path][1],'目标文件已有不同内容，未覆盖，也未切换资料夹。',path)
    owned={row['path'] for row in store.db.execute('SELECT path FROM exports WHERE root=?',(previous,))}
    for row in store.db.execute('SELECT path FROM exports WHERE root=?',(str(output),)):
        require(row['path'] in owned,'目标保留了其他文件登记，未切换。请选择新的资料夹。',row['path'])
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    src=os.open(str(source),flags)
    try:
        dst=os.open(str(output),flags)
        try:
            require(identity(os.fstat(src))==tuple(checked['tree']['identity']) and identity(os.fstat(dst))==tuple(target['identity']),
                    '资料夹在复制前变化，请重新选择。')
            copied=0
            for path,entry in sorted(entries.items()):
                try:copied+=int(publish_file(src,dst,path,entry))
                except (OSError,BackupError) as error:
                    raise ArchiveCopyError(str(error) if isinstance(error,BackupError) else '文件未能完整复制。原目录仍在使用，已完成的副本保留，可重试。',path)
            require(output_snapshot(store.db,objects,source,previous,allow_registered=True)['digest']==checked['digest'],
                    '原资料在复制期间变化，未切换目录。请重新核对后重试。')
            current=read_output_tree(output)
            require(current['identity'][:2]==target['identity'][:2] and set(current['entries'])==set(entries),
                    '目标资料夹在复制期间变化，未切换。请重新核对。')
            for path,entry in entries.items():require(current['entries'][path][1]==entry[1],'目标文件在复制期间变化，未切换。请重新核对。',path)
        finally:os.close(dst)
    finally:os.close(src)
    receipt={'id':request_id,'nodeID':store.setting('nodeID'),'sourceDirectory':previous,'outputDirectory':str(output),'timeZone':timezone,
             'totalFiles':len(entries),'copiedFiles':copied,'reusedFiles':len(entries)-copied}
    with store.db:
        from output_root import bind_root
        bind_root(store.db,output,{'path':str(output),'device':target['identity'][0],'inode':target['identity'][1]})
        store.db.execute('''INSERT OR REPLACE INTO exports(root,path,last_hash,semantic_hash,state,generated_path,updated_at)
            SELECT ?,path,last_hash,semantic_hash,state,generated_path,updated_at FROM exports WHERE root=?''',(str(output),previous))
        for table,column in [('memory_file_operations','root'),('transcript_reviews','root'),('memory_delivery','output_root'),('speech_jobs','output_root')]:
            store.db.execute('UPDATE '+table+' SET '+column+'=? WHERE '+column+'=?',(str(output),previous))
        store.db.execute("UPDATE settings SET value=? WHERE key='outputDirectory'",(str(output),))
        store.db.execute("UPDATE settings SET value=? WHERE key='timeZone'",(timezone,))
        store.db.execute("INSERT OR REPLACE INTO settings VALUES ('archiveCopyReceipt',?)",(json.dumps(receipt),))
        store.db.execute('INSERT INTO settings VALUES (?,?)',('archiveCopy:'+request_id,json.dumps(receipt)))
    return receipt
