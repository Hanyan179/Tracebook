"""Offline, credential-free library backups with a versioned logical database.

Only application-owned records and library file classes are included. SQL from
an archive is never executed; restoration uses the current application's schema.
"""
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import unicodedata
import uuid
import zipfile
from zoneinfo import ZoneInfo
from document_originals import OriginalError, original_metadata, original_name, original_directory

FORMAT = 'tracebook-library-backup'
VERSION = 7
MAX_FILES = 100000
MAX_TOTAL = 256 * 1024 ** 3
MAX_MANIFEST = 16 * 1024 ** 2
MAX_ROW = 32 * 1024 ** 2
TABLES = (
    'settings', 'recordings', 'notes', 'candidates', 'exports', 'documents', 'document_locations',
    'local_sources', 'local_source_files', 'local_inputs', 'local_source_jobs', 'local_source_issues',
    'memory_details', 'memory_versions', 'memory_operations', 'memory_relations', 'memory_delivery',
    'memory_file_operations', 'import_batches', 'note_origins', 'conversations', 'conversation_turns',
    'library_categories', 'library_items', 'library_tags', 'daily_analysis', 'memory_extractions',
    'memory_batches', 'memory_batch_items', 'answer_jobs', 'speech_jobs', 'speech_parts', 'memory_reviews', 'transcript_reviews', 'memory_review_batches',
)
SETTINGS = {'nodeID', 'timeZone', 'outputDirectory', 'dailyEnabled', 'dailyTime', 'dailyLastRun',
            'analysisEnabled', 'speechAutomatic', 'speechAfter', 'memoryDeliveryPaused'}
LIBRARY_ROOTS = {'notes', 'transcripts', 'daily', 'analysis', 'prompts', 'sources', 'documents',
                 'memories', 'organization', '.history', '.generated'}
MEMORY_FILES = {'memory.md', 'memory-inbox.md', 'memory-inactive.md', 'memory-rejected.md', 'memory-archive.md'}


class BackupError(Exception):
    pass


def require(condition, message):
    if not condition:raise BackupError(message)


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def fingerprint(path):
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode), '备份中的文件类型不受支持。')
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def digest_file(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024 * 1024), b''):h.update(block)
    return h.hexdigest()


def sync_directory(path):
    fd=os.open(str(path),os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def safe_name(name):
    require(isinstance(name, str) and 0 < len(name) <= 2048 and '\\' not in name
            and '\0' not in name and not name.startswith('/'), '备份包含不安全的文件位置。')
    parts = name.split('/')
    require(all(part not in ('', '.', '..') and ':' not in part for part in parts), '备份包含不安全的文件位置。')
    return PurePosixPath(name)


def library_file(relative):
    p = safe_name(relative)
    if len(p.parts) == 3 and p.parts[:2] == ('originals', 'documents'):
        try:return original_name(p.stem, p.suffix.lstrip('.')) == p.name
        except OriginalError:return False
    return relative in MEMORY_FILES or (len(p.parts) > 1 and p.parts[0] in LIBRARY_ROOTS
        and p.suffix in ('.md', '.json'))


def archive_file(name):
    p = safe_name(name)
    if p.parts[0] == 'library':return library_file('/'.join(p.parts[1:]))
    if len(p.parts) == 2 and p.parts[0] == 'tables':return p.name in {t+'.jsonl' for t in TABLES}
    if len(p.parts) == 3 and p.parts[:2] == ('node', 'imports'):
        try:return str(uuid.UUID(p.stem)).upper() == p.stem and p.suffix == '.json'
        except ValueError:return False
    if len(p.parts) == 4 and p.parts[:2] == ('node', 'recordings'):
        try:
            rid = str(uuid.UUID(p.parts[2])).upper()
            return rid == p.parts[2] and p.name in (rid+'.caf', 'manifest.json')
        except ValueError:return False
    return False


@contextmanager
def offline(directory, busy_message='资料库仍在运行，请先停止本机节点再备份。'):
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink()
            and (directory/'node.sqlite3').is_file() and not (directory/'node.sqlite3').is_symlink(),
            '找不到可备份的本机资料库。')
    lock = directory/'instance.lock'
    require(not lock.is_symlink(), '资料库锁文件异常，未开始备份。')
    require((directory/'staging').is_dir() and not (directory/'staging').is_symlink(), '资料库暂存位置不可用。')
    with lock.open('a') as handle:
        try:fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:raise BackupError(busy_message)
        yield directory.resolve()


def schema(db):
    return {table:[r[1] for r in db.execute('PRAGMA table_info('+table+')')] for table in TABLES}


def table_names(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not r[0].startswith(('sqlite_', 'library_search'))}


def current_schema(scratch, include_names=False):
    from store import Store
    with tempfile.TemporaryDirectory(prefix='backup-schema-', dir=scratch) as temporary:
        store = Store(Path(temporary)/'node')
        try:return (schema(store.db), table_names(store.db)) if include_names else schema(store.db)
        finally:store.close()


def selected_rows(db, table, output):
    if table == 'settings':
        return (row for row in db.execute('SELECT * FROM settings ORDER BY key') if row[0] in SETTINGS)
    if table in ('exports', 'memory_file_operations', 'transcript_reviews'):
        return db.execute('SELECT * FROM '+table+' WHERE root=? ORDER BY rowid', (str(output),))
    if table == 'import_batches':return db.execute("SELECT * FROM import_batches WHERE state='complete' ORDER BY rowid")
    return db.execute('SELECT * FROM '+table+' ORDER BY rowid')


def retained_originals(db):
    for rid, extension, raw_details in db.execute('SELECT id,format,details FROM documents'):
        details = parse_json(raw_details)
        try:value = original_metadata(rid, extension, details)
        except OriginalError as error:raise BackupError(str(error))
        if value is not None:yield rid, extension, details


def inventory(directory, output, db):
    found = []
    require(output.is_dir() and not output.is_symlink(), '资料文件夹不可用，未创建备份。')
    for name in sorted(LIBRARY_ROOTS | MEMORY_FILES | {'originals'}):
        start = output/name
        if not start.exists() and not start.is_symlink():continue
        paths = [start]
        while paths:
            path = paths.pop()
            require(not path.is_symlink(), '资料文件夹包含符号链接，未创建备份；原文件已保留。')
            if path.is_dir():paths.extend(sorted(path.iterdir(), reverse=True));continue
            relative = path.relative_to(output).as_posix()
            if library_file(relative):found.append(('library/'+relative, path, fingerprint(path)))
    for folder in sorted((directory/'recordings').iterdir()):
        require(not folder.is_symlink(), '原音目录包含符号链接，未创建备份。')
        require(folder.is_dir(), '原音目录包含无法识别的文件，请先核对。')
        try:rid = str(uuid.UUID(folder.name)).upper()
        except ValueError:raise BackupError('原音目录包含无法识别的记录，请先核对。')
        require(folder.name == rid, '原音目录编号异常，未创建备份。')
        for filename in (rid+'.caf', 'manifest.json'):
            path = folder/filename
            if path.exists() or path.is_symlink():
                require(not path.is_symlink(), '原音文件包含符号链接，未创建备份。')
                found.append(('node/recordings/'+rid+'/'+filename, path, fingerprint(path)))
    for row in db.execute("SELECT id FROM import_batches WHERE state='complete'"):
        path = directory/'imports'/(row[0]+'.json')
        require(archive_file('node/imports/'+path.name) and path.is_file() and not path.is_symlink(),
                '已导入的原始聊天文件缺失，未创建备份。')
        found.append(('node/imports/'+path.name, path, fingerprint(path)))
    names = {name for name, _, _ in found}
    expected_originals = {'library/originals/documents/' + original_name(rid, extension)
                          for rid, extension, _ in retained_originals(db)}
    require({name for name in names if name.startswith('library/originals/')} == expected_originals,
            '原件副本缺失或尚未完成登记，请先完成导入并核对资料夹后再备份。')
    for table, folder in [('notes','notes'), ('documents','documents'), ('recordings','transcripts')]:
        for row in db.execute('SELECT id FROM '+table):
            require('library/'+folder+'/'+row[0]+'.md' in names, '部分已归档文字缺失，未创建备份，请先核对资料文件夹。')
    for rid, in db.execute('SELECT id FROM recordings'):
        require('node/recordings/'+rid+'/'+rid+'.caf' in names and 'node/recordings/'+rid+'/manifest.json' in names,
                '部分已接收原音缺失，未创建备份。')
    require(len(found) <= MAX_FILES and sum(info[2] for _,_,info in found) <= MAX_TOTAL, '资料库超过当前备份容量上限。')
    return sorted(found)


def create_backup(directory, destination, progress=lambda _:None):
    destination = Path(destination).absolute()
    require(not destination.exists() and not destination.is_symlink(), '目标已存在，请选择新的备份文件名。')
    require(destination.parent.is_dir(), '备份保存位置不可用。')
    destination = destination.parent.resolve()/destination.name
    created = None
    with offline(directory) as directory, tempfile.TemporaryDirectory(prefix='backup-', dir=directory/'staging') as temporary:
        scratch = Path(temporary)
        source = sqlite3.connect('file:'+str(directory/'node.sqlite3')+'?mode=ro', uri=True)
        db = sqlite3.connect(str(scratch/'snapshot.sqlite3'))
        try:
            source.backup(db)
            require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', '资料库校验未通过，原资料保持不变。')
            expected, names = current_schema(scratch, include_names=True)
            tables = schema(db);require(tables == expected and table_names(db) == names,
                '资料库结构与当前版本不一致，请先用当前应用打开后再备份。')
            output = Path(db.execute("SELECT value FROM settings WHERE key='outputDirectory'").fetchone()[0])
            require(output.is_absolute(), '资料文件夹位置无效。')
            from output_root import issue
            output_problem=issue(db,output,writable=False)
            require(output_problem is None,output_problem or '')
            require(not destination.is_relative_to(directory) and not destination.is_relative_to(output.resolve()),
                    '请把备份保存到资料库和文字资料夹之外。')
            files = inventory(directory, output, db)
            verify_restored(directory, output, db, restored=False)
            manifest = {'format':FORMAT, 'version':VERSION, 'id':str(uuid.uuid4()).upper(), 'createdAt':stamp(),
                'sourceOutputDirectory':str(output), 'tables':{}, 'files':[], 'includesCredentials':False,
                'includesAudio':True, 'encrypted':False, 'externalSourcesIncluded':False,
                'externalDocumentCount':db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],
                'originalDocuments':sum(1 for _ in retained_originals(db)),
                'unconfirmedImportsIncluded':False}
            with destination.open('xb') as target:
                os.chmod(destination, 0o600);created = (destination.stat().st_dev, destination.stat().st_ino)
                with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                    for table in TABLES:
                        name='tables/'+table+'.jsonl';count=0;size=0;h=hashlib.sha256()
                        with archive.open(name, 'w', force_zip64=True) as member:
                            for row in selected_rows(db, table, output):
                                line=json.dumps(list(row), ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()+b'\n'
                                require(len(line)<=MAX_ROW, '一条资料超过当前备份行大小上限。')
                                member.write(line);h.update(line);size+=len(line);count+=1
                        manifest['tables'][table]={'columns':tables[table], 'rows':count}
                        manifest['files'].append({'path':name,'bytes':size,'sha256':h.hexdigest()})
                    for index,(name,path,before) in enumerate(files):
                        require(fingerprint(path)==before, '备份期间资料发生变化，请重新创建备份。')
                        h=hashlib.sha256();size=0
                        with path.open('rb') as src, archive.open(name,'w',force_zip64=True) as member:
                            for block in iter(lambda:src.read(1024*1024),b''):
                                member.write(block);h.update(block);size+=len(block)
                        require(fingerprint(path)==before and size==before[2], '备份期间资料发生变化，请重新创建备份。')
                        manifest['files'].append({'path':name,'bytes':size,'sha256':h.hexdigest()})
                        progress({'phase':'copying','completed':index+1,'total':len(files)})
                    require(inventory(directory,output,db)==files, '备份期间文件清单发生变化，请重新创建备份。')
                    file_info = {item['path']: item for item in manifest['files']}
                    manifest['modifiedOriginalDocuments'] = sum(
                        file_info['library/originals/documents/' + original_name(rid, extension)]['sha256'] != details['original']['sha256']
                        or file_info['library/originals/documents/' + original_name(rid, extension)]['bytes'] != details['original']['byteCount']
                        for rid, extension, details in retained_originals(db))
                    encoded=json.dumps(manifest,ensure_ascii=False,separators=(',',':')).encode()
                    require(len(encoded)<=MAX_MANIFEST, '备份清单过大，请减少单个资料库中的文件数量。')
                    archive.writestr('manifest.json',encoded)
                target.flush();os.fsync(target.fileno())
            sync_directory(destination.parent)
            result=inspect_backup(destination,scratch,progress=progress)
            result['path']=str(destination)
            return result
        except BaseException:
            if created and destination.exists():
                now=destination.lstat()
                if (now.st_dev,now.st_ino)==created:destination.unlink()
            raise
        finally:source.close();db.close()


def parse_json(data):
    def invalid(_):raise ValueError('Non-finite value')
    def unique(pairs):
        result={}
        for key,value in pairs:
            require(key not in result, '备份包含重复的字段。');result[key]=value
        return result
    return json.loads(data,parse_constant=invalid,object_pairs_hook=unique)


def inspect_backup(file, scratch, expected_hash=None, progress=lambda _:None):
    file=Path(file);before=fingerprint(file)
    require(not file.is_symlink() and before[2] <= MAX_TOTAL, '备份文件不可读取或超过容量上限。')
    digest=digest_file(file)
    require(expected_hash is None or digest==expected_hash, '备份文件在预览后发生变化，请重新选择。')
    with zipfile.ZipFile(file) as archive:
        entries=archive.infolist();require(len(entries)<=MAX_FILES+len(TABLES)+1, '备份文件数量超过上限。')
        canonical=set();by_name={}
        for entry in entries:
            safe_name(entry.filename)
            folded=unicodedata.normalize('NFC',entry.filename).casefold()
            require(folded not in canonical, '备份包含重复或冲突的文件位置。');canonical.add(folded)
            require(not entry.is_dir() and stat.S_IFMT(entry.external_attr>>16) in (0,stat.S_IFREG)
                    and not entry.flag_bits&1 and entry.compress_type in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED),
                    '备份包含不支持的文件类型或加密内容。')
            require(entry.filename=='manifest.json' or archive_file(entry.filename), '备份包含超出资料库范围的文件。')
            by_name[entry.filename]=entry
        require('manifest.json' in by_name and by_name['manifest.json'].file_size<=MAX_MANIFEST, '找不到有效的备份清单。')
        manifest=parse_json(archive.read('manifest.json'))
        require(isinstance(manifest,dict) and manifest.get('format')==FORMAT
                and type(manifest.get('version')) is int and manifest['version'] in (1, 2, 3, 4, 5, 6, VERSION),
                '这不是当前版本支持的迹库备份。')
        require(str(uuid.UUID(manifest.get('id',''))).upper()==manifest['id']
                and dt.datetime.fromisoformat(manifest.get('createdAt','').replace('Z','+00:00')).tzinfo is not None,
                '备份编号或时间无效。')
        tables = tuple(t for t in TABLES if (t != 'memory_reviews' or manifest['version'] >= 3)
                       and (t != 'local_source_issues' or manifest['version'] >= 4)
                       and (t != 'transcript_reviews' or manifest['version'] >= 5)
                       and (t != 'memory_review_batches' or manifest['version'] >= 6))
        require(isinstance(manifest.get('files'),list) and isinstance(manifest.get('tables'),dict)
                and set(manifest['tables'])==set(tables), '备份资料结构不完整。')
        require({name for name in by_name if name.startswith('tables/')} == {'tables/'+table+'.jsonl' for table in tables},
                '备份包含未登记的资料表。')
        require(isinstance(manifest.get('sourceOutputDirectory'),str), '备份缺少原资料夹信息。')
        expected=current_schema(scratch)
        for table in tables:
            spec=manifest['tables'][table]
            require(isinstance(spec,dict) and spec.get('columns')==expected[table]
                    and type(spec.get('rows')) is int and spec['rows']>=0
                    and 'tables/'+table+'.jsonl' in by_name, '备份结构与当前应用不兼容。')
        require({item.get('path') for item in manifest['files'] if isinstance(item,dict)}==set(by_name)-{'manifest.json'}
                and len(manifest['files'])==len(by_name)-1, '备份文件清单不完整或重复。')
        total=0
        for index,item in enumerate(manifest['files']):
            require(type(item.get('bytes')) is int and 0<=item['bytes']<=MAX_TOTAL
                    and isinstance(item.get('sha256'),str) and len(item['sha256'])==64, '备份校验信息无效。')
            entry=by_name[item['path']];total+=item['bytes']
            require(entry.file_size==item['bytes'] and total<=MAX_TOTAL, '备份文件大小与清单不一致。')
            h=hashlib.sha256();size=0
            with archive.open(entry) as member:
                for block in iter(lambda:member.read(1024*1024),b''):
                    size+=len(block);require(size<=item['bytes'], '备份解压内容超过清单大小。');h.update(block)
            require(size==item['bytes'] and h.hexdigest()==item['sha256'], '备份文件校验失败，未恢复任何资料。')
            progress({'phase':'verifying','completed':index+1,'total':len(manifest['files'])})
        for table in tables:
            count=sum(1 for _ in read_table(archive,table,expected[table]))
            require(count==manifest['tables'][table]['rows'], '备份中的资料数量与清单不一致。')
        originals = {}
        for values in read_table(archive, 'documents', expected['documents']):
            row = dict(zip(expected['documents'], values));details = parse_json(row['details'])
            try:metadata = original_metadata(row['id'], row['format'], details)
            except OriginalError as error:raise BackupError(str(error))
            if metadata is not None:
                originals['library/originals/documents/' + original_name(row['id'], row['format'])] = metadata
        require(manifest['version'] != 1 or not originals, '原件副本需要新版备份格式。')
        require(manifest['version'] == 1 or (type(manifest.get('originalDocuments')) is int
                and manifest['originalDocuments'] == len(originals)), '备份中的原件副本与资料登记不一致。')
        require({name for name in by_name if name.startswith('library/originals/')} == set(originals),
                '备份中的原件副本与资料登记不一致。')
        file_info = {item['path']: item for item in manifest['files']}
        modified_originals = sum(file_info[name]['sha256'] != metadata['sha256']
                                 or file_info[name]['bytes'] != metadata['byteCount'] for name, metadata in originals.items())
        require(type(manifest.get('modifiedOriginalDocuments', 0)) is int
                and manifest.get('modifiedOriginalDocuments', 0) == modified_originals,
                '备份中的原件改动数量与文件不一致。')
        require(fingerprint(file)==before, '备份文件在读取期间发生变化，请重新选择。')
    return {'id':manifest['id'],'createdAt':manifest['createdAt'],'archiveSHA256':digest,'files':len(manifest['files']),
            'bytes':total,'recordings':manifest['tables']['recordings']['rows'],'documents':manifest['tables']['documents']['rows'],
            'notes':manifest['tables']['notes']['rows'],'memories':manifest['tables']['candidates']['rows'],
            'modelReviews':manifest['tables'].get('memory_reviews',{}).get('rows',0),
            'localInputs':manifest['tables']['local_inputs']['rows'],
            'sourceIssuePositions':manifest['tables'].get('local_source_issues',{}).get('rows',0),
            'externalDocumentCount':manifest.get('externalDocumentCount',0),'originalDocuments':len(originals),
            'modifiedOriginalDocuments':modified_originals,
            'includesCredentials':False,'encrypted':False}


def read_table(archive, table, columns):
    with archive.open('tables/'+table+'.jsonl') as member:
        while True:
            line=member.readline(MAX_ROW+1)
            if not line:return
            require(len(line)<=MAX_ROW and line.endswith(b'\n'), '备份中的资料行过大或不完整。')
            row=parse_json(line)
            require(isinstance(row,list) and len(row)==len(columns), '备份中的资料列不完整。')
            require(all(value is None or type(value) in (str,int,float) for value in row), '备份中的资料字段类型无效。')
            require(all(not isinstance(value,float) or math.isfinite(value) for value in row), '备份中的数字无效。')
            yield row


def mapped_row(table, columns, row, output, directory, old_output):
    value=dict(zip(columns,row))
    if table=='settings':
        require(value['key'] in SETTINGS, '备份包含未允许的设置。')
        replacements={'outputDirectory':str(output),'nodeID':str(uuid.uuid4()).upper(),
            'dailyEnabled':'false','analysisEnabled':'false','speechAutomatic':'','memoryDeliveryPaused':'true'}
        if value['key'] in replacements:value['value']=replacements[value['key']]
    if table in ('exports','memory_file_operations','transcript_reviews'):
        require(value['root']==old_output, '备份包含其他资料库的文件操作。')
        value['root']=str(output)
    if table in ('memory_file_operations','transcript_reviews') and value['state']!='done':value['state']='blocked'
    if table=='memory_delivery':
        value['output_root']=str(output);value['pending']=1
        value['problem']=None  # Pausing restored projections is intentional, not a failed write.
    if table=='speech_jobs':
        value['output_root']=str(output)
        if value['status'] not in ('ready','conflict'):
            value['status']='failed';value['next_run']=0
            value['problem']='从备份恢复后，请核对服务和原音，再手动重试转写。'
    if table=='local_sources':
        # The archive cannot authorize reading a path on this computer.
        # A new explicit source selection can reconnect the original stable ID.
        identifier=str(uuid.UUID(value['id'])).upper();require(identifier==value['id'], '来源编号无效。')
        value['directory']=str(directory/'DisconnectedSources'/identifier)
        value['automatic']=0;value['problem']='从备份恢复后，请重新选择并预览来源目录。'
    if table=='local_source_jobs' and value['state'] in ('queued','running','pausing'):
        value['state']='paused';value['problem']='从备份恢复后，请重新选择来源再继续。'
    if table in ('memory_batches','memory_review_batches') and value['status'] in ('running','pausing','queued'):
        value['status']='paused';value['problem']='从备份恢复后，请核对范围再继续。'
    if table=='memory_batch_items' and value['status']=='running':value['status']='queued'
    if table in ('memory_extractions','answer_jobs','daily_analysis','memory_reviews') and value['status'] in ('running','queued'):
        value['status']='interrupted';value['problem']='从备份恢复后，请核对来源再重试。'
    return [value[column] for column in columns]


def verify_restored(directory, output, db, restored=True):
    from store import validate_manifest
    require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok', '恢复后的资料库校验未通过。')
    settings=dict(db.execute('SELECT key,value FROM settings'))
    require({'nodeID','timeZone','outputDirectory','dailyEnabled','dailyTime','dailyLastRun'}<=settings.keys(),
            '备份缺少资料库设置。')
    uuid.UUID(settings['nodeID']);ZoneInfo(settings['timeZone'])
    hour,minute=map(int,settings['dailyTime'].split(':'))
    require(0<=hour<24 and 0<=minute<60, '备份的每日整理时间无效。')
    recorded={r[0] for r in db.execute('SELECT id FROM recordings')}
    # Require opening the source library to recover orphan packages before backup.
    # This avoids surprising count changes when the restored copy starts.
    require({p.name for p in (directory/'recordings').iterdir()}==recorded,
            '原音目录有未登记的记录，请先打开本机资料库完成恢复，再创建备份。')
    for rid,checksum,raw in db.execute('SELECT id,checksum,manifest FROM recordings'):
        manifest=parse_json(raw);require(validate_manifest(manifest,rid)==rid, '恢复后的录音编号无效。')
        actual=parse_json((directory/'recordings'/rid/'manifest.json').read_bytes())
        require(actual==manifest, '原音清单与资料库记录不一致。')
        audio=directory/'recordings'/rid/(rid+'.caf')
        require(audio.is_file() and audio.stat().st_size==manifest['recording']['byteCount']
                and digest_file(audio)==checksum==manifest['audioSHA256'], '恢复后的原音校验未通过。')
    for table,folder in [('notes','notes'),('documents','documents'),('recordings','transcripts')]:
        for rid, in db.execute('SELECT id FROM '+table):
            require(str(uuid.UUID(rid)).upper()==rid and (output/folder/(rid+'.md')).is_file(), '恢复后的文字资料缺失。')
    for rid, extension, details in retained_originals(db):
        try:
            with original_directory(output) as folder:
                value = os.stat(original_name(rid, extension), dir_fd=folder, follow_symlinks=False)
                require(stat.S_ISREG(value.st_mode), '原件副本不是普通文件，未创建备份。')
        except OSError:raise BackupError('原件副本缺失或不可读取，请核对资料夹后再备份。')
    inputs=db.execute('SELECT i.id,i.source_id,s.provider FROM local_inputs i LEFT JOIN local_sources s ON i.source_id=s.id')
    for rid,source_id,provider in inputs:
        require(provider in ('codex','claude') and str(uuid.UUID(rid)).upper()==rid
                and str(uuid.UUID(source_id)).upper()==source_id, '来源编号无效。')
        require((output/'sources'/provider/source_id/'inputs'/(rid+'.md')).is_file(), '恢复后的文字资料缺失。')
    for rid, in db.execute("SELECT id FROM import_batches WHERE state='complete'"):
        require(str(uuid.UUID(rid)).upper()==rid and (directory/'imports'/(rid+'.json')).is_file(), '恢复后的原始导入文件缺失。')
    if restored:require(db.execute('SELECT COUNT(*) FROM reader_grants').fetchone()[0]==0, '恢复副本不得保留读取授权。')


def restore_backup(file, destination, expected_hash, scratch, progress=lambda _:None):
    file=Path(file);destination=Path(destination).absolute()
    require(not destination.exists() and not destination.is_symlink(), '恢复目标已存在，请选择新的资料库位置。')
    require(destination.parent.is_dir() and not destination.parent.is_symlink(), '恢复位置不可用。')
    destination=destination.parent.resolve()/destination.name
    summary=inspect_backup(file,scratch,expected_hash=expected_hash,progress=progress)
    require(shutil.disk_usage(destination.parent).free>summary['bytes']+64*1024**2, '磁盘空间不足，未恢复资料。')
    # Claim the new directory exclusively. Do not use rename(), which can replace
    # an empty directory created by someone else between checking and renaming.
    # The completion marker is written last; incomplete copies are never selected.
    destination.mkdir(mode=0o700)
    stage=destination
    identity=(stage.stat().st_dev,stage.stat().st_ino)
    completed=False
    try:
        from store import Store
        store=Store(stage)
        expected=schema(store.db)
        try:
            with zipfile.ZipFile(file) as archive:
                manifest=parse_json(archive.read('manifest.json'))
                with store.db:
                    for table in TABLES:store.db.execute('DELETE FROM '+table)
                    for table in TABLES:
                        if table not in manifest['tables']:continue  # Known older formats have no comparison or scan-position table.
                        columns=expected[table];count=0
                        sql='INSERT INTO '+table+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')'
                        for row in read_table(archive,table,columns):
                            values=mapped_row(table,columns,row,destination/'Library',destination,manifest['sourceOutputDirectory'])
                            store.db.execute(sql,values);count+=1
                        require(count==manifest['tables'][table]['rows'], '备份中的资料数量与清单不一致。')
                    store.db.execute("INSERT OR REPLACE INTO settings VALUES ('memoryDeliveryPaused','true')")
                    # Neither access grants nor transient import approvals cross restores.
                    store.db.execute('DELETE FROM reader_grants')
                    store.db.execute('DELETE FROM document_previews')
                    store.db.execute('DELETE FROM local_source_previews')
                    if store.catalog.trigram:
                        store.db.execute('DELETE FROM library_search')
                        for kind,rid,text in store.db.execute('SELECT kind,id,search_text FROM library_items'):
                            store.db.execute('INSERT INTO library_search(entity,body) VALUES (?,?)',(kind+':'+rid,text))
                for index,item in enumerate(manifest['files']):
                    name=item['path']
                    if name.startswith('tables/'):continue
                    relative=name[8:] if name.startswith('library/') else name[5:]
                    target=(stage/'Library' if name.startswith('library/') else stage)/relative
                    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    # Only default files in this newly-created stage may be replaced.
                    with archive.open(name) as src, target.open('wb') as dst:
                        os.chmod(target,0o600);h=hashlib.sha256();size=0
                        for block in iter(lambda:src.read(1024*1024),b''):
                            size+=len(block);require(size<=item['bytes'], '备份内容大小发生变化。')
                            dst.write(block);h.update(block)
                        dst.flush();os.fsync(dst.fileno())
                    require(size==item['bytes'] and h.hexdigest()==item['sha256'], '备份文件在恢复期间发生变化。')
                    progress({'phase':'restoring','completed':index+1,'total':len(manifest['files'])})
                verify_restored(stage,stage/'Library',store.db)
                require(digest_file(file)==expected_hash, '备份文件在恢复期间发生变化，未启用恢复副本。')
                # Validate every supplied file after reconstruction, including histories.
                for item in manifest['files']:
                    if item['path'].startswith('tables/'):continue
                    relative=item['path'][8:] if item['path'].startswith('library/') else item['path'][5:]
                    target=(stage/'Library' if item['path'].startswith('library/') else stage)/relative
                    require(digest_file(target)==item['sha256'], '恢复后的资料文件校验未通过。')
                marker={'format':FORMAT,'version':VERSION,'backupID':summary['id'],'createdAt':stamp(),
                    'archiveSHA256':expected_hash,'receivingPaused':True,'dataDirectory':str(destination),
                    'counts':{name:summary[name] for name in ('recordings','documents','notes','memories')}}
                marker_path=stage/'restored-library.json'
                with marker_path.open('x') as handle:
                    os.chmod(marker_path,0o600)
                    handle.write(json.dumps(marker,ensure_ascii=False,indent=2));handle.flush();os.fsync(handle.fileno())
                store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:store.close()
        for folder in sorted((p for p in stage.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
            sync_directory(folder)
        sync_directory(stage);sync_directory(destination.parent)
        completed=True
        return {**summary,'directory':str(destination),'receivingPaused':True}
    finally:
        if not completed and stage.exists():
            now=stage.lstat()
            if (now.st_dev,now.st_ino)==identity and not stage.is_symlink():shutil.rmtree(stage)
