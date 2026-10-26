"""Read an explicitly selected, already moved text folder before changing pointers.

No files are copied, created or rewritten by this module. File descriptors keep
validation inside the selected directory even if a nested path is replaced.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

from backup import BackupError, LIBRARY_ROOTS, MEMORY_FILES, MAX_FILES, MAX_TOTAL, library_file, retained_originals
from document_originals import original_name


def require(value, message):
    if not value:raise BackupError(message)


def output_snapshot(db, objects, directory, previous):
    root=Path(directory).absolute()
    require(root.is_dir() and not root.is_symlink() and str(root)!='/', '请选择已搬动的文字资料夹，不要选择空目录或文件链接。')
    selected_identity=root.lstat()
    root=root.resolve()
    names=('exports','memory_file_operations','memory_delivery','speech_jobs','notes','documents','recordings','local_inputs','local_sources','candidates')
    rows={};data_digest=hashlib.sha256();row_count=0
    keep={'exports':None,'notes':('id',),'documents':('id',),'recordings':('id',),
          'local_inputs':('id','source_id'),'local_sources':('id','provider')}
    for table in names:
        if table not in objects:continue
        kind,sql=objects[table]
        require(kind=='table' and not sql.lstrip().upper().startswith('CREATE VIRTUAL TABLE'), '资料库包含不支持的数据结构。')
        cursor=db.execute('SELECT * FROM '+table+' ORDER BY rowid')
        columns=[x[0] for x in cursor.description]
        rows[table]=[]
        for row in cursor:
            row_count+=1
            require(row_count<=MAX_FILES*4, '资料夹超过当前核对范围，请使用备份还原。')
            data_digest.update(json.dumps([table,row],sort_keys=True,separators=(',',':')).encode())
            if table in keep:
                value=dict(zip(columns,row))
                rows[table].append(value if keep[table] is None else {key:value[key] for key in keep[table]})
    require(not any(r['root']==str(root) for r in rows.get('exports',[])), '这个位置已有不同的文件登记，请核对原资料或从备份恢复到新副本。')
    owned={r['path']:r for r in rows.get('exports',[]) if r['root']==previous}
    expected=set(owned)
    expected.update(r['generated_path'] for r in owned.values() if r['generated_path'])
    for table,folder in [('notes','notes'),('documents','documents'),('recordings','transcripts')]:
        expected.update(folder+'/'+r['id']+'.md' for r in rows.get(table,[]))
    sources={r['id']:r['provider'] for r in rows.get('local_sources',[])}
    for row in rows.get('local_inputs',[]):
        provider=sources.get(row['source_id'])
        require(provider in ('codex','claude'), '资料库的本地来源登记不完整，请先核对。')
        expected.add('sources/'+provider+'/'+row['source_id']+'/inputs/'+row['id']+'.md')
    original_checks={}
    if 'documents' in rows:
        original_checks={'originals/documents/'+original_name(rid,extension):details['original']['sha256']
                         for rid,extension,details in retained_originals(db)}
        expected.update(original_checks)
    require(all(library_file(name) for name in expected), '资料库包含不支持的文字位置，请从备份还原。')
    found={};total=0;visited=0
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    def identity(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    def walk(parent, name, relative):
        nonlocal total,visited
        visited+=1
        require(visited<=MAX_FILES*2, '资料夹超过当前核对范围，请使用备份还原。')
        before=os.stat(name,dir_fd=parent,follow_symlinks=False)
        require(not stat.S_ISLNK(before.st_mode), '文字资料夹包含文件链接，未重新关联。请保留原文件并核对。')
        if stat.S_ISDIR(before.st_mode):
            child=os.open(name,flags,dir_fd=parent)
            try:
                require(identity(os.fstat(child))==identity(before), '资料夹在核对期间变化，请重新选择。')
                for part in sorted(os.listdir(child)):walk(child,part,relative+'/'+part)
                require(identity(os.fstat(child))==identity(before), '资料夹在核对期间变化，请重新选择。')
            finally:os.close(child)
        elif library_file(relative):
            require(stat.S_ISREG(before.st_mode), '文字资料夹含不可读取的文件，未重新关联。')
            total+=before.st_size
            require(len(found)<MAX_FILES and total<=MAX_TOTAL, '资料夹超过当前核对范围，请使用备份还原。')
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
            with os.fdopen(fd,'rb') as stream:
                require(identity(os.fstat(stream.fileno()))==identity(before), '文件在核对期间变化，请重新选择。')
                digest=hashlib.sha256()
                remaining=before.st_size
                while remaining:
                    block=stream.read(min(1024*1024,remaining))
                    require(block, '文件在核对期间变化，请重新选择。')
                    digest.update(block);remaining-=len(block)
                require(not stream.read(1), '文件在核对期间变化，请重新选择。')
                require(identity(os.fstat(stream.fileno()))==identity(before), '文件在核对期间变化，请重新选择。')
            found[relative]=[identity(before),digest.hexdigest()]
        require(identity(os.stat(name,dir_fd=parent,follow_symlinks=False))==identity(before), '文件在核对期间变化，请重新选择。')
    try:
        fd=os.open(str(root),flags)
        try:
            before=os.fstat(fd)
            require(identity(before)==identity(selected_identity), '资料夹在核对期间变化，请重新选择。')
            available=set(os.listdir(fd))
            for name in sorted((LIBRARY_ROOTS|MEMORY_FILES|{'originals'})&available):walk(fd,name,name)
            require(identity(os.fstat(fd))==identity(before) and identity(root.stat())==identity(before), '资料夹在核对期间变化，请重新选择。')
        finally:os.close(fd)
    except OSError:raise BackupError('文字资料夹不可完整读取，请核对权限、连接与文件链接。')
    require(found and expected<=set(found), '所选资料夹缺少已登记的文字或原件，未重新关联。请选择完整搬动后的资料夹。')
    modified={name for name,row in owned.items() if row['last_hash'] and found[name][1]!=row['last_hash']}
    modified.update(name for name,digest in original_checks.items() if found[name][1]!=digest)
    snapshot=hashlib.sha256(json.dumps([str(root),identity(before),found,data_digest.hexdigest()],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'digest':snapshot,'files':len(found),'modifiedFiles':len(modified)}


def relink_output(db, objects, previous, output):
    """Run inside the caller's transaction after its snapshot is revalidated."""
    for table,column in [('exports','root'),('memory_file_operations','root'),('memory_delivery','output_root'),('speech_jobs','output_root')]:
        if table in objects:db.execute('UPDATE '+table+' SET '+column+'=? WHERE '+column+'=?',(output,previous))
    db.execute("UPDATE settings SET value=? WHERE key='outputDirectory'",(output,))
