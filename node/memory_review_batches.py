"""Explicit, resumable batches of versioned memory-pair advice; no decisions."""
import hashlib
import json
import threading
from memory_batches import STOP_PROBLEMS
import memory_reviews

LIMIT = 20
CHANGED = '核对范围或模型服务已变化，请重新预览后发送。'
BUSY = '另一组记忆正在核对，请稍后重试。'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class MemoryReviewBatches:
    def __init__(self, store):
        self.s, self.db = store, store.db
        self.active_id = None
        self.worker = None
        self.stopping = threading.Event()
        with self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS memory_review_batches(
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, plan TEXT NOT NULL,
                items TEXT NOT NULL, status TEXT NOT NULL, problem TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            self.db.execute("UPDATE memory_review_batches SET status='paused',problem=? WHERE status IN ('running','pausing')",
                            ('上次批量核对已中断，请预览剩余范围后继续。',))

    def error(self, status, message):
        from store import StoreError
        raise StoreError(status, message)

    def pairs(self, values):
        from store import canonical_id
        if not isinstance(values, list) or not 1 <= len(values) <= LIMIT:
            self.error(400, '每批请选择 1 至 20 组记忆。')
        pairs = []; seen = set()
        for value in values:
            if not isinstance(value, dict):self.error(400, '请选择需要核对的两条记忆。')
            pair = {k:canonical_id(value.get(k)) for k in ('incomingID','targetID')}
            for key in ('incomingRevision','targetRevision'):
                if type(value.get(key)) is not int or value[key] < 1:self.error(400, '记忆版本不正确。')
                pair[key] = value[key]
            key = (pair['incomingID'], pair['targetID'])
            if key in seen:self.error(400, '同一组记忆不能重复选择。')
            seen.add(key); pairs.append(pair)
        return pairs

    def preview(self, values):
        from store import StoreError
        with self.s.lock:
            config = self.s.analysis.configuration or {}
            items = []
            for pair in self.pairs(values):
                item = {'pair':pair, 'selection':None, 'reviewID':None, 'reusable':False, 'problem':None}
                try:
                    selected = self.s.memory_reviews.selected(pair)
                    identifier = self.s.memory_reviews.identity(selected)
                    if config.get('key') and config['key'] in json.dumps(selected, ensure_ascii=False):
                        self.error(409, '所选记忆或依据包含疑似凭证，请先移除凭证后重新核对。')
                    row = self.db.execute('SELECT status,result FROM memory_reviews WHERE id=?', (identifier,)).fetchone()
                    item.update(selection=selected, reviewID=identifier, reusable=bool(row and row['status']=='ready' and row['result']))
                except (StoreError, OSError, UnicodeError) as error:
                    item['problem'] = str(error) if isinstance(error, StoreError) else '这组记忆的依据暂时不可读取，请重新核对。'
                items.append(item)
            plan = {'items':items, 'model':config.get('model',''), 'endpoint':config.get('endpoint',''),
                    'rulesVersion':digest(memory_reviews.RULES)}
            signature = {**plan, 'nodeID':self.s.setting('nodeID'),
                         'items':[{k:v for k,v in i.items() if k!='reusable'} for i in items]}
            return {**plan, 'snapshot':digest(signature), 'configured':bool(config),
                    'newRequests':sum(not i['reusable'] and not i['problem'] for i in items),
                    'characters':sum(i['selection']['characters'] for i in items if i['selection'])}

    def row(self, identifier):
        from store import canonical_id
        row = self.db.execute('SELECT * FROM memory_review_batches WHERE id=?', (canonical_id(identifier),)).fetchone()
        if not row:self.error(404, '找不到这次批量核对。')
        return row

    def status(self, identifier):
        with self.s.lock:
            row = self.row(identifier); plan = json.loads(row['plan']); states = json.loads(row['items'])
            items = []
            for planned, state in zip(plan['items'], states):
                # Include the immutable selected content. Viewing history never sends a request.
                item = {**planned, **state}
                review = self.db.execute('SELECT status,result FROM memory_reviews WHERE id=?', (planned['reviewID'],)).fetchone()
                item['relation'] = json.loads(review['result'])['relation'] if review and review['result'] else None
                items.append(item)
            return {'id':row['id'], 'status':row['status'], 'problem':row['problem'], 'items':items,
                    'model':plan['model'], 'endpoint':plan['endpoint'], 'createdAt':row['created_at'],
                    'finishedCount':sum(i['status']=='ready' for i in items), 'pairCount':len(items)}

    def listing(self):
        with self.s.lock:
            items = []
            for row in self.db.execute('SELECT * FROM memory_review_batches ORDER BY created_at DESC,rowid DESC LIMIT 20'):
                plan = json.loads(row['plan']); states = json.loads(row['items'])
                items.append({'id':row['id'], 'status':row['status'], 'problem':row['problem'],
                              'items':[], 'pairCount':len(states), 'model':plan['model'], 'endpoint':plan['endpoint'],
                              'createdAt':row['created_at'], 'finishedCount':sum(i['status']=='ready' for i in states)})
            return {'items':items}

    def require_idle(self):
        if self.stopping.is_set() or self.active_id or self.s.memory_reviews.run_lock.locked():self.error(409, BUSY)

    def start(self, values, background=True):
        from store import canonical_id, stamp
        if values.get('sendToModel') is not True:self.error(400, '请预览并明确发送所选记忆及依据。')
        key = canonical_id(values.get('requestID')); pairs = self.pairs(values.get('pairs'))
        fingerprint = digest([pairs, values.get('snapshot')])
        with self.s.lock:
            old = self.db.execute('SELECT fingerprint FROM memory_review_batches WHERE id=?', (key,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:self.error(409, CHANGED)
                return self.status(key)
            self.require_idle(); plan = self.preview(pairs)
            if values.get('snapshot') != plan['snapshot']:self.error(409, CHANGED)
            if any(i['problem'] for i in plan['items']):self.error(409, '所选记忆有未就绪项，请移除或重新选择后预览。')
            if plan['newRequests'] and not plan['configured']:self.error(409, '请先在电脑设置中配置文字模型。')
            now = stamp()
            with self.db:self.db.execute('INSERT INTO memory_review_batches VALUES (?,?,?,?,?,?,?,?)',
                (key, fingerprint, json.dumps(plan), json.dumps([{'status':'queued','problem':None} for _ in pairs]), 'running', None, now, now))
            self.launch(key, background)
            return self.status(key)

    def resume_preview(self, key):
        with self.s.lock:
            state = self.status(key)
            remaining = [i['pair'] for i in state['items'] if i['status']!='ready']
            if not remaining:self.error(409, '这次批量核对已经完成。')
            return self.preview(remaining)

    def resume(self, key, values, background=True):
        from store import stamp
        if values.get('sendToModel') is not True:self.error(400, '请预览并明确发送所选记忆及依据。')
        with self.s.lock:
            state = self.status(key)
            if state['status'] in ('ready','running','pausing'):return state
            self.require_idle(); preview = self.resume_preview(key)
            plan = json.loads(self.row(key)['plan'])
            if (values.get('snapshot') != preview['snapshot'] or
                    preview['rulesVersion']!=plan['rulesVersion'] or
                    (preview['newRequests'] and any(preview[k]!=plan[k] for k in ('model','endpoint')))):self.error(409, CHANGED)
            remaining = [i for i, current in zip(plan['items'], state['items']) if current['status']!='ready']
            if any(a['selection']!=b['selection'] or a['reviewID']!=b['reviewID'] or b['problem']
                   for a,b in zip(remaining, preview['items'])):self.error(409, CHANGED)
            if preview['newRequests'] and not preview['configured']:self.error(409, '请先在电脑设置中配置文字模型。')
            states = [{'status':i['status'] if i['status']=='ready' else 'queued','problem':None} for i in state['items']]
            with self.db:self.db.execute("UPDATE memory_review_batches SET status='running',problem=NULL,items=?,updated_at=? WHERE id=?",
                (json.dumps(states), stamp(), state['id']))
            self.launch(state['id'], background)
            return self.status(state['id'])

    def pause(self, key):
        from store import stamp
        with self.s.lock:
            state = self.status(key)
            if state['status']=='running':
                with self.db:self.db.execute("UPDATE memory_review_batches SET status='pausing',updated_at=? WHERE id=?", (stamp(),state['id']))
            return self.status(state['id'])

    def launch(self, key, background):
        self.active_id = key
        if background:
            self.worker = threading.Thread(target=self.run, args=(key,), daemon=True); self.worker.start()
        else:self.run(key)

    def run(self, key):
        from store import StoreError, stamp
        problem = None
        try:
            with self.s.lock:
                row = self.row(key); plan = json.loads(row['plan']); states = json.loads(row['items'])
            for position, item in enumerate(plan['items']):
                if states[position]['status']=='ready':continue
                with self.s.lock:
                    if self.stopping.is_set() or self.row(key)['status']=='pausing':break
                    states[position] = {'status':'running','problem':None}
                    with self.db:self.db.execute('UPDATE memory_review_batches SET items=?,updated_at=? WHERE id=?', (json.dumps(states),stamp(),key))
                try:
                    with self.s.lock:
                        selected = self.s.memory_reviews.selected(item['pair'])
                        if selected!=item['selection'] or digest(memory_reviews.RULES)!=plan['rulesVersion']:self.error(409, CHANGED)
                        saved = self.db.execute('SELECT status,result FROM memory_reviews WHERE id=?',(item['reviewID'],)).fetchone()
                        if saved and saved['status']=='ready' and saved['result']:
                            state = {'status':'ready', 'problem':None}
                        else:
                            config = self.s.analysis.configuration or {}
                            if config.get('model')!=plan['model'] or config.get('endpoint')!=plan['endpoint']:self.error(409, CHANGED)
                            token = self.s.memory_reviews.approval(item['reviewID'],config)
                            state = None
                    if state is None:
                        state = self.s.memory_reviews.start({'pair':item['pair'],'previewToken':token,'sendToModel':True},background=False,batch_id=key)
                    states[position] = {'status':state['status'],'problem':state.get('problem')}
                    if state['status']!='ready':problem = state.get('problem') or '这组记忆核对未完成，请重试。'
                except Exception as error:
                    problem = str(error) if isinstance(error,StoreError) else '批量核对未完成，已保存结果保留。'
                    states[position] = {'status':'failed','problem':problem}
                with self.s.lock,self.db:
                    self.db.execute('UPDATE memory_review_batches SET items=?,updated_at=? WHERE id=?', (json.dumps(states),stamp(),key))
                if problem in STOP_PROBLEMS or problem in (CHANGED,BUSY):break
        finally:
            with self.s.lock,self.db:
                current = self.row(key)['status']
                status = 'ready' if all(i['status']=='ready' for i in states) else 'paused' if current=='pausing' or self.stopping.is_set() else 'partial'
                self.db.execute('UPDATE memory_review_batches SET status=?,problem=?,updated_at=? WHERE id=?', (status,problem,stamp(),key))
                self.active_id = None

    def close(self):
        self.stopping.set()
        if self.worker and self.worker.is_alive():self.worker.join(timeout=2)
        return not self.worker or not self.worker.is_alive()
