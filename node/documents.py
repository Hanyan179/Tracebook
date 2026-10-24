"""Explicit local text-document imports with immutable source revisions."""
import datetime as dt
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import uuid
import threading
import time
from local_sources import redact
from pdf_documents import PDFError, extract_pdf
from docx_documents import DOCXError, extract_docx
from document_originals import OriginalError, retain_original, read_original, original_state

NAMESPACE = uuid.UUID('2132fb96-e68f-4a7b-8bd2-697b876fcb0d')
MAX_REQUEST = 16 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
CONTENT_ROLES = {
    'unknown': '性质未确认',
    'user_text': '本人原文（导入时指定）',
    'transcript': '转录文字（说话人未确认）',
    'generated': '模型生成内容',
}


def archive_header(item, previous, legacy_original_notice=False):
    """The exact app-written prefix, shared by imports and conservative indexing."""
    basis = '文件修改时间' if item['dateBasis'] == 'modified' else '导入时间（文件时间未知）'
    body = '# '+item['title']+'\n\n来源：导入资料\n格式：'+item['format']+'\n'+basis+'：'+item['created_at']+'\n'
    body += '作者：未确认。资料内容不自动成为本人事实或长期记忆。\n'
    role = item.get('contentRole', 'unknown')
    if role != 'unknown':
        body += '资料性质：'+CONTENT_ROLES[role]+'。此标记不代表内容已核实。\n'
        if role == 'generated':
            body += '记忆整理：不从模型生成内容提取本人事实，请回到原始来源核对。\n'
    if previous:body += '此前导入版本：['+previous+']('+previous+'.md)\n'
    if item['redacted']:body += '已隐藏识别到的常见凭证；原文件未修改。\n'
    details = item.get('details', {})
    if details.get('format') == 'pdf':
        body += 'PDF 提取文字：保留文件页序；未识别图片中的文字，阅读顺序与表格可能需要校对。\n'
        body += ('原文件仍保留在选择的位置；本资料夹保存提取文字。\n' if legacy_original_notice else
                 '原文件保持原位；此页为提取文字，原件副本状态请在应用中核对。\n')
    elif details.get('format') == 'docx':
        body += 'DOCX 提取文字：按文档结构读取正文、表格与附注，编号表示提取顺序，不是原文件页码。\n'
        body += '样式、自动编号和版式未还原；表格、修订、域结果及遗漏内容请对照原文件。\n'
        body += ('批注与附注不代表本人陈述；原文件保持原位，资料夹仅保存提取文字。\n' if legacy_original_notice else
                 '批注与附注不代表本人陈述；原文件保持原位，原件副本状态请在应用中核对。\n')
    return body+'\n---\n\n'


class DocumentImports:
    def __init__(self, store):
        self.s = store
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS documents(
                id TEXT PRIMARY KEY,title TEXT NOT NULL,source_path TEXT NOT NULL,format TEXT NOT NULL,
                source_hash TEXT NOT NULL,created_at TEXT NOT NULL,imported_at TEXT NOT NULL,
                previous_id TEXT,redacted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS document_locations(path_hash TEXT PRIMARY KEY,document_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS document_previews(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,
                state TEXT NOT NULL,digest TEXT NOT NULL,report TEXT NOT NULL);
        ''')
        if 'details' not in {r['name'] for r in store.db.execute('PRAGMA table_info(documents)')}:
            store.db.execute("ALTER TABLE documents ADD COLUMN details TEXT NOT NULL DEFAULT '{}'")
        with store.db:
            store.db.execute("UPDATE document_previews SET state='interrupted' WHERE state='preparing'")
        self.worker = None
        self.cancel_event = threading.Event()
        self.active_id = None
        self.stopping = False
        # Only remove known parser cache files left by a terminated node. Never
        # traverse a linked directory or remove unfamiliar files.
        scratch = store.data/'document-previews'
        if scratch.is_dir() and not scratch.is_symlink():
            for kind in ('pdf', 'docx'):
                for folder in scratch.glob('.'+kind+'-*'):
                    if folder.is_symlink() or not folder.is_dir():continue
                    for name in ('input.'+kind, 'result.json'):
                        path = folder/name
                        if path.is_file() and not path.is_symlink():
                            try:path.unlink()
                            except OSError:pass
                    try:folder.rmdir()
                    except OSError:pass

    def error(self, status, message):
        from store import StoreError
        raise StoreError(status, message)

    @staticmethod
    def stamp():
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

    def preparation(self, rid):
        from store import canonical_id
        with self.s.lock:
            row = self.s.db.execute('SELECT * FROM document_previews WHERE id=?', (canonical_id(rid),)).fetchone()
            if not row:self.error(404, '找不到这次资料预览，请重新选择文件。')
            report = json.loads(row['report'])
            return {'id': row['id'], 'state': 'ready' if row['state'] == 'preview' else row['state'],
                    'completed': report.get('completed', report.get('selected', 0)),
                    'total': report.get('total', report.get('selected', 0)), 'currentFile': report.get('currentFile'),
                    'problem': report.get('problem'), 'preview': report if row['state'] == 'preview' else None}

    def prepare(self, values):
        from store import canonical_id
        rid = canonical_id(values.get('requestID'))
        files = values.get('files')
        if not isinstance(files, list) or not 1 <= len(files) <= 100:
            self.error(400, '每次请选择 1 至 100 份文字资料。')
        fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.s.lock:
            old = self.s.db.execute('SELECT report FROM document_previews WHERE id=?', (rid,)).fetchone()
            if old:
                if json.loads(old['report']).get('requestHash') != fingerprint:
                    self.error(409, '这次资料选择已变化，请重新选择文件。')
                return self.preparation(rid)
            if self.stopping or (self.worker and self.worker.is_alive()):
                self.error(409, '已有资料正在解析，请等待完成或先取消。')
            report = {'completed': 0, 'total': len(files), 'currentFile': None, 'requestHash': fingerprint}
            with self.s.db:
                self.s.db.execute('INSERT INTO document_previews VALUES (?,?,?,?,?)',
                                 (rid, self.stamp(), 'preparing', '', json.dumps(report)))
            self.cancel_event = threading.Event();self.active_id = rid
            self.worker = threading.Thread(target=self._prepare, args=(rid, values, self.cancel_event), daemon=True)
            try:self.worker.start()
            except Exception:
                self._failed(rid, '资料解析未启动，请重新选择文件。');raise
            return self.preparation(rid)

    def _failed(self, rid, message):
        with self.s.lock, self.s.db:
            row = self.s.db.execute('SELECT report FROM document_previews WHERE id=?', (rid,)).fetchone()
            report = json.loads(row['report']) if row else {}
            report['problem'] = message
            self.s.db.execute("UPDATE document_previews SET state='failed',report=? WHERE id=? AND state='preparing'",
                              (json.dumps(report), rid))

    def _prepare(self, rid, values, cancel):
        from store import StoreError
        try:self.preview(values, rid, cancel)
        except Exception as error:
            if not cancel.is_set():
                self._failed(rid, str(error) if isinstance(error, StoreError) else '资料解析未完成，请重新选择文件。')

    def close(self):
        self.stopping = True;self.cancel_event.set()
        if self.worker and self.worker.is_alive():self.worker.join(timeout=3)
        return not self.worker or not self.worker.is_alive()

    def preview(self, values, preparation_id=None, cancel=None):
        files = values.get('files')
        if not isinstance(files, list) or not 1 <= len(files) <= 100:
            self.error(400, '每次请选择 1 至 100 份文字资料。')
        items, warnings, seen = [], [], set()
        duplicate = 0
        total = 0
        details_report = []
        docx_report = []
        fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest() if preparation_id else None
        deadline = time.monotonic() + 40
        for index, value in enumerate(files):
            if cancel and cancel.is_set():raise PDFError('cancelled')
            if not isinstance(value, dict):
                self.error(400, '资料信息格式不正确。')
            name = re.sub(r'[\x00-\x1f]', ' ', Path(str(value.get('name', ''))).name)[:160]
            text = value.get('text')
            role = value.get('contentRole', 'unknown')
            if not isinstance(role, str) or role not in CONTENT_ROLES:
                self.error(400, '资料性质不受支持，请重新选择。')
            suffix = Path(name).suffix.lower()
            reason = value.get('error')
            details = {}
            raw_original = None
            retain = value.get('retainOriginal', False)
            if type(retain) is not bool:self.error(400, '请选择是否保留原文件副本。')
            if preparation_id:
                with self.s.lock, self.s.db:
                    self.s.db.execute("UPDATE document_previews SET report=? WHERE id=? AND state='preparing'",
                        (json.dumps({'completed': index, 'total': len(files), 'currentFile': name, 'requestHash': fingerprint}), preparation_id))
            if suffix == '.pdf' and not reason:
                try:
                    encoded = value.get('data')
                    if not isinstance(encoded, str):raise PDFError('invalid_pdf')
                    if len(encoded) > 4 * ((MAX_FILE+2)//3):raise PDFError('too_large')
                    try:raw_pdf = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError):raise PDFError('invalid_pdf')
                    total += len(raw_pdf)
                    raw_original = raw_pdf
                    if total > 12 * 1024 * 1024:self.error(413, '本批资料超过 12 MB，请分批导入。')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:raise PDFError('not_processed')
                    extracted = extract_pdf(raw_pdf, self.s.data/'document-previews', cancel, seconds=min(10, remaining))
                    if not any(page['status'] == 'text' for page in extracted['pages']):
                        raise PDFError('invalid_pdf' if any(p['status'] == 'unreadable' for p in extracted['pages']) else 'no_text')
                    parts = [];pages = [];position = 0;hidden = False
                    for page in extracted['pages']:
                        heading = '## 第 '+str(page['number'])+' 页\n\n'
                        passage, redacted = redact(page['text']);hidden = hidden or redacted
                        if page['status'] != 'text':
                            passage = '此页未提取到文字，请核对原文件。' if page['status'] == 'empty' else '此页解析失败，请核对原文件。'
                        parts.append(heading+passage+'\n\n')
                        pages.append({'number': page['number'], 'status': page['status'],
                                      'start': position+len(heading), 'end': position+len(heading)+len(passage)})
                        position += len(parts[-1])
                    text = ''.join(parts)
                    details = {'format': 'pdf', 'parser': extracted.get('parser', 'unknown'), 'pages': pages,
                        'pageCount': len(pages), 'originalFileSHA256': hashlib.sha256(raw_pdf).hexdigest(),
                        'parserWarnings': extracted['parserWarnings'], 'redacted': bool(hidden)}
                    if len(text.encode()) > MAX_FILE:raise PDFError('text_limit')
                except PDFError as error:
                    if str(error) == 'cancelled':raise
                    reason = str(error)
            elif suffix == '.docx' and not reason:
                try:
                    encoded = value.get('data')
                    if not isinstance(encoded, str):raise DOCXError('invalid_docx')
                    if len(encoded) > 4 * ((MAX_FILE+2)//3):raise DOCXError('too_large')
                    try:raw_docx = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError):raise DOCXError('invalid_docx')
                    total += len(raw_docx)
                    raw_original = raw_docx
                    if total > 12 * 1024 * 1024:self.error(413, '本批资料超过 12 MB，请分批导入。')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:raise DOCXError('not_processed')
                    extracted = extract_docx(raw_docx, self.s.data/'document-previews', cancel, seconds=min(10, remaining))
                    pieces = [];blocks = [];position = 0;hidden = False
                    names = {'body': '正文', 'header': '页眉', 'footer': '页脚', 'footnote': '脚注', 'endnote': '尾注', 'comment': '批注'}
                    by_part = {}
                    for block in extracted['blocks']:by_part.setdefault(block['part'], []).append(block)
                    for part in extracted['parts']:
                        heading = '## '+names[part['kind']]+('' if part['kind'] == 'body' else ' '+str(part['number']))+'\n\n'
                        pieces.append(heading);position += len(heading)
                        previous_cell = None
                        for block in by_part.get(part['id'], []):
                            location = (block.get('table'), block.get('row'), block.get('cell'))
                            if block['kind'] == 'table_cell' and location != previous_cell:
                                label = '表格 '+str(location[0])+' · 第 '+str(location[1])+' 行 · 第 '+str(location[2])+' 个单元格\n\n'
                                pieces.append(label);position += len(label)
                            previous_cell = location
                            passage, redacted = redact(block['text']);hidden = hidden or redacted
                            blocks.append({k:v for k,v in block.items() if k != 'text'})
                            blocks[-1].update(start=position, end=position+len(passage))
                            pieces.append(passage+'\n\n');position += len(passage)+2
                    text = ''.join(pieces)
                    if len(text.encode()) > MAX_FILE:raise DOCXError('text_limit')
                    details = {'format': 'docx', 'parser': extracted['parser'], 'parts': extracted['parts'],
                        'blocks': blocks, 'paragraphCount': extracted['paragraphCount'], 'tableCount': extracted['tableCount'],
                        'warnings': extracted['warnings'], 'parserWarnings': bool(extracted['warnings']),
                        'originalFileSHA256': hashlib.sha256(raw_docx).hexdigest(), 'redacted': bool(hidden)}
                except DOCXError as error:
                    if str(error) == 'cancelled':raise
                    reason = str(error)
            if suffix not in ('.md', '.markdown', '.txt', '.text', '.pdf', '.docx'):
                reason = 'unsupported'
            elif not isinstance(text, str) or not text.strip() or '\0' in text:
                reason = reason or 'unreadable'
            elif len(text.encode()) > MAX_FILE:
                reason = 'too_large'
            if reason:
                allowed = ('unsupported', 'too_large', 'encrypted', 'invalid_pdf', 'page_limit', 'text_limit',
                           'no_text', 'parser_limit', 'parser_timeout', 'parser_unavailable', 'parser_busy', 'not_processed', 'batch_limit',
                           'invalid_docx', 'encrypted_docx', 'unsupported_docx')
                warnings.append({'name': name, 'reason': reason if reason in allowed else 'unreadable'})
                continue
            if suffix not in ('.pdf', '.docx'):total += len(text.encode())
            if total > 12 * 1024 * 1024:
                self.error(413, '本批资料超过 12 MB，请分批导入。')
            if suffix in ('.pdf', '.docx'):hidden = details['redacted']
            else:text, hidden = redact(text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n'))
            signature = hashlib.sha256(((details['format']+'-v1\0'+details['originalFileSHA256']+'\0') if details else '').encode()+text.encode()).hexdigest()
            # Preserve legacy IDs for unclassified imports. A different explicit
            # classification is a new revision, never an in-place trust upgrade.
            if role != 'unknown':
                signature = hashlib.sha256(('document-role-v1\0'+role+'\0'+signature).encode()).hexdigest()
            rid = str(uuid.uuid5(NAMESPACE, signature)).upper()
            if rid in seen:
                duplicate += 1
                if retain and raw_original:
                    prior = next(item for item in items if item['id'] == rid)
                    if hidden:prior['originalBlocked'] = True
                    else:prior['originalData'] = base64.b64encode(raw_original).decode()
                continue
            seen.add(rid)
            if details.get('format') == 'pdf':
                details_report.append({'name': name, 'pages': details['pageCount'],
                    'textPages': sum(p['status'] == 'text' for p in details['pages']),
                    'emptyPages': [p['number'] for p in details['pages'] if p['status'] == 'empty'],
                    'failedPages': [p['number'] for p in details['pages'] if p['status'] == 'unreadable'],
                    'parserWarnings': details['parserWarnings']})
            elif details.get('format') == 'docx':
                docx_report.append({'name': name, 'paragraphs': details['paragraphCount'], 'tables': details['tableCount'],
                                    'parts': details['parts'], 'blocks': len(details['blocks']), 'warnings': details['warnings']})
            if role != 'unknown':details = {**details, 'contentRole': role}
            source = value.get('sourcePath')
            source = source if isinstance(source, str) and 0 < len(source) <= 4096 else name
            moment = self.stamp()
            date_basis = 'imported'
            try:
                parsed = dt.datetime.fromisoformat(value.get('modifiedAt', '').replace('Z', '+00:00'))
                if not parsed.tzinfo or not 1970 <= parsed.year <= 2200:
                    raise ValueError()
                moment = parsed.astimezone(dt.timezone.utc).isoformat()
                date_basis = 'modified'
            except (ValueError, TypeError, AttributeError):
                pass
            with self.s.lock:
                existing = bool(self.s.db.execute('SELECT 1 FROM documents WHERE id=?', (rid,)).fetchone())
            items.append({'id': rid, 'title': name, 'text': text, 'source': source, 'format': suffix.lstrip('.'),
                          'signature': signature, 'created_at': moment, 'dateBasis': date_basis,
                          'redacted': hidden, 'existing': existing, 'details': details, 'contentRole': role,
                          'originalData': base64.b64encode(raw_original).decode() if retain and raw_original and not hidden else None,
                          'originalBlocked': bool(retain and raw_original and hidden)})
        rid = preparation_id or str(uuid.uuid4()).upper()
        report = {'id': rid, 'selected': len(files), 'accepted': len(items),
                  'added': sum(not i['existing'] for i in items),
                  'duplicates': duplicate+sum(i['existing'] for i in items),
                  'redacted': sum(i['redacted'] for i in items), 'warnings': warnings,
                  'titles': [i['title'] for i in items], 'pdfs': details_report,
                  'docxDocuments': docx_report,
                  'originalCopies': [{'name': i['title'], 'state': 'blocked' if i['originalBlocked'] else 'selected',
                                      'bytes': len(base64.b64decode(i['originalData'])) if i['originalData'] else 0}
                                     for i in items if i['originalData'] or i['originalBlocked']],
                  'sourceRoles': [{'name': i['title'], 'contentRole': i['contentRole']} for i in items],
                  'samples': [{'name': i['title'], 'text': i['text'][:400]} for i in items[:3]]}
        if fingerprint:report['requestHash'] = fingerprint
        data = json.dumps(items, ensure_ascii=False).encode()
        folder = self.s.data/'document-previews';folder.mkdir(exist_ok=True, mode=0o700)
        with self.s.lock, self.s.db:
            if cancel and cancel.is_set():raise PDFError('cancelled')
            self.s._atomic(folder/(rid+'.json'), data)
            self.s.db.execute('INSERT OR REPLACE INTO document_previews VALUES (?,?,?,?,?)',
                              (rid, self.stamp(), 'preview', hashlib.sha256(data).hexdigest(), json.dumps(report)))
        return report

    def commit(self, rid):
        from store import canonical_id
        rid = canonical_id(rid)
        with self.s.lock:
            row = self.s.db.execute('SELECT * FROM document_previews WHERE id=?', (rid,)).fetchone()
            if not row:
                self.error(404, '找不到这次资料预览。')
            if row['state'] == 'complete':
                saved = json.loads(row['report'])
                return {key: saved[key] for key in ('added', 'skipped', 'versions', 'originalsSaved') if key in saved}
            if row['state'] != 'preview':self.error(409, '资料预览尚未就绪，请重新选择文件。')
            if (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(row['created_at'])).total_seconds() > 1800:
                self.error(409, '预览已过期，请重新选择资料。')
            file = self.s.data/'document-previews'/(rid+'.json')
            raw = file.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['digest']:
                self.error(409, '资料预览已变化，请重新选择。')
            items = json.loads(raw)
            # Within-batch duplicates were removed during preview. Include them
            # in the final result as well as entries already present at commit.
            original_report = json.loads(row['report'])
            added = versions = originals_saved = 0
            skipped = max(0, original_report.get('duplicates', 0) - sum(bool(i.get('existing')) for i in items))
            for item in items:
                path_hash = hashlib.sha256(item['source'].encode()).hexdigest()
                old = self.s.db.execute('SELECT document_id FROM document_locations WHERE path_hash=?', (path_hash,)).fetchone()
                previous = old[0] if old and old[0] != item['id'] else None
                exists = self.s.db.execute('SELECT details FROM documents WHERE id=?', (item['id'],)).fetchone()
                if exists:
                    skipped += 1
                    if item.get('originalData'):
                        details = json.loads(exists['details'])
                        if details.get('originalFileSHA256') != item['details'].get('originalFileSHA256'):
                            self.error(409, '原件与已导入资料不一致，请重新选择文件。')
                        original = self._retain_original(item);originals_saved += 1
                        with self.s.db:
                            self.s.db.execute('UPDATE documents SET details=? WHERE id=?',
                                (json.dumps({**details, 'original': original}), item['id']))
                else:
                    body = archive_header(item, previous)
                    if previous:versions += 1
                    details = item.get('details', {})
                    if details.get('format') == 'pdf':
                        details = {**details, 'pages': [{**p, 'start': p['start']+len(body), 'end': p['end']+len(body)} for p in details['pages']]}
                    elif details.get('format') == 'docx':
                        details = {**details, 'blocks': [{**b, 'start': b['start']+len(body), 'end': b['end']+len(body)} for b in details['blocks']]}
                    body += item['text']+'\n'
                    if details.get('format') in ('pdf', 'docx'):details['textVersion'] = hashlib.sha256(body.encode()).hexdigest()
                    output = self.s.export('documents/'+item['id']+'.md', body, item['signature'])
                    if output['state'] == 'conflict':
                        self.error(409, '资料目标已有人工内容，已保留，请先核对另存版本。')
                    if item.get('originalData'):
                        details = {**details, 'original': self._retain_original(item)};originals_saved += 1
                    with self.s.db:
                        self.s.db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (item['id'], item['title'], item['source'], item['format'], item['signature'],
                             item['created_at'], self.stamp(), previous, int(item['redacted']), json.dumps(details)))
                    added += 1
                with self.s.db:
                    self.s.db.execute('INSERT OR REPLACE INTO document_locations VALUES (?,?)', (path_hash, item['id']))
                self.s.catalog.index_document(item['id'])
            report = {'added': added, 'skipped': skipped, 'versions': versions, 'originalsSaved': originals_saved}
            stored_report = dict(report)
            if original_report.get('requestHash'):
                stored_report.update(requestHash=original_report['requestHash'], selected=original_report['selected'])
            with self.s.db:
                self.s.db.execute("UPDATE document_previews SET state='complete',report=? WHERE id=?", (json.dumps(stored_report), rid))
            file.unlink(missing_ok=True)
            return report

    def cancel(self, rid):
        from store import canonical_id
        rid = canonical_id(rid)
        with self.s.lock, self.s.db:
            if self.active_id == rid:self.cancel_event.set()
            row = self.s.db.execute('SELECT state FROM document_previews WHERE id=?', (rid,)).fetchone()
            if row and row[0] in ('preview', 'preparing', 'interrupted', 'failed'):
                (self.s.data/'document-previews'/(rid+'.json')).unlink(missing_ok=True)
                self.s.db.execute("UPDATE document_previews SET state='cancelled' WHERE id=?", (rid,))
        return {'cancelled': True}

    def _retain_original(self, item):
        try:
            raw = base64.b64decode(item['originalData'], validate=True)
            return retain_original(self.s.output, item['id'], item['format'], raw,
                                   item['details']['originalFileSHA256'])
        except (ValueError, binascii.Error, OriginalError) as error:
            self.error(409, str(error) if isinstance(error, OriginalError) else '原件内容与预览不一致，请重新选择文件。')

    def info(self, rid, version):
        row = self.s.db.execute('SELECT details FROM documents WHERE id=?', (rid,)).fetchone()
        details = json.loads(row['details']) if row else {}
        if details.get('format') not in ('pdf', 'docx'):return None
        valid = details['textVersion'] == version
        details = {**details, 'originalCopy': original_state(self.s.output, rid, details['format'], details)}
        if details['format'] == 'docx':
            return {**details, 'rangesValid': valid, 'blocks': details['blocks'] if valid else
                    [{k:v for k,v in b.items() if k not in ('start', 'end')} for b in details['blocks']]}
        return {**details, 'rangesValid': valid,
                'pages': details['pages'] if valid else [{k:v for k,v in p.items() if k in ('number','status')} for p in details['pages']]}

    def original(self, identifier):
        from store import canonical_id
        with self.s.lock:
            rid = canonical_id(identifier)
            row = self.s.db.execute('SELECT title,format,details FROM documents WHERE id=?', (rid,)).fetchone()
            if not row:self.error(404, '找不到这份资料。')
            try:raw = read_original(self.s.output, rid, row['format'], json.loads(row['details']))
            except OriginalError as error:self.error(409, str(error))
            return {'name': row['title'], 'format': row['format'], 'byteCount': len(raw),
                    'sha256': hashlib.sha256(raw).hexdigest(), 'data': base64.b64encode(raw).decode()}

    def content_role(self, rid):
        row = self.s.db.execute('SELECT details FROM documents WHERE id=?', (rid,)).fetchone()
        role = json.loads(row['details']).get('contentRole', 'unknown') if row else 'unknown'
        return role if role in CONTENT_ROLES else 'unknown'

    @staticmethod
    def content_start(item, body):
        """Skip only an exact known prefix, never arbitrary user Markdown front matter.

        Older imports did not store dateBasis. Both app-generated date labels are
        reconstructible from their immutable row, so no source rewrite is needed.
        If the user edited or prepended to the prefix, keep the whole text indexed.
        """
        details = json.loads(item['details'])
        role = details.get('contentRole', 'unknown')
        if role not in CONTENT_ROLES:return 0
        for basis in ('modified', 'imported'):
            for legacy in (False, True):
                prefix = archive_header({**dict(item), 'details': details, 'contentRole': role, 'dateBasis': basis},
                                        item['previous_id'], legacy_original_notice=legacy)
                if body.startswith(prefix):return len(prefix)
        return 0

    def revision(self, identifier):
        row = self.s.db.execute('SELECT previous_id,imported_at,source_path FROM documents WHERE id=?', (identifier,)).fetchone()
        if not row:return None
        location = self.s.db.execute('SELECT document_id FROM document_locations WHERE path_hash=?',
            (hashlib.sha256(row['source_path'].encode()).hexdigest(),)).fetchone()
        return {'previousID': row['previous_id'], 'importedAt': row['imported_at'],
                'isLatestImport': location[0] == identifier if location else None}

    def listing(self, parameters):
        try:
            offset = max(0, int(parameters.get('offset', 0)))
        except (ValueError, TypeError):
            self.error(400, '读取位置不正确。')
        with self.s.lock:
            rows = self.s.db.execute('SELECT id FROM documents ORDER BY imported_at DESC,id DESC LIMIT 41 OFFSET ?', (offset,)).fetchall()
            items = []
            for row in rows[:40]:
                self.s.catalog.index_document(row['id'])
                source = self.s.db.execute("SELECT * FROM library_items WHERE kind='document' AND id=?", (row['id'],)).fetchone()
                item = self.s.knowledge.source_data(source, source['body'])
                items.append(item)
            return {'items': items, 'count': self.s.db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],
                    'originalCopiesSupported': True,
                    'nextOffset': offset+40 if len(rows)>40 else None}
