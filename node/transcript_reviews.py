"""Explicit local transcript conflict decisions, with recoverable file delivery.

The original audio, machine output and memory facts are never rewritten. A
user-selected replacement becomes the transcript source only after a backup.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat


class TranscriptReviews:
    MAX_BYTES = 1024 * 1024

    def __init__(self, store):
        self.s,self.db=store,store.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS transcript_reviews(
            id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,recording_id TEXT NOT NULL,
            root TEXT NOT NULL,document TEXT NOT NULL,state TEXT NOT NULL,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        self.db.commit()

    def error(self,status,message):
        from store import StoreError
        raise StoreError(status,message)

    def read(self,relative):
        # Open each directory without following links; reads stay bounded even
        # if another process appends to the selected file during inspection.
        parts=relative.split('/')
        if any(p in ('','.', '..') for p in parts):self.error(400,'转写核对文件位置不正确。')
        fd=os.open(self.s.output,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=child
            raw_fd=os.open(parts[-1],os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=fd)
            with os.fdopen(raw_fd,'rb') as stream:
                before=os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):self.error(409,'转写核对文件不可读取，请核对资料夹。')
                raw=stream.read(self.MAX_BYTES+1)
                if len(raw)>self.MAX_BYTES:self.error(413,'转写核对每个版本最多读取 1 MB，请在电脑资料夹处理更大的文件。')
                after=os.fstat(stream.fileno());current=os.stat(parts[-1],dir_fd=fd,follow_symlinks=False)
                fingerprint=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
                if fingerprint(before)!=fingerprint(after) or fingerprint(before)!=fingerprint(current):
                    self.error(409,'转写文字在读取期间变化，请重新核对。')
            try:text=raw.decode('utf-8')
            except UnicodeError:self.error(400,'转写文件不是可读取的文字，请在电脑资料夹核对。')
            return {'path':relative,'text':text,'etag':hashlib.sha256(raw).hexdigest()}
        finally:os.close(fd)

    def pair(self,rid,allow_resolved=False):
        from store import canonical_id
        rid=canonical_id(rid);self.s.receipt(rid)
        path='transcripts/'+rid+'.md'
        job=self.db.execute('SELECT * FROM speech_jobs WHERE id=?',(rid,)).fetchone()
        row=self.db.execute('SELECT * FROM exports WHERE root=? AND path=?',(str(self.s.output),path)).fetchone()
        decision=self.last_decision(rid) if allow_resolved and job and job['status']=='ready' else None
        resolved=decision is not None
        if not job or not row or (not resolved and (job['status']!='conflict' or row['state'] not in ('conflict','manual'))):
            self.error(409,'转写冲突状态已变化，请重新读取录音。')
        generated=job['generated_path'] if resolved or row['state']=='manual' else row['generated_path']
        if (job['output_root']!=str(self.s.output) or job['generated_path']!=generated or not generated
                or not re.fullmatch(re.escape('.generated/'+path+'/')+r'[a-f0-9]{16}\.md',generated)):
            self.error(409,'转写另存版本的位置已变化，请重新核对。')
        original,new=self.read(path),self.read(generated)
        if not generated.endswith('/'+new['etag'][:16]+'.md'):
            self.error(409,'另存转写已被修改，请保留两个版本并在电脑资料夹核对。')
        signature=[self.s.setting('nodeID'),str(self.s.output),rid,job['checksum'],job['service'],row['semantic_hash'],original['etag'],new['etag'],generated]
        snapshot=hashlib.sha256(json.dumps(signature).encode()).hexdigest()
        return {'recordingID':rid,'nodeID':self.s.setting('nodeID'),'path':path,'snapshot':snapshot,
                'original':original,'generated':new,'maxBytes':self.MAX_BYTES,'resolved':resolved,'lastDecision':decision},row

    def pending(self,rid):
        row=self.db.execute("SELECT id,document FROM transcript_reviews WHERE root=? AND recording_id=? AND state='pending' ORDER BY created_at,id LIMIT 1",(str(self.s.output),rid)).fetchone()
        return {'requestID':row['id'],'action':json.loads(row['document'])['action']} if row else None

    def preview(self,rid):
        from store import canonical_id
        with self.s.lock:
            rid=canonical_id(rid);pair,_=self.pair(rid,allow_resolved=True)
            pair['pendingOperation']=self.pending(rid)
            return pair

    def resolve(self,rid,body):
        from store import canonical_id,stamp
        rid=canonical_id(rid);key=canonical_id(body.get('requestID'))
        if body.get('nodeID')!=self.s.setting('nodeID'):self.error(409,'资料库已变化，草稿仍保留。请回到原资料库核对。')
        snapshot,action=body.get('snapshot'),body.get('action');text=body.get('text')
        if action not in ('keep','generated','merge') or not isinstance(snapshot,str) or not re.fullmatch(r'[a-f0-9]{64}',snapshot):
            self.error(400,'请先核对两个版本，再选择如何保存转写。')
        if action=='merge':
            if not isinstance(text,str) or len(text.encode())>self.MAX_BYTES or '\0' in text:
                self.error(400,'合并文字须为有效文字，最多 1 MB。')
        elif text is not None:self.error(400,'只有手动合并可以提交新的文字。')
        fingerprint=hashlib.sha256(json.dumps([rid,snapshot,action,text]).encode()).hexdigest()
        with self.s.lock:
            old=self.db.execute('SELECT * FROM transcript_reviews WHERE id=?',(key,)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint or old['root']!=str(self.s.output):self.error(409,'这次转写核对的内容或资料夹已变化，请重新核对。')
                if old['state']=='done':return self.receipt(old)
                if old['state']=='blocked':self.error(409,'转写文字在保存期间变化，请重新核对两个版本。')
            else:
                if self.pending(rid):self.error(409,'上次转写核对仍未保存完成，请先继续上次处理。')
                pair,row=self.pair(rid)
                if pair['snapshot']!=snapshot:self.error(409,'转写文字在预览后变化，草稿仍保留。请重新核对两个版本。')
                replacement=text if action=='merge' else pair['generated' if action=='generated' else 'original']['text']
                op={'path':pair['path'],'action':action,'originalHash':pair['original']['etag'],
                    'generatedHash':pair['generated']['etag'],'generatedPath':pair['generated']['path'],
                    'semantic':row['semantic_hash'],'replacementHash':hashlib.sha256(replacement.encode()).hexdigest(),
                    'mergedText':text if action=='merge' else None,
                    'backupPath':'.history/'+pair['path']+'/review-'+key+'.md' if action!='keep' else None}
                with self.db:self.db.execute('INSERT INTO transcript_reviews VALUES (?,?,?,?,?,?,?,?)',
                    (key,fingerprint,rid,str(self.s.output),json.dumps(op),'pending',stamp(),stamp()))
            return self.complete(key)

    def receipt(self,row):
        op=json.loads(row['document'])
        return {'requestID':row['id'],'recordingID':row['recording_id'],'action':op['action'],
                'backupPath':op['backupPath'],'generatedPath':op['generatedPath'],'contentVersion':op['replacementHash']}

    def retry(self,rid,body):
        from store import canonical_id
        rid=canonical_id(rid);key=canonical_id(body.get('requestID'))
        if body.get('nodeID')!=self.s.setting('nodeID'):self.error(409,'资料库已变化，草稿仍保留。请回到原资料库核对。')
        with self.s.lock:
            row=self.db.execute('SELECT * FROM transcript_reviews WHERE id=?',(key,)).fetchone()
            if not row or row['recording_id']!=rid or row['root']!=str(self.s.output):self.error(409,'找不到当前资料库中的转写核对记录。')
            if row['state']=='done':return self.receipt(row)
            if row['state']!='pending':self.error(409,'转写文字在保存期间变化，请重新核对两个版本。')
            return self.complete(key)

    def complete(self,key):
        from store import StoreError,stamp
        row=self.db.execute('SELECT * FROM transcript_reviews WHERE id=?',(key,)).fetchone();op=json.loads(row['document'])
        try:
            if row['root']!=str(self.s.output):self.error(409,'资料夹已变化，请重新核对。')
            pair,export=self.pair(row['recording_id'])
            if (export['semantic_hash']!=op['semantic'] or pair['generated']['path']!=op['generatedPath']
                    or pair['generated']['etag']!=op['generatedHash']):
                self.error(409,'转写文字在保存期间变化，请重新核对两个版本。')
            original=pair['original']
            if op['action']!='keep':
                backup=self.s._path(op['backupPath'])
                backup_ok=backup.is_file() and self.read(op['backupPath'])['etag']==op['originalHash']
                already_written=original['etag']==op['replacementHash'] and backup_ok
                if not already_written:
                    if original['etag']!=op['originalHash'] or (backup.exists() and not backup_ok):self.error(409,'转写文字在保存期间变化，请重新核对两个版本。')
                    if not backup_ok:self.s._atomic(backup,original['text'].encode())
                    if self.pair(row['recording_id'])[0]['snapshot']!=pair['snapshot']:self.error(409,'转写文字在保存期间变化，请重新核对两个版本。')
                    text=op['mergedText'] if op['action']=='merge' else pair['generated']['text']
                    self.s._atomic(self.s._path(op['path']),text.encode())
            elif original['etag']!=op['originalHash']:self.error(409,'转写文字在保存期间变化，请重新核对两个版本。')
            if self.read(op['path'])['etag']!=op['replacementHash']:self.error(409,'转写文字在保存期间变化，请重新核对两个版本。')
            # Derived local files may fail too. Keep the durable operation pending
            # until they finish; retry never calls a speech or memory model.
            self.s.catalog.index_recording(row['recording_id'],force=True)
            self.s.build_daily(self.s.day_for(self.s.manifest(row['recording_id'])['recording']))
            with self.db:
                self.db.execute('UPDATE exports SET last_hash=?,state=?,generated_path=NULL,updated_at=? WHERE root=? AND path=?',
                    (op['replacementHash'],'current' if op['action']=='generated' else 'reviewed',stamp(),row['root'],op['path']))
                self.db.execute("UPDATE speech_jobs SET status='ready',problem=NULL,updated_at=? WHERE id=?",(stamp(),row['recording_id']))
                self.db.execute("UPDATE transcript_reviews SET state='done',updated_at=? WHERE id=?",(stamp(),key))
            return self.receipt(row)
        except StoreError:
            with self.db:self.db.execute("UPDATE transcript_reviews SET state='blocked',updated_at=? WHERE id=?",(stamp(),key))
            raise

    def recover(self):
        with self.s.lock:
            for row in self.db.execute("SELECT id FROM transcript_reviews WHERE state='pending' AND root=?",(str(self.s.output),)).fetchall():
                try:self.complete(row['id'])
                except Exception:pass  # I/O failure remains pending for explicit retry.

    def last_decision(self,rid):
        from store import StoreError
        row=self.db.execute("SELECT * FROM transcript_reviews WHERE root=? AND recording_id=? AND state='done' ORDER BY updated_at DESC,rowid DESC LIMIT 1",(str(self.s.output),rid)).fetchone()
        if not row:return None
        result=self.receipt(row)
        try:result['matchesCurrent']=self.read('transcripts/'+rid+'.md')['etag']==result['contentVersion']
        except (OSError,ValueError,StoreError):result['matchesCurrent']=False
        return result
