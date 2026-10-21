"""Explicit ChatGPT text import and portable library exports; no account scraping."""
from __future__ import annotations
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import uuid
import zipfile
from zoneinfo import ZoneInfo

MAX_IMPORT = 64 * 1024 * 1024
NAMESPACE = uuid.UUID('7d4f5cb3-fcc5-480c-b333-fd1ee47fd948')

class DataExchange:
    def __init__(self, store):
        self.s = store
        with store.lock:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS import_batches(id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL,state TEXT NOT NULL,report TEXT);
            CREATE TABLE IF NOT EXISTS note_origins(id TEXT PRIMARY KEY,source TEXT NOT NULL,source_id TEXT NOT NULL,body_hash TEXT NOT NULL,title TEXT NOT NULL);
            ''')

    def error(self, status, message):
        from store import StoreError
        raise StoreError(status, message)

    @staticmethod
    def stamp():return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

    def parse(self, data):
        if len(data) > MAX_IMPORT:self.error(413, '导入文件超过 64 MB，请分批导入。')
        if data.startswith(b'PK'):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    matches=[i for i in archive.infolist() if Path(i.filename).name=='conversations.json' and not i.is_dir()]
                    if len(matches)!=1:self.error(400,'压缩包中需要包含唯一的 conversations.json。')
                    item=matches[0]
                    if item.file_size>MAX_IMPORT:self.error(413,'对话文件过大，请分批导入。')
                    with archive.open(item) as stream:data=stream.read(MAX_IMPORT+1)
                    if len(data)>MAX_IMPORT:self.error(413,'对话文件过大，请分批导入。')
            except (zipfile.BadZipFile,RuntimeError,OSError):self.error(400,'无法读取这个压缩包。')
        try:payload=json.loads(data)
        except (ValueError,UnicodeError):self.error(400,'请使用聊天导出的 JSON 或 ZIP 文件。')
        if isinstance(payload,dict):payload=payload.get('conversations')
        if not isinstance(payload,list) or not payload or len(payload)>20000:self.error(400,'未识别到有效的对话列表。')
        items=[];warnings=[];messages=0;branch_count=0;nontext=0
        for index,conversation in enumerate(payload):
            if not isinstance(conversation,dict) or not isinstance(conversation.get('mapping'),dict):
                warnings.append('conversation '+str(index+1)+': unsupported shape');continue
            cid=conversation.get('id') or conversation.get('conversation_id')
            if not isinstance(cid,str) or not cid or len(cid)>256:
                warnings.append('conversation '+str(index+1)+': missing stable ID');continue
            mapping=conversation['mapping'];current=conversation.get('current_node');chain=[];seen=set()
            if current:
                while current:
                    if not isinstance(current,str):self.error(400,'对话分支编号格式不正确。')
                    if current in seen or current not in mapping:self.error(400,'对话分支结构损坏，未导入。')
                    seen.add(current);node=mapping[current]
                    if not isinstance(node,dict):self.error(400,'对话消息结构不正确。')
                    chain.append(node);current=node.get('parent')
                chain.reverse()
                branch_count+=sum(isinstance(n,dict) and isinstance(n.get('message'),dict) for k,n in mapping.items() if k not in seen)
            else:
                # Without a selected branch, importing all nodes as a linear dialog would fabricate chronology.
                warnings.append('conversation '+str(index+1)+': missing current_node');continue
            buckets={}
            for node in chain:
                message=node.get('message')
                if not isinstance(message,dict):continue
                author=message.get('author') or {}
                if not isinstance(author,dict):self.error(400,'对话作者信息格式不正确。')
                role=author.get('role','unknown')
                if role not in ('user','assistant'):continue
                content=message.get('content') or {}
                if not isinstance(content,dict):self.error(400,'对话内容格式不正确。')
                parts=content.get('parts',[])
                text='\n'.join(p for p in parts if isinstance(p,str)) if isinstance(parts,list) else ''
                if not text.strip():nontext+=1;continue
                ts=message.get('create_time') or conversation.get('create_time')
                try:
                    if isinstance(ts,bool):raise ValueError()
                    moment=dt.datetime.fromtimestamp(float(ts),dt.timezone.utc)
                    if moment.year<1970 or moment.year>2200:raise ValueError()
                except (ValueError,TypeError,OverflowError,OSError):
                    warnings.append('conversation '+str(index+1)+': message timestamp missing');continue
                day=moment.astimezone(ZoneInfo(self.s.setting('timeZone'))).date().isoformat()
                heading='用户' if role=='user' else '助手'
                rendered='### '+heading+' · '+moment.isoformat()+'\n\n'+text.strip()
                buckets.setdefault(day,[]).append((moment,rendered));messages+=1
            title=str(conversation.get('title') or 'ChatGPT 对话').replace('\n',' ')[:120]
            for day,turns in buckets.items():
                chunks=[];buffer=[];size=0
                for moment,rendered in turns:
                    # Split oversized individual messages too; every part keeps its author and time.
                    for offset in range(0,len(rendered),150000):
                        piece=rendered[offset:offset+150000]
                        if buffer and size+len(piece)>160000:chunks.append(buffer);buffer=[];size=0
                        buffer.append((moment,piece));size+=len(piece)
                if buffer:chunks.append(buffer)
                for part,chunk in enumerate(chunks):
                    source_id=cid+':'+day+':'+str(part)
                    identifier=str(uuid.uuid5(NAMESPACE,source_id)).upper()
                    body='# '+title+'\n\n来源：ChatGPT 导入\n原始对话编号：'+cid+'\n日期：'+day+'\n\n用户与助手发言分别保留。助手内容不等于用户事实。\n\n'+'\n\n'.join(t for _,t in chunk)+'\n'
                    items.append({'id':identifier,'title':(title+' · '+day+(' · '+str(part+1) if len(chunks)>1 else ''))[:160],
                                  'text':body,'created_at':chunk[0][0].isoformat(),'source_id':source_id,'day':day})
        if not items:self.error(400,'没有可导入的文字对话。请检查导出文件和日期信息。')
        if len({i['id'] for i in items})!=len(items):self.error(400,'导出文件包含重复对话编号，未导入。')
        return data,items,{'conversations':len(payload),'notes':len(items),'messages':messages,
                           'otherBranchMessages':branch_count,'nonTextMessages':nontext,'warnings':warnings[:100],
                           'warningCount':len(warnings),'firstDay':min(i['day'] for i in items),'lastDay':max(i['day'] for i in items)}

    def preview(self, data):
        raw,items,report=self.parse(data);fingerprint=hashlib.sha256(raw).hexdigest()
        with self.s.lock:
            old=self.s.db.execute('SELECT * FROM import_batches WHERE digest=?',(fingerprint,)).fetchone()
            if old and old['state']=='complete':return {'id':old['id'],'alreadyImported':True,**json.loads(old['report'])}
            rid=old['id'] if old else str(uuid.uuid4()).upper()
            folder=self.s.data/'imports';folder.mkdir(exist_ok=True,mode=0o700)
            self.s._atomic(folder/(rid+'.json'),raw)
            with self.s.db:self.s.db.execute('INSERT OR REPLACE INTO import_batches VALUES (?,?,?,?,?)',(rid,fingerprint,self.stamp(),'preview',json.dumps(report)))
            return {'id':rid,'alreadyImported':False,**report,'titles':[i['title'] for i in items[:10]]}

    def commit(self, identifier):
        from store import canonical_id
        rid=canonical_id(identifier)
        with self.s.lock:
            batch=self.s.db.execute('SELECT * FROM import_batches WHERE id=?',(rid,)).fetchone()
            if not batch:self.error(404,'找不到这次导入。')
            if batch['state']=='complete':return json.loads(batch['report'])
            _,items,report=self.parse((self.s.data/'imports'/(rid+'.json')).read_bytes())
            added=updated=skipped=conflicts=0;days=set()
            for item in items:
                old=self.s.db.execute('SELECT * FROM note_origins WHERE id=?',(item['id'],)).fetchone()
                existing=self.s.db.execute('SELECT * FROM notes WHERE id=?',(item['id'],)).fetchone()
                body_hash=hashlib.sha256(item['text'].encode()).hexdigest()
                if existing:
                    document=self.s.read_document('notes/'+item['id']+'.md')
                    if document['text']==item['text'] and existing['title']==item['title']:skipped+=1;continue
                    if not old or document['etag']!=old['body_hash'] or existing['title']!=old['title']:
                        self.s._atomic(self.s._path('.generated/imports/'+item['id']+'-'+body_hash[:12]+'.md'),item['text'].encode())
                        conflicts+=1;continue
                    self.s.edit_document('notes/'+item['id']+'.md',item['text'],document['etag']);updated+=1
                    with self.s.db:self.s.db.execute('UPDATE notes SET title=?,updated_at=? WHERE id=?',(item['title'],self.stamp(),item['id']))
                else:
                    path='notes/'+item['id']+'.md'
                    document=self.s.read_document(path)
                    if document['text'] and document['text']!=item['text']:self.error(409,'导入目标已存在其他内容。')
                    self.s.edit_document(path,item['text'],document['etag'])
                    with self.s.db:self.s.db.execute('INSERT INTO notes VALUES (?,?,?,?)',(item['id'],item['title'],item['created_at'],self.stamp()))
                    added+=1
                with self.s.db:self.s.db.execute('INSERT OR REPLACE INTO note_origins VALUES (?,?,?,?,?)',(item['id'],'chatgpt',item['source_id'],body_hash,item['title']))
                self.s.catalog.index_note(item['id'])
                day=self.s.db.execute("SELECT day FROM library_items WHERE kind='note' AND id=?",(item['id'],)).fetchone()[0];days.add(day)
            for day in days:
                self.s.build_daily(day)
            report.update({'added':added,'updated':updated,'skipped':skipped,'conflicts':conflicts})
            with self.s.db:self.s.db.execute('UPDATE import_batches SET state=?,report=? WHERE id=?',('complete',json.dumps(report),rid))
            return report

    def cancel(self, identifier):
        from store import canonical_id
        rid=canonical_id(identifier)
        with self.s.lock:
            row=self.s.db.execute('SELECT state FROM import_batches WHERE id=?',(rid,)).fetchone()
            if row and row['state']=='preview':
                (self.s.data/'imports'/(rid+'.json')).unlink(missing_ok=True)
                with self.s.db:self.s.db.execute('DELETE FROM import_batches WHERE id=?',(rid,))
        return {'cancelled':True}

    def export(self):
        """Portable text bundle; excludes credentials, raw database and audio bytes."""
        with self.s.lock:
            temp=tempfile.NamedTemporaryFile(prefix='library-export-',suffix='.zip',dir=self.s.staging,delete=False);temp.close()
            path=Path(temp.name);manifest={'schemaVersion':1,'createdAt':self.stamp(),'timeZone':self.s.setting('timeZone'),
                'includesAudio':False,'includesCredentials':False,'notes':[],'recordings':[], 'candidates':[],'files':[]}
            try:
                with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
                    for folder in ['notes','transcripts','daily','analysis','prompts']:
                        for file in sorted((self.s.output/folder).glob('*.md')):
                            relative=folder+'/'+file.name;data=self.s._path(relative).read_bytes()
                            archive.writestr(relative,data);manifest['files'].append({'path':relative,'sha256':hashlib.sha256(data).hexdigest()})
                    for filename in ['memory.md','memory-inbox.md']:
                        data=self.s.read_document(filename)['text'].encode();archive.writestr(filename,data)
                        manifest['files'].append({'path':filename,'sha256':hashlib.sha256(data).hexdigest()})
                    source_files = list((self.s.output/'sources').rglob('*'))
                    source_files += list((self.s.output/'.generated/sources').rglob('*.md'))
                    source_files += list((self.s.output/'.history/sources').rglob('*.md'))
                    source_files += list((self.s.output/'documents').glob('*.md'))
                    source_files += list((self.s.output/'.generated/documents').glob('*.md'))
                    source_files += list((self.s.output/'.history/documents').glob('*.md'))
                    for file in sorted(source_files):
                        if file.is_file() and not file.is_symlink() and (file.suffix=='.md' or file.name=='index.json'):
                            relative=file.relative_to(self.s.output).as_posix();data=self.s._path(relative).read_bytes()
                            archive.writestr(relative,data);manifest['files'].append({'path':relative,'sha256':hashlib.sha256(data).hexdigest()})
                    for kind,key in [('note','notes'),('recording','recordings'),('memory','candidates'),('document','documents')]:
                        manifest[key]=[self.s.catalog.item(row) for row in self.s.db.execute('SELECT * FROM library_items WHERE kind=?',(kind,))]
                    conversations=[]
                    for row in self.s.db.execute('SELECT * FROM conversations ORDER BY created_at,id'):
                        item=dict(row);item['turns']=[self.s.knowledge.turn_data(r) for r in self.s.db.execute('SELECT * FROM conversation_turns WHERE conversation_id=? ORDER BY sequence',(row['id'],))]
                        conversations.append(item)
                    archive.writestr('conversations.json',json.dumps(conversations,ensure_ascii=False,indent=2))
                    archive.writestr('categories.json',json.dumps([dict(r) for r in self.s.db.execute('SELECT * FROM library_categories')],ensure_ascii=False,indent=2))
                    for row in self.s.db.execute("SELECT id FROM import_batches WHERE state='complete'"):
                        original=self.s.data/'imports'/(row['id']+'.json')
                        if original.is_file():archive.write(original,'original-imports/'+original.name)
                    archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                    archive.writestr('README.txt','Personal library text export. Includes source text, dialog history, category metadata and original imported chat JSON. Audio and credentials are excluded. Import originals preserve branches that are not in the readable notes. This is a portable export, not an automatic restore package.\n')
                return path
            except Exception:path.unlink(missing_ok=True);raise
