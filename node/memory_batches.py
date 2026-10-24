"""Explicit bounded batches, using the existing versioned single-source extractor."""
import base64
import hashlib
import json
import math
import threading
from catalog import encoded, normalized
from memory_extraction import MAX_CHARACTERS
from memory_segments import segment_value, valid_version

MAX_SOURCES = 20
STOP_PROBLEMS = {
    '模型服务拒绝访问，请检查密钥和模型权限。', '模型额度或频率受限，请稍后重试。',
    '模型请求失败，请检查服务地址和模型名称。', '模型连接超时或不可用，原有资料保留。',
    '模型服务已变化，请重新读取并核对发送目标。', '请先在电脑设置中配置文字模型。',
    '记忆整理规则已变化，请重新选择资料并预览。',
}


class MemoryBatches:
    def __init__(self, store):
        self.s, self.db = store, store.db
        self.worker = None
        self.active_id = None
        self.stopping = threading.Event()
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS memory_batches(
            id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, plan TEXT NOT NULL,
            status TEXT NOT NULL, problem TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_batch_items(
            batch_id TEXT NOT NULL, position INTEGER NOT NULL, document TEXT NOT NULL,
            status TEXT NOT NULL, candidate_ids TEXT NOT NULL, problem TEXT,
            PRIMARY KEY(batch_id,position));
        ''')
        with self.db:
            self.db.execute("UPDATE memory_batches SET status='paused',problem=? WHERE status IN ('running','pausing')",
                            ('上次批量整理已中断，请核对范围后继续。',))
            self.db.execute("UPDATE memory_batch_items SET status='queued' WHERE status='running'")

    def error(self, status, message):
        from store import StoreError
        raise StoreError(status, message)

    def sources(self, params):
        kind, query = params.get('kind','note'), params.get('q','').strip()
        if kind not in self.s.memories.KINDS or len(query)>200:self.error(400,'资料筛选条件不正确。')
        with self.s.lock:
            where='kind=? AND archived=0'; args=[kind]
            if query:where+=' AND instr(search_text,?)>0';args.append(normalized(query))
            total=self.db.execute('SELECT count(*) FROM library_items WHERE '+where,args).fetchone()[0]
            signature=hashlib.sha256(json.dumps([kind,query]).encode()).hexdigest()[:20]
            if params.get('cursor'):
                try:
                    raw=params['cursor']
                    if len(raw)>2000:raise ValueError()
                    cursor=json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
                    if (not isinstance(cursor,list) or len(cursor)!=3 or cursor[0]!=signature or
                            not isinstance(cursor[1],(int,float)) or isinstance(cursor[1],bool) or
                            not math.isfinite(cursor[1]) or not isinstance(cursor[2],str)):raise ValueError()
                except (ValueError,TypeError,IndexError):self.error(400,'分页位置已失效，请重新读取。')
                where+=' AND (sort_date<? OR (sort_date=? AND id<?))';args += [cursor[1],cursor[1],cursor[2]]
            rows=self.db.execute('SELECT id,kind,title,day,sort_date,length(trim(body)) AS text_size FROM library_items WHERE '+where+' ORDER BY sort_date DESC,id DESC LIMIT 41',args).fetchall()
            page=rows[:40]
            return {'items':[{'id':r['id'],'kind':r['kind'],'title':r['title'],'date':r['day'],'textAvailable':r['text_size']>0} for r in page],
                    'total':total,'nextCursor':encoded([signature,page[-1]['sort_date'],page[-1]['id']]) if len(rows)>40 else None}

    def references(self, values):
        from store import canonical_id
        if not isinstance(values,list) or not 1<=len(values)<=MAX_SOURCES:self.error(400,'每批请选择 1 至 20 份资料。')
        refs=[];seen={}
        for value in values:
            if not isinstance(value,dict) or not isinstance(value.get('kind'),str) or value['kind'] not in self.s.memories.KINDS:self.error(400,'资料类型不正确。')
            kind,rid=value['kind'],canonical_id(value.get('id'));key=(kind,rid)
            span=segment_value(value.get('segment'))
            ref={'kind':kind,'id':rid}
            if span:
                if not valid_version(value.get('contentVersion')):self.error(400,'请先读取当前版本的分段范围。')
                ref.update(segment=span,contentVersion=value['contentVersion'])
            for previous in seen.get(key,[]):
                if previous is None or span is None or max(previous['start'],span['start'])<min(previous['end'],span['end']):
                    self.error(400,'同一份资料或重叠片段不能重复选择。')
            seen.setdefault(key,[]).append(span);refs.append(ref)
        return refs

    def preview(self, values):
        from store import StoreError
        refs=self.references(values);items=[]
        for ref in refs:
            item={**ref,'title':ref['id'],'date':'','contentVersion':ref.get('contentVersion',''),
                  'characters':0,'reusable':False,'problem':None}
            try:
                with self.s.lock:
                    row,text,version=self.s.memories.source(ref['kind'],ref['id'])
                    item.update(title=row['title'],date=row['day'],contentVersion=version,characters=len(text))
                if ref.get('segment') and version!=ref['contentVersion']:
                    self.error(409,'原文已变化，请重新读取并选择片段。')
                selected=self.s.memory_segments.select(text,version,ref.get('segment'))
                focus=text[selected['start']:selected['end']] if selected else text
                if selected:item.update(segment=selected,characters=selected['contextEnd']-selected['contextStart'])
                if not focus.strip():item['problem']='这份资料还没有可整理的文字。'
                elif not selected and len(text)>MAX_CHARACTERS:item['problem']='这份资料超过 32000 个字符，请先手动提出记忆建议。'
                task_id,_=self.s.memory_extraction.identity(ref['kind'],ref['id'],version,selected)
                with self.s.lock:
                    old=self.db.execute('SELECT result FROM memory_extractions WHERE id=?',(task_id,)).fetchone()
                    item['reusable']=bool(old and old['result'] is not None)
                    _,_,current=self.s.memories.source(ref['kind'],ref['id'])
                if current!=version:self.error(409,'原文在分段时发生变化，请重新读取。')
            except (StoreError,OSError,UnicodeError) as error:
                with self.s.lock:
                    known=self.db.execute('SELECT title,day FROM library_items WHERE kind=? AND id=?',(ref['kind'],ref['id'])).fetchone()
                    if known:item.update(title=known['title'],date=known['day'])
                item['problem']=str(error) if isinstance(error,StoreError) and error.status in (409,413,503) else '这份资料不可读取或已归档，请返回选择其他资料。'
            items.append(item)
        with self.s.lock:
            config=self.s.analysis.configuration or {};rules=self.s.memory_extraction.rules()
            plan={'items':items,'model':config.get('model',''),'endpoint':config.get('endpoint',''),
                  'rulesVersion':hashlib.sha256(rules.encode()).hexdigest()}
        signature={**plan,'items':[{k:v for k,v in item.items() if k!='reusable'} for item in items]}
        return {**plan,'snapshot':hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest(),
                'configured':bool(config),'newRequests':sum(not i['reusable'] and i['problem'] is None for i in items),
                'characters':sum(i['characters'] for i in items),'sourceLimit':MAX_SOURCES}

    def listing(self):
        with self.s.lock:
            rows=self.db.execute('SELECT id FROM memory_batches ORDER BY created_at DESC,rowid DESC LIMIT 20').fetchall()
            return {'items':[self.status(row['id']) for row in rows]}

    def status(self, batch_id):
        from store import canonical_id
        with self.s.lock:
            row=self.db.execute('SELECT * FROM memory_batches WHERE id=?',(canonical_id(batch_id),)).fetchone()
            if not row:self.error(404,'找不到这次批量整理。')
            plan=json.loads(row['plan']);items=[]
            for item in self.db.execute('SELECT * FROM memory_batch_items WHERE batch_id=? ORDER BY position',(row['id'],)):
                items.append({**json.loads(item['document']),'status':item['status'],
                              'candidateIDs':json.loads(item['candidate_ids']),'problem':item['problem']})
            config=self.s.analysis.configuration or {}
            remaining_requests=0
            for item in items:
                if item['status']=='ready':continue
                tid,_=self.s.memory_extraction.identity(item['kind'],item['id'],item['contentVersion'],item.get('segment'))
                saved=self.db.execute('SELECT result FROM memory_extractions WHERE id=?',(tid,)).fetchone()
                if not saved or saved['result'] is None:remaining_requests+=1
            return {'id':row['id'],'status':row['status'],'problem':row['problem'],
                    'createdAt':row['created_at'],'updatedAt':row['updated_at'],'items':items,
                    'model':plan['model'],'endpoint':plan['endpoint'],'configured':bool(config),
                    'remainingRequests':remaining_requests,
                    'finishedCount':sum(i['status'] in ('ready','failed') for i in items),
                    'candidateCount':sum(len(i['candidateIDs']) for i in items)}

    def start(self, values, background=True):
        from store import canonical_id,stamp
        key=canonical_id(values.get('requestID'));refs=self.references(values.get('sources'))
        if values.get('sendToModel') is not True:self.error(400,'请核对范围并明确选择发送所选资料。')
        snapshot=values.get('snapshot')
        if not isinstance(snapshot,str) or len(snapshot)!=64 or any(c not in '0123456789abcdef' for c in snapshot):self.error(400,'请先预览批量整理范围。')
        fingerprint=hashlib.sha256(json.dumps([refs,snapshot],sort_keys=True).encode()).hexdigest()
        with self.s.lock:
            old=self.db.execute('SELECT fingerprint FROM memory_batches WHERE id=?',(key,)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:self.error(409,'这次批量整理的范围已变化，请重新预览。')
                return self.status(key)
        plan=self.preview(refs)
        with self.s.lock:
            old=self.db.execute('SELECT fingerprint FROM memory_batches WHERE id=?',(key,)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:self.error(409,'这次批量整理的范围已变化，请重新预览。')
                return self.status(key)
            self.require_idle()
            if plan['snapshot']!=snapshot:self.error(409,'资料或模型服务在预览后发生变化，请重新预览发送范围。')
            if any(i['problem'] for i in plan['items']):self.error(409,'所选资料有未就绪项，请返回选择并移除后再试。')
            if plan['newRequests'] and not plan['configured']:self.error(409,'请先在电脑设置中配置文字模型。')
            now=stamp()
            with self.db:
                self.db.execute('INSERT INTO memory_batches VALUES (?,?,?,?,?,?,?)',(key,fingerprint,json.dumps(plan),'running',None,now,now))
                self.db.executemany('INSERT INTO memory_batch_items VALUES (?,?,?,?,?,?)',
                    [(key,index,json.dumps(item),'queued','[]',None) for index,item in enumerate(plan['items'])])
            self.launch(key,background)
            return self.status(key)

    def require_idle(self):
        if self.stopping.is_set() or self.active_id is not None or self.s.memory_extraction.run_lock.locked():
            self.error(409,'另一项记忆整理正在进行，请完成或暂停后再开始。')

    def launch(self, key, background):
        self.active_id=key
        if background:
            self.worker=threading.Thread(target=self.run,args=(key,),daemon=True);self.worker.start()
        else:self.run(key)

    def resume(self, batch_id, values, background=True):
        from store import canonical_id,stamp
        key=canonical_id(batch_id)
        if values.get('sendToModel') is not True:self.error(400,'请核对范围并明确选择发送所选资料。')
        with self.s.lock:
            state=self.status(key)
            if state['status'] in ('ready','running','pausing'):return state
            self.require_idle()
            plan=json.loads(self.db.execute('SELECT plan FROM memory_batches WHERE id=?',(key,)).fetchone()[0])
            if values.get('model')!=plan['model'] or values.get('endpoint')!=plan['endpoint']:
                self.error(409,'发送目标与这次批量整理不一致，请重新选择资料并预览。')
            if hashlib.sha256(self.s.memory_extraction.rules().encode()).hexdigest()!=plan['rulesVersion']:
                self.error(409,'记忆整理规则已变化，请重新选择资料并预览。')
            remaining=[item for item in state['items'] if item['status']!='ready']
            needs_request=False
            for item in remaining:
                tid,_=self.s.memory_extraction.identity(item['kind'],item['id'],item['contentVersion'],item.get('segment'))
                old=self.db.execute('SELECT result FROM memory_extractions WHERE id=?',(tid,)).fetchone()
                needs_request=needs_request or not (old and old['result'] is not None)
            if needs_request:
                config=self.s.analysis.configuration or {}
                if config.get('model')!=plan['model'] or config.get('endpoint')!=plan['endpoint']:
                    self.error(409,'模型服务已变化或未配置，请恢复原服务，或重新选择资料并预览。')
            with self.db:
                self.db.execute("UPDATE memory_batch_items SET status='queued',problem=NULL WHERE batch_id=? AND status!='ready'",(key,))
                self.db.execute("UPDATE memory_batches SET status='running',problem=NULL,updated_at=? WHERE id=?",(stamp(),key))
            self.launch(key,background)
            return self.status(key)

    def pause(self, batch_id):
        from store import stamp
        with self.s.lock:
            state=self.status(batch_id)
            if state['status']=='running':
                with self.db:self.db.execute("UPDATE memory_batches SET status='pausing',updated_at=? WHERE id=?",(stamp(),state['id']))
            return self.status(state['id'])

    def run(self, key):
        from store import StoreError,stamp
        try:
            with self.s.lock:
                plan=json.loads(self.db.execute('SELECT plan FROM memory_batches WHERE id=?',(key,)).fetchone()[0])
                positions=[r['position'] for r in self.db.execute("SELECT position FROM memory_batch_items WHERE batch_id=? AND status='queued' ORDER BY position",(key,))]
            for position in positions:
                with self.s.lock:
                    if self.stopping.is_set() or self.status(key)['status']=='pausing':break
                    row=self.db.execute('SELECT document FROM memory_batch_items WHERE batch_id=? AND position=?',(key,position)).fetchone()
                    item=json.loads(row['document'])
                    with self.db:self.db.execute("UPDATE memory_batch_items SET status='running' WHERE batch_id=? AND position=?",(key,position))
                status,ids,problem='failed',[],None
                try:
                    with self.s.lock:
                        _,_,current=self.s.memories.source(item['kind'],item['id'])
                        if current!=item['contentVersion']:self.error(409,'原文已变化，未发送这个新版本，请重新选择资料并预览。')
                        if hashlib.sha256(self.s.memory_extraction.rules().encode()).hexdigest()!=plan['rulesVersion']:
                            self.error(409,'记忆整理规则已变化，请重新选择资料并预览。')
                    result=self.s.memory_extraction.start(item['kind'],item['id'],
                        {'contentVersion':item['contentVersion'],'segment':item.get('segment'),'sendToModel':True,'model':plan['model'],'endpoint':plan['endpoint']},background=False,batch_id=key)
                    if result['sourceChanged']:self.error(409,'原文已变化，未发送这个新版本，请重新选择资料并预览。')
                    status='ready' if result['status']=='ready' else 'failed'
                    ids,problem=result['candidateIDs'],result['problem']
                except Exception as error:
                    problem=str(error) if isinstance(error,StoreError) else '这份资料整理未完成，原文与已有结果保留。'
                with self.s.lock,self.db:
                    self.db.execute('UPDATE memory_batch_items SET status=?,candidate_ids=?,problem=? WHERE batch_id=? AND position=?',
                                    (status,json.dumps(ids),problem,key,position))
                    self.db.execute('UPDATE memory_batches SET updated_at=? WHERE id=?',(stamp(),key))
                    if problem in STOP_PROBLEMS:
                        self.db.execute("UPDATE memory_batches SET status='pausing',problem=? WHERE id=?",(problem,key))
            with self.s.lock,self.db:
                states=[r[0] for r in self.db.execute('SELECT status FROM memory_batch_items WHERE batch_id=?',(key,))]
                status='paused' if any(s in ('queued','running') for s in states) else 'partial' if 'failed' in states else 'ready'
                self.db.execute('UPDATE memory_batches SET status=?,updated_at=? WHERE id=?',(status,stamp(),key))
        except Exception:
            with self.s.lock,self.db:
                self.db.execute("UPDATE memory_batch_items SET status='queued' WHERE batch_id=? AND status='running'",(key,))
                self.db.execute("UPDATE memory_batches SET status='paused',problem=?,updated_at=? WHERE id=?",
                                ('批量整理已暂停，已有结果保留。请重新读取后继续。',stamp(),key))
        finally:
            with self.s.lock:self.active_id=None

    def close(self):
        self.stopping.set()
        if self.worker and self.worker.is_alive():self.worker.join(timeout=2)
        return not self.worker or not self.worker.is_alive()
