"""Inspect an explicitly selected library without opening its source folders.

Selection does not merge libraries or grant permission to resume model requests.
Only the separately confirmed prepare operation pauses background work.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import time
import uuid
from zoneinfo import ZoneInfo

from backup import BackupError, offline, sync_directory

MAX_DATABASE_BYTES = 2 * 1024 ** 3
REQUIRED = {
    'settings': {'key','value'},
    'recordings': {'id','checksum','manifest','received_at'},
    'notes': {'id','title','created_at','updated_at'},
    'candidates': {'id','text','sources','state','created_at','updated_at'},
}


def require(condition, message):
    if not condition:raise BackupError(message)


@contextmanager
def library_database(directory, writable=False):
    root=Path(directory).absolute()
    require(root.is_dir() and not root.is_symlink(), '找不到所选资料库，请连接存储位置或重新选择。')
    root=root.resolve();file=root/'node.sqlite3'
    require(file.is_file() and not file.is_symlink(), '这里没有可打开的迹库资料库。普通文字文件请从资料导入入口加入。')
    before=file.lstat()
    require(stat.S_ISREG(before.st_mode) and 0<before.st_size<=MAX_DATABASE_BYTES, '资料库文件不可读取或超过当前检查范围。')
    db=sqlite3.connect(file.as_uri()+'?mode='+('rw' if writable else 'ro'),uri=True,timeout=1)
    try:
        if not writable:db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        deadline=time.monotonic()+10
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
        objects={row[0]:(row[1],row[2] or '') for row in db.execute('SELECT name,type,sql FROM sqlite_master')}
        require(not any(kind=='trigger' for kind,_ in objects.values()), '资料库包含当前版本不支持的自动操作，未打开。请使用备份还原。')
        for table,columns in REQUIRED.items():
            kind,sql=objects.get(table,('',''))
            require(kind=='table' and not sql.lstrip().upper().startswith('CREATE VIRTUAL TABLE')
                    and columns<={row[1] for row in db.execute('PRAGMA table_info('+table+')')},
                    '这不是当前版本可识别的迹库资料库。')
        after=file.lstat()
        require(not file.is_symlink() and (before.st_dev,before.st_ino)==(after.st_dev,after.st_ino),
                '资料库文件在检查期间被替换，请重新选择。')
        yield root,db,(before.st_dev,before.st_ino),objects
    finally:db.close()


def read_identity(root, db, identity, objects, output_directory=None):
    settings=dict(db.execute("SELECT key,value FROM settings WHERE key IN ('nodeID','timeZone','outputDirectory')"))
    require(set(settings)=={'nodeID','timeZone','outputDirectory'}, '资料库缺少必要的位置或身份信息。')
    node_id=str(uuid.UUID(settings['nodeID']))
    ZoneInfo(settings['timeZone'])
    raw=Path(settings['outputDirectory'])
    require(raw.is_absolute() and str(raw)!='/', '资料库中的文字保存位置无效，请先核对原资料。')
    output=raw.resolve()
    from output_root import issue, inspect_root, OutputRootError
    output_problem=issue(db,raw)
    previous=str(output);relocation=None
    if output_directory is not None and (output_problem or Path(output_directory).is_symlink() or Path(output_directory).resolve()!=output):
        from library_relocation import output_snapshot
        relocation=output_snapshot(db,objects,output_directory,str(raw),allow_registered=Path(output_directory).resolve()==output)
        output=Path(output_directory).resolve()
        try:inspect_root(output)
        except OutputRootError as error:raise BackupError(str(error))
        output_problem=None
    try:output_identity=inspect_root(output) if output_problem is None else None
    except OutputRootError as error:output_identity=None;output_problem=str(error)
    snapshot_input=[str(root),identity,node_id,str(output)]
    snapshot_input.append(output_identity)
    if relocation:snapshot_input.extend([previous,relocation['digest']])
    snapshot=hashlib.sha256(json.dumps(snapshot_input,separators=(',',':')).encode()).hexdigest()
    # File hashing can take longer than a database query; give the following
    # bounded metadata queries their own deadline.
    deadline=time.monotonic()+10
    db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
    counts={}
    for key,table in [('recordings','recordings'),('notes','notes'),('memories','candidates'),('documents','documents'),('localInputs','local_inputs')]:
        if table not in objects:counts[key]=0;continue
        kind,sql=objects[table]
        require(kind=='table' and not sql.lstrip().upper().startswith('CREATE VIRTUAL TABLE'), '资料库包含不支持的数据结构。')
        counts[key]=db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
    pending=db.execute("SELECT COUNT(*) FROM candidates WHERE state='pending'").fetchone()[0]
    receipt=None
    row=db.execute("SELECT value FROM settings WHERE key='archiveCopyReceipt'").fetchone()
    if row and isinstance(row[0],str) and len(row[0])<=8192:
        try:
            saved=json.loads(row[0])
            if (isinstance(saved,dict) and saved.get('nodeID')==settings['nodeID']
                    and saved.get('outputDirectory')==str(raw)
                    and isinstance(saved.get('id'),str)
                    and str(uuid.UUID(saved.get('id',''))).upper()==saved['id']):
                receipt={'id':saved['id'],'outputDirectory':str(raw)}
        except (ValueError,TypeError,KeyError):pass
    return {'id':snapshot,'snapshot':snapshot,'nodeID':settings['nodeID'],'dataDirectory':str(root),
            'outputDirectory':str(output),'outputAvailable':output_problem is None,
            'outputIssue':output_problem,'outputIdentity':output_identity,
            'previousOutputDirectory':str(raw) if relocation else None,
            'relocationFiles':relocation['files'] if relocation else None,
            'modifiedFiles':relocation['modifiedFiles'] if relocation else None,
            'archiveCopyReceipt':receipt,
            'timeZone':settings['timeZone'],'pendingMemories':pending,**counts}


def inspect_library(directory, output_directory=None):
    with library_database(directory) as (root,db,identity,objects):
        db.execute('BEGIN')
        result=read_identity(root,db,identity,objects,output_directory)
        require(db.execute('PRAGMA quick_check(1)').fetchone()[0]=='ok', '资料库检查未通过，请保留原目录并从备份还原。')
        return result


def prepare_library(directory, expected_snapshot, output_directory=None):
    """Pause external work only after explicit library-selection confirmation."""
    with offline(directory) as root, library_database(directory,writable=True) as (_,db,identity,objects):
        with db:
            db.execute('BEGIN IMMEDIATE')
            result=read_identity(root,db,identity,objects,output_directory)
            require(result['snapshot']==expected_snapshot, '资料库、文字位置或文件在核对后变化，请重新核对。')
            require(result['outputAvailable'], '文字资料夹暂不可用，请连接存储位置后重试，或从备份还原。')
            require(db.execute('PRAGMA quick_check(1)').fetchone()[0]=='ok', '资料库检查未通过，请保留原目录并从备份还原。')
            if result['previousOutputDirectory'] is not None:
                from library_relocation import relink_output
                relink_output(db,objects,result['previousOutputDirectory'],result['outputDirectory'])
            from output_root import bind_root, OutputRootError
            try:bind_root(db,result['outputDirectory'],result['outputIdentity'])
            except OutputRootError as error:raise BackupError(str(error))
            for key,value in [('dailyEnabled','false'),('analysisEnabled','false'),('speechAutomatic',''),('memoryDeliveryPaused','true')]:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,value))
            message='切换资料库后，请核对范围与服务再继续。'
            if 'memory_delivery' in objects:
                db.execute("UPDATE memory_delivery SET pending=1")
            if 'memory_file_operations' in objects:
                db.execute("UPDATE memory_file_operations SET state='blocked' WHERE state='pending'")
            if 'transcript_reviews' in objects:
                db.execute("UPDATE transcript_reviews SET state='blocked' WHERE state='pending'")
            if 'speech_jobs' in objects:
                db.execute("UPDATE speech_jobs SET status='failed',next_run=0,problem=? WHERE status NOT IN ('ready','conflict')",(message,))
            if 'local_sources' in objects:db.execute('UPDATE local_sources SET automatic=0')
            if 'local_source_jobs' in objects:
                db.execute("UPDATE local_source_jobs SET state='paused',problem=? WHERE state IN ('queued','running','pausing')",(message,))
            if 'memory_batches' in objects:
                db.execute("UPDATE memory_batches SET status='paused',problem=? WHERE status IN ('queued','running','pausing')",(message,))
            if 'memory_batch_items' in objects:db.execute("UPDATE memory_batch_items SET status='queued' WHERE status='running'")
            for table in ('memory_extractions','answer_jobs','daily_analysis','memory_reviews'):
                if table in objects:db.execute('UPDATE '+table+" SET status='interrupted',problem=? WHERE status IN ('queued','running')",(message,))
        db.execute('PRAGMA wal_checkpoint(FULL)')
        sync_directory(root)
        return {**result,'receivingPaused':True,'automaticProcessingPaused':True}
