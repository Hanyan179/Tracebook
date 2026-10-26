"""Indexed organization and bounded browsing; original audio and documents remain intact."""
from __future__ import annotations
import base64
import datetime as dt
import hashlib
import json
from contextlib import nullcontext
import sqlite3
import unicodedata
import uuid
from zoneinfo import ZoneInfo

DEFAULTS = {
    'recording': [('work','工作'),('life','生活'),('learning','学习'),('ideas','灵感')],
    'note': [('work','工作'),('life','生活'),('learning','学习'),('ideas','灵感')],
    'memory': [('profile','个人资料'),('preferences','偏好习惯'),('goals','目标计划'),('projects','工作项目'),('people','人物关系')],
}

def normalized(text):
    return unicodedata.normalize('NFKC', text).casefold()

def encoded(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')

class Catalog:
    def __init__(self, store):
        self.s = store
        self.db = store.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS library_categories(id TEXT PRIMARY KEY,scope TEXT NOT NULL,name TEXT NOT NULL,normalized TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,UNIQUE(scope,normalized));
        CREATE TABLE IF NOT EXISTS library_items(kind TEXT NOT NULL,id TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,sort_date REAL NOT NULL,day TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,mtime TEXT NOT NULL,category_id TEXT NOT NULL DEFAULT '',tags TEXT NOT NULL DEFAULT '[]',archived INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 0,search_text TEXT NOT NULL DEFAULT '',PRIMARY KEY(kind,id));
        CREATE INDEX IF NOT EXISTS library_order ON library_items(kind,archived,sort_date DESC,id DESC);
        CREATE INDEX IF NOT EXISTS library_category ON library_items(kind,category_id,archived,sort_date DESC,id DESC);
        CREATE INDEX IF NOT EXISTS library_day ON library_items(kind,day);
        CREATE TABLE IF NOT EXISTS library_tags(kind TEXT NOT NULL,id TEXT NOT NULL,tag TEXT NOT NULL,folded TEXT NOT NULL,PRIMARY KEY(kind,id,folded));
        CREATE INDEX IF NOT EXISTS library_tag_lookup ON library_tags(kind,folded,id);
        ''')
        self.trigram = True
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS library_search USING fts5(entity UNINDEXED,body,tokenize='trigram')")
        except sqlite3.OperationalError:
            self.trigram = False
        with self.db:
            for scope, entries in DEFAULTS.items():
                for key, name in entries:
                    self.db.execute('INSERT OR IGNORE INTO library_categories(id,scope,name,normalized) VALUES (?,?,?,?)', (scope+'.'+key,scope,name,normalized(name)))
        self.reconcile()
        self.export_categories()

    def error(self, status, message):
        from store import StoreError
        raise StoreError(status, message)

    def _search_index(self, kind, rid):
        row = self.db.execute('SELECT title,body,tags,data FROM library_items WHERE kind=? AND id=?',(kind,rid)).fetchone()
        start = json.loads(row['data']).get('contentStart', 0) if kind == 'document' else 0
        text = normalized(row['title']+'\n'+row['body'][start:]+'\n'+' '.join(json.loads(row['tags'])))
        self.db.execute('UPDATE library_items SET search_text=? WHERE kind=? AND id=?',(text,kind,rid))
        if self.trigram:
            key=kind+':'+rid
            self.db.execute('DELETE FROM library_search WHERE entity=?',(key,))
            self.db.execute('INSERT INTO library_search(entity,body) VALUES (?,?)',(key,text))

    def _upsert(self, kind, rid, title, body, date, state, data, mtime, commit=True):
        day=date.astimezone(ZoneInfo(self.s.setting('timeZone'))).date().isoformat()
        raw=json.dumps(data,ensure_ascii=False,sort_keys=True)
        old=self.db.execute('SELECT data,mtime,day FROM library_items WHERE kind=? AND id=?',(kind,rid)).fetchone()
        if old and (old['data'],old['mtime'],old['day'])==(raw,mtime,day):return
        with self.db if commit else nullcontext():
            self.db.execute('''INSERT INTO library_items(kind,id,title,body,sort_date,day,state,data,mtime) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(kind,id) DO UPDATE SET title=excluded.title,body=excluded.body,sort_date=excluded.sort_date,day=excluded.day,state=excluded.state,data=excluded.data,mtime=excluded.mtime''',
                (kind,rid,title,body,date.timestamp(),day,state,raw,mtime))
            self._search_index(kind,rid)

    def index_recording(self, rid, force=False):
        from store import StoreError
        with self.s.lock:
            m=self.s.manifest(rid);r=m['recording']
            old=self.db.execute("SELECT body,mtime FROM library_items WHERE kind='recording' AND id=?",(rid,)).fetchone()
            try:
                p=self.s._path('transcripts/'+rid+'.md')
                stat=p.stat() if p.exists() else None
                mtime=str(self.s.output)+(':'+str(stat.st_mtime_ns)+':'+str(stat.st_size) if stat else ':missing')
                body=old['body'] if old and old['mtime']==mtime and not force else (p.read_text() if stat else '')
            except (OSError,UnicodeError,StoreError):
                body=old['body'] if old else '';mtime='unavailable'
            data={'id':rid,'title':r.get('title') or '语音记录','startedAt':r['startedAt'],'duration':r['duration'],
                  'byteCount':r['byteCount'],'sourceDevice':r.get('sourceDevice'),'audioSHA256':m['audioSHA256'],'markers':r.get('markers') or [],
                  'textState':'ready' if body.strip() else 'awaitingText'}
            self._upsert('recording',rid,data['title'],body,dt.datetime.fromisoformat(r['startedAt'].replace('Z','+00:00')),data['textState'],data,mtime)

    def index_memory(self, cid, commit=True):
        with self.s.lock:
            row=self.db.execute('SELECT * FROM candidates WHERE id=?',(cid,)).fetchone()
            if not row:return
            data=self.s.memories.data(row)
            self._upsert('memory',cid,'',row['text'],dt.datetime.fromisoformat(row['created_at'].replace('Z','+00:00')),row['state'],data,row['updated_at']+row['state']+row['text'],commit=commit)

    def index_note(self, rid):
        with self.s.lock:
            row=self.db.execute('SELECT * FROM notes WHERE id=?',(rid,)).fetchone()
            if not row:return
            document=self.s.read_document('notes/'+rid+'.md')
            data={**dict(row),'excerpt':document['text'][:160]}
            self._upsert('note',rid,row['title'],document['text'],dt.datetime.fromisoformat(row['created_at']),
                         'ready',data,document['etag']+row['updated_at'])

    def reconcile(self):
        with self.s.lock:
            self.s.require_output()
            for row in self.db.execute('SELECT id FROM recordings').fetchall():self.index_recording(row['id'])
            for row in self.db.execute('SELECT id FROM candidates').fetchall():self.index_memory(row['id'])
            for row in self.db.execute('SELECT id FROM notes').fetchall():self.index_note(row['id'])
            for row in self.db.execute('SELECT id FROM local_inputs').fetchall():self.index_input(row['id'])
            for row in self.db.execute('SELECT id FROM documents').fetchall():self.index_document(row['id'])
            days={r[0] for r in self.db.execute("SELECT DISTINCT day FROM library_items WHERE kind IN ('recording','note')")}
            try:days.update(p.stem for p in self.s._path('daily/index').parent.glob('????-??-??.md'))
            except OSError:pass
            for day in days:self.index_daily(day)
            for file in self.s._path("analysis/index").parent.glob("????-??-??.md"):self.index_analysis(file.stem)

    def index_analysis(self, day):
        try:date=dt.datetime.combine(dt.date.fromisoformat(day),dt.time(12),ZoneInfo(self.s.setting('timeZone')))
        except ValueError:return
        with self.s.lock:
            document=self.s.read_document('analysis/'+day+'.md')
            if not document['text']:return
            data={'id':day,'title':day+' · AI 每日分析'}
            self._upsert('analysis',day,data['title'],document['text'],date,'ready',data,document['etag'])

    def index_input(self, rid):
        with self.s.lock:
            item = self.db.execute('SELECT i.*,s.provider FROM local_inputs i JOIN local_sources s ON s.id=i.source_id WHERE i.id=?', (rid,)).fetchone()
            if not item:return
            path = self.s.local_sources.document_path(item, item['provider'])
            file = self.s._path(path)
            info = file.stat() if file.exists() else None
            mtime = str(self.s.output)+(':'+str(info.st_mtime_ns)+':'+str(info.st_size) if info else ':missing')
            old = self.db.execute("SELECT body,mtime FROM library_items WHERE kind='input' AND id=?", (rid,)).fetchone()
            body = old['body'] if old and old['mtime']==mtime else self.s.read_document(path)['text']
            title = item['text'].splitlines()[0].lstrip('# ').strip()[:100] or '本人输入'
            data = {'id':rid,'title':title,'provider':item['provider'],'path':path,'sourceID':item['source_id']}
            self._upsert('input',rid,title,body,dt.datetime.fromisoformat(item['timestamp']),
                         'ready' if body else 'missing',data,mtime)

    def index_document(self, rid):
        with self.s.lock:
            item = self.db.execute('SELECT * FROM documents WHERE id=?', (rid,)).fetchone()
            if not item:return
            path = 'documents/'+rid+'.md'
            document = self.s.read_document(path)
            data = {'id':rid,'title':item['title'],'path':path,'format':item['format'],'previousID':item['previous_id']}
            data['documentInfo'] = self.s.documents.info(rid, document['etag'])
            data['contentStart'] = self.s.documents.content_start(item, document['text'])
            self._upsert('document',rid,item['title'],document['text'],dt.datetime.fromisoformat(item['created_at']),
                         'ready' if document['text'] else 'missing',data,document['etag'])

    def index_daily(self, day):
        try:date=dt.datetime.combine(dt.date.fromisoformat(day),dt.time(12),ZoneInfo(self.s.setting('timeZone')))
        except ValueError:return
        with self.s.lock:
            path='daily/'+day+'.md';p=self.s._path(path)
            stat=p.stat() if p.exists() else None
            mtime=str(self.s.output)+(':'+str(stat.st_mtime_ns)+':'+str(stat.st_size) if stat else ':missing')
            old=self.db.execute("SELECT body,mtime FROM library_items WHERE kind='daily' AND id=?",(day,)).fetchone()
            body=old['body'] if old and old['mtime']==mtime else (p.read_text() if stat else '')
            export=self.db.execute('SELECT state,generated_path FROM exports WHERE root=? AND path=?',(str(self.s.output),path)).fetchone()
            count=self.db.execute("SELECT COUNT(*) FROM library_items WHERE kind='recording' AND day=?",(day,)).fetchone()[0]
            note_count=self.db.execute("SELECT COUNT(*) FROM library_items WHERE kind='note' AND day=?",(day,)).fetchone()[0]
            data={'noteCount':note_count,'id':day,'state':export['state'] if export else 'pending','generatedPath':export['generated_path'] if export else None,'recordingCount':count}
            self._upsert('daily',day,day,body,date,data['state'],data,mtime)

    def organization(self, row):
        return {'categoryID':row['category_id'],'tags':json.loads(row['tags']),'archived':bool(row['archived']),'revision':row['revision']}

    def item(self, row):
        data=json.loads(row['data']);data['organization']=self.organization(row);data['day']=row['day']
        if row['kind']=='recording':data['excerpt']=row['body'].replace('\n',' ')[:140]
        return data

    def detail(self, kind, rid):
        from store import canonical_id
        with self.s.lock:
            row=self.db.execute('SELECT * FROM library_items WHERE kind=? AND id=?',(kind,canonical_id(rid))).fetchone()
            if not row:self.error(404,'找不到这条资料。')
            return self.item(row)

    def categories(self, kind):
        if kind not in DEFAULTS:self.error(400,'分类范围不正确。')
        with self.s.lock:
            return [dict(r) for r in self.db.execute('''SELECT c.id,c.scope,c.name,c.revision,COUNT(i.id) AS count FROM library_categories c
                LEFT JOIN library_items i ON i.category_id=c.id AND i.kind=c.scope AND i.archived=0 AND i.state!='rejected'
                WHERE c.scope=? GROUP BY c.id ORDER BY c.rowid''',(kind,))]

    def save_category(self, kind, name, cid=None, revision=None):
        if kind not in DEFAULTS or not isinstance(name,str) or not 1<=len(name.strip())<=24 or '\n' in name or '\r' in name:self.error(400,'分类名称请使用 1 至 24 个字符。')
        name=name.strip()
        with self.s.lock:
            try:
                with self.db:
                    if cid:
                        row=self.db.execute('SELECT revision FROM library_categories WHERE id=? AND scope=?',(cid,kind)).fetchone()
                        if not row:self.error(404,'找不到这个分类。')
                        if not isinstance(revision,int) or isinstance(revision,bool) or row['revision']!=revision:self.error(409,'分类已被修改，请重新读取。')
                        self.db.execute('UPDATE library_categories SET name=?,normalized=?,revision=revision+1 WHERE id=?',(name,normalized(name),cid))
                    else:
                        cid=str(uuid.uuid4()).upper()
                        self.db.execute('INSERT INTO library_categories(id,scope,name,normalized) VALUES (?,?,?,?)',(cid,kind,name,normalized(name)))
                    if kind=='memory':self.s.memories.mark_delivery_pending()
            except sqlite3.IntegrityError:self.error(409,'这个分类名称已经存在。')
            self.export_categories()
            if kind=='memory':self.s.export_memory()
            return next(c for c in self.categories(kind) if c['id']==cid)

    def export_categories(self):
        raw=json.dumps([dict(r) for r in self.db.execute('SELECT id,scope,name,revision FROM library_categories ORDER BY id')],ensure_ascii=False,indent=2)
        self.s.export('organization/categories.json',raw,hashlib.sha256(raw.encode()).hexdigest())

    def update(self, kind, rid, body):
        from store import canonical_id
        rid=canonical_id(rid)
        with self.s.lock:
            row=self.db.execute('SELECT * FROM library_items WHERE kind=? AND id=?',(kind,rid)).fetchone()
            if not row:self.error(404,'找不到这条资料。')
            if not isinstance(body.get('revision'),int) or isinstance(body.get('revision'),bool) or body['revision']!=row['revision']:self.error(409,'资料分类已被其他地方修改，请重新读取。')
            category=body.get('categoryID',row['category_id']);tags=body.get('tags',json.loads(row['tags']));archived=body.get('archived',bool(row['archived']))
            if not isinstance(category,str) or (category and not self.db.execute('SELECT 1 FROM library_categories WHERE id=? AND scope=?',(category,kind)).fetchone()):self.error(400,'请选择此类资料的有效分类。')
            if not isinstance(tags,list) or len(tags)>12 or any(not isinstance(t,str) or not 1<=len(t.strip())<=32 or any(c in t for c in '\n\r') for t in tags):self.error(400,'最多添加 12 个标签，每个不超过 32 个字符。')
            if not isinstance(archived,bool):self.error(400,'归档状态不正确。')
            unique={}
            for tag in tags:unique.setdefault(normalized(tag.strip()),tag.strip())
            tags=list(unique.values())
            if (category,tags,archived)==(row['category_id'],json.loads(row['tags']),bool(row['archived'])):
                return self.item(row)
            with self.db:
                self.db.execute('UPDATE library_items SET category_id=?,tags=?,archived=?,revision=revision+1 WHERE kind=? AND id=?',(category,json.dumps(tags,ensure_ascii=False),int(archived),kind,rid))
                self.db.execute('DELETE FROM library_tags WHERE kind=? AND id=?',(kind,rid))
                self.db.executemany('INSERT INTO library_tags VALUES (?,?,?,?)',[(kind,rid,t,normalized(t)) for t in tags])
                self._search_index(kind,rid)
                if kind=='memory':self.s.memories.mark_delivery_pending()
            result=self.detail(kind,rid)
            raw=json.dumps({'id':rid,'kind':kind,**result['organization']},ensure_ascii=False,indent=2)
            self.s.export('organization/'+kind+'/'+rid+'.json',raw,hashlib.sha256(raw.encode()).hexdigest())
            if kind=='memory':self.s.export_memory()
            return result

    def query(self, kind, params):
        if kind not in ('recording','memory','daily','note'):self.error(400,'资料类型不正确。')
        with self.s.lock:
            try:limit=int(params.get('limit','40'))
            except (ValueError,TypeError):self.error(400,'分页大小不正确。')
            if not 1<=limit<=100:self.error(400,'每页最多读取 100 条。')
            q=params.get('q','').strip();category=params.get('category','');tag=params.get('tag','').strip();month=params.get('month','');state=params.get('state','');archived=params.get('archived','false')
            selected_day=params.get('day','')
            if selected_day:
                try:
                    if dt.date.fromisoformat(selected_day).isoformat()!=selected_day:raise ValueError()
                except (ValueError,TypeError):self.error(400,'日期不正确。')
            if len(q)>200 or len(tag)>32 or len(category)>80 or state not in ('','ready','awaitingText','pending','confirmed','rejected','inactive','current','manual','conflict') or archived not in ('true','false','all'):self.error(400,'筛选条件不正确。')
            clauses=['kind=?'];values=[kind]
            if archived!='all':clauses.append('archived=?');values.append(int(archived=='true'))
            if selected_day:clauses.append('day=?');values.append(selected_day)
            if kind=='memory' and not state:clauses.append("state!='rejected'")
            if state:clauses.append('state=?');values.append(state)
            if category:clauses.append('category_id=?');values.append('' if category=='uncategorized' else category)
            if tag:clauses.append('id IN (SELECT id FROM library_tags WHERE kind=? AND folded=?)');values += [kind,normalized(tag)]
            if month:
                try:
                    day=dt.date.fromisoformat(month+'-01');end=dt.date(day.year+(day.month==12),1 if day.month==12 else day.month+1,1)
                except (ValueError,TypeError):self.error(400,'月份不正确。')
                clauses += ['day>=?','day<?'];values += [day.isoformat(),end.isoformat()]
            if q:
                if self.trigram and len(normalized(q))>=3:
                    clauses.append("(kind||':'||id) IN (SELECT entity FROM library_search WHERE library_search MATCH ?)")
                    values.append('"'+normalized(q).replace('"','""')+'"')
                else:
                    clauses.append('instr(search_text,?)>0');values.append(normalized(q))
            where=' AND '.join(clauses)
            total=self.db.execute('SELECT COUNT(*) FROM library_items WHERE '+where,values).fetchone()[0]
            fingerprint=hashlib.sha256(json.dumps([kind,q,category,tag,month,state,archived,selected_day]).encode()).hexdigest()[:20]
            if params.get('cursor'):
                try:
                    cursor=params['cursor'];decoded=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
                    if decoded[0]!=fingerprint or len(decoded)!=3 or not isinstance(decoded[1],(float,int)) or not isinstance(decoded[2],str):raise ValueError()
                except (ValueError,TypeError,IndexError):self.error(400,'分页位置已失效，请重新读取。')
                where+=' AND (sort_date<? OR (sort_date=? AND id<?))';values += [decoded[1],decoded[1],decoded[2]]
            rows=self.db.execute('SELECT * FROM library_items WHERE '+where+' ORDER BY sort_date DESC,id DESC LIMIT ?',values+[limit+1]).fetchall()
            page=rows[:limit];cursor=encoded([fingerprint,page[-1]['sort_date'],page[-1]['id']]) if len(rows)>limit else None
            return {'items':[self.item(r) for r in page],'total':total,'nextCursor':cursor}

    def facets(self, kind):
        with self.s.lock:
            return {'categories':self.categories(kind) if kind in DEFAULTS else [],'months':[r[0] for r in self.db.execute('SELECT DISTINCT substr(day,1,7) FROM library_items WHERE kind=? ORDER BY day DESC',(kind,))],
                'tags':[r[0] for r in self.db.execute('SELECT tag FROM library_tags WHERE kind=? GROUP BY folded ORDER BY COUNT(*) DESC,folded LIMIT 100',(kind,))]}

    def overview(self):
        with self.s.lock:
            row=self.db.execute("SELECT COUNT(*) AS count,COALESCE(SUM(json_extract(data,'$.duration')),0) AS duration,COUNT(DISTINCT day) AS days FROM library_items WHERE kind='recording'").fetchone()
            confirmed=self.db.execute("SELECT COUNT(*) FROM library_items WHERE kind='memory' AND state='confirmed' AND archived=0").fetchone()[0]
            notes=self.db.execute("SELECT COUNT(*) FROM library_items WHERE kind='note' AND archived=0").fetchone()[0]
            pending=self.db.execute("SELECT COUNT(*) FROM library_items WHERE kind='memory' AND state='pending' AND archived=0").fetchone()[0]
            today=dt.datetime.now(ZoneInfo(self.s.setting('timeZone'))).date()
            start=(today-dt.timedelta(days=6)).isoformat()
            counts={r['day']:r['count'] for r in self.db.execute("SELECT day,COUNT(*) AS count FROM library_items WHERE kind='recording' AND day>=? GROUP BY day",(start,))}
            return {'recordingCount':row['count'],'duration':row['duration'],'dayCount':self.db.execute("SELECT COUNT(DISTINCT day) FROM library_items WHERE kind IN ('recording','note')").fetchone()[0],'memoryCount':confirmed,'noteCount':notes,'pendingMemoryCount':pending,
                'week':[{'day':(today-dt.timedelta(days=i)).isoformat(),'count':counts.get((today-dt.timedelta(days=i)).isoformat(),0)} for i in range(6,-1,-1)]}

    def all_records(self, day=None):
        with self.s.lock:
            sql="SELECT * FROM library_items WHERE kind='recording'";args=[]
            if day:sql+=' AND day=?';args.append(day)
            return [self.item(r) for r in self.db.execute(sql+' ORDER BY sort_date DESC,id DESC',args)]
