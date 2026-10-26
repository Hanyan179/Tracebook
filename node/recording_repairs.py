"""Explicit repair of registered received audio; original packages are retained."""
from __future__ import annotations
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import json
import os
import stat
import sys
import uuid


def identity(info):
    return [info.st_dev, info.st_ino]


def version(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def rename_exclusive(source_fd, source, target_fd, target):
    # Apple RENAME_EXCL and Linux RENAME_NOREPLACE reject even an empty target.
    # Never fall back to rename(), which can silently replace an existing directory.
    name, flag = ('renameatx_np', 4) if sys.platform == 'darwin' else ('renameat2', 1)
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, name, None)
    if function is None:raise OSError(errno.ENOTSUP, 'Exclusive rename unavailable')
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(source_fd, os.fsencode(source), target_fd, os.fsencode(target), flag) != 0:
        number = ctypes.get_errno();raise OSError(number, os.strerror(number))


def read_file(parent, name, maximum):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:raise ValueError()
        checksum = hashlib.sha256();remaining = before.st_size;metadata=[]
        while remaining:
            data = stream.read(min(remaining, 1024 * 1024))
            if not data:raise ValueError()
            checksum.update(data);remaining -= len(data)
            if name=='manifest.json':metadata.append(data)
        after = os.fstat(stream.fileno())
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if version(before) != version(after) or version(current) != version(after):raise ValueError()
        result={'version':version(after), 'bytes':after.st_size, 'sha256':checksum.hexdigest()}
        if name=='manifest.json':
            try:result['jsonSHA256']=hashlib.sha256(json.dumps(json.loads(b''.join(metadata)),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
            except (ValueError,UnicodeError):result['jsonSHA256']=None
        return result


def package_snapshot(parent, name, rid):
    from store import MAX_AUDIO
    try:descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except FileNotFoundError:return {'directory':None, 'files':{}}
    try:
        before = os.fstat(descriptor);names = os.listdir(descriptor)
        allowed = {rid+'.caf':MAX_AUDIO, 'manifest.json':256*1024}
        if set(names)-set(allowed):raise ValueError()
        files = {filename:read_file(descriptor,filename,allowed[filename]) for filename in names}
        current = os.stat(name,dir_fd=parent,follow_symlinks=False)
        if identity(current)!=identity(before) or sorted(os.listdir(descriptor))!=sorted(names):raise ValueError()
        return {'directory':identity(before), 'files':files}
    finally:os.close(descriptor)


class RecordingRepairs:
    def __init__(self, store):
        self.s, self.db = store, store.db
        self.data_identity = identity(store.data.stat())
        self.db.execute('''CREATE TABLE IF NOT EXISTS recording_repairs(
            id TEXT PRIMARY KEY, recording_id TEXT NOT NULL, document TEXT NOT NULL, state TEXT NOT NULL)''')
        self.db.commit()

    def error(self, text, code=409):
        from store import StoreError
        raise StoreError(code,text)

    @contextmanager
    def roots(self, write=False):
        descriptors=[]
        try:
            root=os.open(self.s.data,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);descriptors.append(root)
            if identity(os.fstat(root))!=self.data_identity:self.error('资料库位置已变化，未修复接收文件。')
            records=os.open('recordings',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root);descriptors.append(records)
            if write:
                try:os.mkdir('recording-repairs',0o700,dir_fd=root);os.fsync(root)
                except FileExistsError:pass
                history=os.open('recording-repairs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root);descriptors.append(history)
            else:history=None
            yield records,history
            if identity(os.stat(self.s.data,follow_symlinks=False))!=self.data_identity:
                self.error('资料库位置已变化，已保留的副本不受影响。')
        finally:
            for descriptor in reversed(descriptors):os.close(descriptor)

    @contextmanager
    def staging_directory(self):
        root=os.open(self.s.data,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            if identity(os.fstat(root))!=self.data_identity:self.error('资料库位置已变化，未修复接收文件。')
            descriptor=os.open('staging',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
            try:yield descriptor
            finally:os.close(descriptor)
        finally:os.close(root)

    @contextmanager
    def incoming(self):
        # Hold an anonymous, anchored file through upload and validation. Replacing
        # the staging path cannot redirect writes or delete an unrelated file.
        with self.staging_directory() as staging:
            name='repair-upload-'+uuid.uuid4().hex
            descriptor=os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600,dir_fd=staging)
            try:
                os.unlink(name,dir_fd=staging)
                yield descriptor
            finally:os.close(descriptor)

    @contextmanager
    def temporary_package(self, rid):
        with self.staging_directory() as staging:
            name='repair-package-'+uuid.uuid4().hex
            os.mkdir(name,0o700,dir_fd=staging)
            parent=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=staging)
            package=None
            try:
                os.mkdir('package',0o700,dir_fd=parent)
                package=os.open('package',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                yield parent,package
            finally:
                if package is not None:
                    # Remove only our unpublished files, never a directory tree.
                    try:
                        if identity(os.stat('package',dir_fd=parent,follow_symlinks=False))==identity(os.fstat(package)):
                            for child in (rid+'.caf','manifest.json'):
                                try:os.unlink(child,dir_fd=package)
                                except OSError:pass
                            try:os.rmdir('package',dir_fd=parent)
                            except OSError:pass
                    except OSError:pass
                    os.close(package)
                os.close(parent)
                try:os.rmdir(name,dir_fd=staging)
                except OSError:pass

    def registered(self, rid, source=None):
        from store import canonical_id, validate_manifest, markers_checksum
        rid=canonical_id(rid);manifest=self.s.manifest(rid);validate_manifest(manifest,rid)
        if source is not None:
            if (type(source.get('byteCount')) is not int or source.get('audioSHA256')!=manifest['audioSHA256'] or
                source.get('byteCount')!=manifest['recording']['byteCount'] or
                source.get('markersSHA256')!=markers_checksum(manifest['recording'])):
                self.error('本机原音或重点标记与接收登记不一致，未修复；两端文件均保留。')
        return rid,manifest

    def token(self, manifest, snapshot):
        raw=json.dumps([self.s.setting('nodeID'),manifest,snapshot],sort_keys=True,separators=(',',':')).encode()
        return hashlib.sha256(raw).hexdigest()

    def pending(self, rid):
        return self.db.execute("SELECT * FROM recording_repairs WHERE recording_id=? AND state IN ('upload','prepared','archived') ORDER BY rowid DESC LIMIT 1",(rid,)).fetchone()

    def state(self, row, document, state, problem=None):
        document['problem']=problem
        with self.db:self.db.execute('UPDATE recording_repairs SET state=?,document=? WHERE id=?',(state,json.dumps(document,ensure_ascii=False),row['id']))
        return self.row(row['id'])

    def row(self, key):
        from store import canonical_id
        row=self.db.execute('SELECT * FROM recording_repairs WHERE id=?',(canonical_id(key),)).fetchone()
        if row is None:self.error('找不到这次原音修复记录。',404)
        return row

    def status(self, rid, key):
        from store import canonical_id
        with self.s.lock:
            row=self.row(key);doc=json.loads(row['document'])
            if row['recording_id']!=canonical_id(rid):self.error('原音修复编号不匹配。')
            return {'requestID':row['id'],'recordingID':row['recording_id'],'nodeID':doc['nodeID'],
                    'state':row['state'],'problem':doc.get('problem'),'completedAt':doc.get('completedAt'),
                    'keptFiles':len(doc['before']['files']), 'keptBytes':sum(f['bytes'] for f in doc['before']['files'].values()),
                    'archivePath':('recording-repairs/'+row['id']+'/original') if doc['before']['directory'] else None}

    def preview(self, rid, source):
        from store import StoreError, markers_checksum
        with self.s.lock:
            rid,manifest=self.registered(rid,source)
            try:
                with self.roots() as (records,_):snapshot=package_snapshot(records,rid,rid)
            except (OSError,ValueError):self.error('接收目录含有额外文件、链接或不可读取内容，请在电脑核对；没有修改文件。')
            healthy=False
            try:self.s.receipt(rid,verify_files=True);healthy=True
            except StoreError:pass
            pending=self.pending(rid);pending_id=None
            token=self.token(manifest,snapshot)
            if pending:
                doc=json.loads(pending['document'])
                if doc['nodeID']==self.s.setting('nodeID') and (pending['state'] in ('prepared','archived') or doc['snapshot']==token):
                    pending_id=pending['id'];token=doc['snapshot']
            audio=snapshot['files'].get(rid+'.caf');metadata=snapshot['files'].get('manifest.json')
            return {'recordingID':rid,'nodeID':self.s.setting('nodeID'),'snapshot':token,'pendingRequestID':pending_id,
                    'title':manifest['recording'].get('title') or '语音记录','byteCount':manifest['recording']['byteCount'],
                    'audioState':'missing' if audio is None else ('matching' if audio['sha256']==manifest['audioSHA256'] else 'changed'),
                    'manifestState':'missing' if metadata is None else ('matching' if metadata.get('jsonSHA256')==hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest() else 'changed'),
                    'keptFiles':len(snapshot['files']),'keptBytes':sum(f['bytes'] for f in snapshot['files'].values()),
                    'canRepair':not healthy,'audioSHA256':manifest['audioSHA256'],'markersSHA256':markers_checksum(manifest['recording'])}

    def prepare(self, rid, key, body):
        from store import canonical_id, stamp
        with self.s.lock:
            rid,manifest=self.registered(rid,body);key=canonical_id(key)
            snapshot=body.get('snapshot')
            if not isinstance(snapshot,str) or len(snapshot)!=64:self.error('请先核对接收文件后再修复。',400)
            row=self.db.execute('SELECT * FROM recording_repairs WHERE id=?',(key,)).fetchone()
            if row:
                doc=json.loads(row['document'])
                if row['recording_id']!=rid or doc['nodeID']!=self.s.setting('nodeID') or doc['snapshot']!=snapshot:
                    self.error('这次修复的资料库或预览已变化，请重新核对。')
                if row['state']=='blocked':self.error(doc.get('problem') or '修复范围已变化，请重新核对。')
                if row['state'] in ('prepared','archived'):self.finish(row)
                return self.status(rid,key)
            preview=self.preview(rid,body)
            if not preview['canRepair']:self.error('接收文件已完整，无需修复。')
            if preview['snapshot']!=snapshot:self.error('接收文件在预览后已变化，请重新核对；原文件未被替换。')
            previous=self.pending(rid)
            if previous:
                old=json.loads(previous['document'])
                if previous['state'] in ('prepared','archived') or old['snapshot']==snapshot:
                    self.error('这条原音已有待完成的修复，请继续同一次处理。')
                self.state(previous,old,'blocked','接收文件已变化，上次预览不再适用。')
            with self.roots() as (records,_):
                before=package_snapshot(records,rid,rid);parent_identity=identity(os.fstat(records))
            if self.token(manifest,before)!=snapshot:self.error('接收文件在预览后已变化，请重新核对；原文件未被替换。')
            doc={'nodeID':self.s.setting('nodeID'),'snapshot':snapshot,'before':before,'manifest':manifest,
                 'dataIdentity':self.data_identity,'recordingsIdentity':parent_identity,'createdAt':stamp()}
            with self.db:self.db.execute('INSERT INTO recording_repairs VALUES (?,?,?,?)',(key,rid,json.dumps(doc,ensure_ascii=False),'upload'))
            return self.status(rid,key)

    def stage(self, row, doc, source, history):
        rid=row['recording_id'];key=row['id'];manifest=doc['manifest']
        try:os.mkdir(key,0o700,dir_fd=history);os.fsync(history)
        except FileExistsError:pass
        operation=os.open(key,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=history)
        try:
            existing=package_snapshot(operation,'replacement',rid)
            if existing['directory']:
                try:self.verify_replacement(operation,'replacement',rid,manifest)
                except Exception:
                    self.state(row,doc,'blocked','待恢复的原音副本已变化，请重新核对；原文件仍保留。')
                    raise
                return identity(os.stat('replacement',dir_fd=operation,follow_symlinks=False))
            with self.temporary_package(rid) as (parent,package):
                checksum=hashlib.sha256();size=0
                descriptor=os.dup(source) if isinstance(source,int) else os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                with os.fdopen(descriptor,'rb') as incoming:
                    incoming.seek(0);before=os.fstat(incoming.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_size!=manifest['recording']['byteCount']:raise ValueError()
                    output=os.open(rid+'.caf',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=package)
                    with os.fdopen(output,'wb') as outgoing:
                        remaining=before.st_size
                        while remaining:
                            block=incoming.read(min(remaining,1024*1024))
                            if not block:raise ValueError()
                            outgoing.write(block);checksum.update(block);size+=len(block);remaining-=len(block)
                        outgoing.flush();os.fsync(outgoing.fileno())
                    if version(os.fstat(incoming.fileno()))!=version(before):raise ValueError()
                if size!=manifest['recording']['byteCount'] or checksum.hexdigest()!=manifest['audioSHA256']:raise ValueError()
                output=os.open('manifest.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=package)
                with os.fdopen(output,'w',encoding='utf-8') as out:
                    out.write(json.dumps(manifest,ensure_ascii=False,indent=2));out.flush();os.fsync(out.fileno())
                os.fsync(package);expected=identity(os.fstat(package))
                rename_exclusive(parent,'package',operation,'replacement');os.fsync(operation)
                return expected
        finally:os.close(operation)

    def verify_replacement(self, parent, name, rid, manifest):
        state=package_snapshot(parent,name,rid)
        raw=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
        if (state['files'].get(rid+'.caf',{}).get('sha256')!=manifest['audioSHA256'] or
            state['files'].get('manifest.json',{}).get('sha256')!=hashlib.sha256(raw).hexdigest()):
            self.error('待恢复的原音副本不完整，原文件仍保留。')
        return state

    def accept(self, rid, key, source):
        from store import canonical_id
        with self.s.lock:
            row=self.row(key);doc=json.loads(row['document']);rid=canonical_id(rid)
            if row['recording_id']!=rid or doc['nodeID']!=self.s.setting('nodeID'):self.error('修复资料库已变化，未接收原音。')
            if row['state']=='done':return self.s.receipt(rid,verify_files=True)
            if row['state']=='blocked':self.error(doc.get('problem') or '修复范围已变化，请重新核对。')
            _,current=self.registered(rid)
            if current!=doc['manifest']:self.error('接收登记已变化，未替换原音。')
            if row['state']=='upload':
                try:
                    with self.roots(write=True) as (records,history):
                        if doc['dataIdentity']!=self.data_identity or doc['recordingsIdentity']!=identity(os.fstat(records)):
                            self.error('资料库位置已变化，未修复接收文件。')
                        before=package_snapshot(records,rid,rid)
                        if self.token(current,before)!=doc['snapshot']:
                            self.state(row,doc,'blocked','接收文件在预览后已变化，请重新核对；原文件未被替换。')
                            self.error('接收文件在预览后已变化，请重新核对；原文件未被替换。')
                        doc['replacementIdentity']=self.stage(row,doc,source,history)
                    row=self.state(row,doc,'prepared')
                except ValueError:self.error('用于修复的原音校验未通过，原文件仍保留。',422)
            self.finish(row)
            return self.s.receipt(rid,verify_files=True)

    def finish(self, row):
        from store import stamp, StoreError, canonical_id
        doc=json.loads(row['document']);rid=canonical_id(row['recording_id']);key=canonical_id(row['id'])
        if doc['nodeID']!=self.s.setting('nodeID'):
            self.state(row,doc,'blocked','从备份恢复后，请重新核对原音再修复。');return
        try:
            _,registered=self.registered(rid)
            if registered!=doc['manifest']:self.error('接收登记已变化，未替换原音。')
            with self.roots(write=True) as (records,history):
                if doc['dataIdentity']!=self.data_identity or doc['recordingsIdentity']!=identity(os.fstat(records)):
                    self.error('资料库位置已变化，未修复接收文件。')
                operation=os.open(key,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=history)
                try:
                    current=package_snapshot(records,rid,rid)
                    if current['directory']==doc['replacementIdentity']:
                        self.s.receipt(rid,verify_files=True)
                    else:
                        self.verify_replacement(operation,'replacement',rid,doc['manifest'])
                        archived=package_snapshot(operation,'original',rid)
                        if archived['directory'] is None and current!=doc['before']:
                            self.error('接收文件在修复期间已变化，请重新核对；旧副本仍保留。')
                        if archived['directory'] is None and doc['before']['directory'] is not None:
                            if current!=doc['before']:self.error('接收文件在修复期间已变化，请重新核对；旧副本仍保留。')
                            rename_exclusive(records,rid,operation,'original');os.fsync(records);os.fsync(operation)
                            try:archived=package_snapshot(operation,'original',rid)
                            except (ValueError,OSError):
                                try:rename_exclusive(operation,'original',records,rid);os.fsync(records)
                                except OSError:pass
                                self.error('接收文件在修复期间已变化，请重新核对；旧副本仍保留。')
                        if archived!=doc['before']:
                            # A concurrent edit moved with the package; put it back only
                            # into a still-absent destination, never over another file.
                            if archived['directory'] is not None:
                                try:rename_exclusive(operation,'original',records,rid);os.fsync(records)
                                except OSError:pass
                            self.error('接收文件在修复期间已变化，请重新核对；旧副本仍保留。')
                        row=self.state(row,doc,'archived')
                        rename_exclusive(operation,'replacement',records,rid);os.fsync(records);os.fsync(operation)
                        self.s.receipt(rid,verify_files=True)
                    doc['completedAt']=stamp();self.state(row,doc,'done')
                finally:os.close(operation)
        except StoreError as error:
            self.state(row,doc,'blocked',str(error));raise
        except ValueError:
            message='接收目录内容已变化，请在电脑核对；旧副本仍保留。'
            self.state(row,doc,'blocked',message);self.error(message)
        except OSError:
            self.state(row,doc,row['state'],'接收文件尚未修复完成，请检查电脑存储后继续同次修复。')
            self.error('接收文件尚未修复完成，请检查电脑存储后继续同次修复。',503)

    def recover(self):
        with self.s.lock:
            for row in self.db.execute("SELECT * FROM recording_repairs WHERE state IN ('prepared','archived')").fetchall():
                try:self.finish(row)
                except Exception:pass  # Original and staged copies stay available for explicit retry.
