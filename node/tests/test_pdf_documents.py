import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
import uuid
import zipfile
from unittest.mock import patch
import test_node as fixtures
from store import Store, StoreError
from pdf_documents import extract_pdf, supervise, worker_command, PDFError

# Use the same pinned dependency location as the bundled worker during local tests.
bundled=Path(__file__).resolve().parents[2]/'DerivedData/PythonRuntime/common'
if (bundled/'pypdf').is_dir():sys.path.insert(0,str(bundled))
from pypdf import PdfWriter
from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject,ArrayObject,NumberObject,TextStringObject


def pdf_bytes(texts, encrypted=False):
    writer=PdfWriter()
    for text in texts:
        page=writer.add_blank_page(width=2000,height=792)
        if text is None:continue
        # A deterministic ToUnicode map gives Linux and Mac the same Unicode fixture.
        chars=list(dict.fromkeys(text));codes={char:index+1 for index,char in enumerate(chars)}
        mapping='\n'.join('<%04X> <%s>'%(codes[c],c.encode('utf-16-be').hex().upper()) for c in chars)
        cmap=DecodedStreamObject();cmap.set_data(('/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n'
            '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n'
            '/CMapName /Synthetic-UCS def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n'
            +str(len(chars))+' beginbfchar\n'+mapping+'\nendbfchar\nendcmap\n'
            'CMapName currentdict /CMap defineresource pop\nend\nend').encode())
        descriptor=DictionaryObject({NameObject('/Type'):NameObject('/FontDescriptor'),
            NameObject('/FontName'):NameObject('/SyntheticUnicode'),NameObject('/Flags'):NumberObject(4),
            NameObject('/FontBBox'):ArrayObject([NumberObject(n) for n in [0,-200,1000,900]]),
            NameObject('/ItalicAngle'):NumberObject(0),NameObject('/Ascent'):NumberObject(880),
            NameObject('/Descent'):NumberObject(-120),NameObject('/CapHeight'):NumberObject(700),NameObject('/StemV'):NumberObject(80)})
        descendant=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/CIDFontType2'),
            NameObject('/BaseFont'):NameObject('/SyntheticUnicode'),NameObject('/DW'):NumberObject(1000),
            NameObject('/FontDescriptor'):writer._add_object(descriptor),NameObject('/CIDToGIDMap'):NameObject('/Identity'),
            NameObject('/CIDSystemInfo'):DictionaryObject({NameObject('/Registry'):TextStringObject('Adobe'),
                NameObject('/Ordering'):TextStringObject('Identity'),NameObject('/Supplement'):NumberObject(0)})})
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type0'),
            NameObject('/BaseFont'):NameObject('/SyntheticUnicode'),NameObject('/Encoding'):NameObject('/Identity-H'),
            NameObject('/ToUnicode'):writer._add_object(cmap),NameObject('/DescendantFonts'):ArrayObject([writer._add_object(descendant)])})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 40 740 Td <'+''.join('%04X'%codes[c] for c in text)+'> Tj ET').encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    if encrypted:writer.encrypt('synthetic-test-only')
    data=io.BytesIO();writer.write(data);return data.getvalue()


class PDFImportTests(unittest.TestCase):
    setUp=fixtures.NodeTests.setUp
    tearDown=fixtures.NodeTests.tearDown

    def item(self,raw,name='synthetic.pdf'):
        return {'name':name,'sourcePath':'/synthetic/documents/'+name,'data':base64.b64encode(raw).decode()}

    def test_real_worker_preserves_unicode_and_page_order_and_reports_empty_pages(self):
        texts=['本人陈述：每周五回顾项目。👩🏽‍💻 café','Other speaker: this is not a user fact.',None]
        raw=pdf_bytes(texts);value=extract_pdf(raw,self.root/'scratch')
        self.assertEqual([p['text'] for p in value['pages']],texts[:2]+[''])
        self.assertEqual([p['status'] for p in value['pages']],['text','text','empty'])
        self.assertEqual(list((self.root/'scratch').iterdir()),[])

    def test_preview_commit_page_positions_and_no_automatic_memory_or_external_access(self):
        raw=pdf_bytes(['项目第一页。','项目第二页。',None]);item=self.item(raw)
        preview=self.store.documents.preview({'files':[item]})
        self.assertEqual(preview['pdfs'][0]['emptyPages'],[3]);self.assertEqual(preview['added'],1)
        self.assertEqual(self.store.documents.listing({})['count'],0)
        self.store.documents.commit(preview['id'])
        source=self.store.documents.listing({})['items'][0];info=source['documentInfo']
        self.assertEqual(info['originalFileSHA256'],hashlib.sha256(raw).hexdigest());self.assertTrue(info['rangesValid'])
        whole=self.store.knowledge.read_source('document',source['id'],['documents'],{'limit':16000})
        for page,text in zip(info['pages'][:2],['项目第一页。','项目第二页。']):
            self.assertEqual(whole['text'][page['start']:page['end']],text)
        self.assertIn('作者：未确认',whole['text']);self.assertEqual(self.store.candidates(),[])
        with self.assertRaises(StoreError):self.store.knowledge.read_source('document',source['id'],['notes'],{})
        self.assertEqual(self.store.analysis.snapshot(whole['date'])['sources'],[])
        results=self.store.knowledge.query({'query':'第二页','scopes':['documents']})
        self.assertEqual(results['sources'][0]['id'],source['id'])

    def test_duplicate_binary_and_updated_revision_preserve_manual_text_and_invalidate_page_offsets(self):
        first=pdf_bytes(['Original project plan.']);values={'files':[self.item(first),self.item(first,'copy.pdf')]}
        preview=self.store.documents.preview(values);self.assertEqual(preview['duplicates'],1)
        self.assertEqual(len(preview['pdfs']),1)
        self.assertEqual(self.store.documents.commit(preview['id'])['skipped'],1)
        source=self.store.documents.listing({})['items'][0]
        file=self.store.output/'documents'/f"{source['id']}.md";file.write_text('Manual project correction.')
        again=self.store.documents.preview({'files':[self.item(first)]});self.assertEqual(again['duplicates'],1)
        self.store.documents.commit(again['id']);self.assertEqual(file.read_text(),'Manual project correction.')
        reread=self.store.knowledge.read_source('document',source['id'],['documents'],{})
        self.assertFalse(reread['documentInfo']['rangesValid']);self.assertNotIn('start',reread['documentInfo']['pages'][0])
        updated=self.store.documents.preview({'files':[self.item(pdf_bytes(['Updated project plan.']))]})
        self.assertEqual(self.store.documents.commit(updated['id'])['versions'],1)
        self.assertEqual(file.read_text(),'Manual project correction.')

    def test_encrypted_empty_malformed_oversized_and_page_limit_are_explicit(self):
        files=[self.item(pdf_bytes(['Hidden'],True),'locked.pdf'),self.item(pdf_bytes([None]),'scan.pdf'),
               self.item(b'%PDF-1.7\ninvalid','broken.pdf'),self.item(b'%PDF-'+b'x'*(2*1024*1024),'large.pdf'),
               self.item(pdf_bytes([None]*201),'pages.pdf')]
        preview=self.store.documents.preview({'files':files})
        self.assertEqual(preview['accepted'],0)
        self.assertEqual([w['reason'] for w in preview['warnings']],['encrypted','no_text','invalid_pdf','too_large','page_limit'])
        self.assertEqual(self.store.documents.listing({})['count'],0)

    def test_partial_page_failure_keeps_page_number_and_coverage(self):
        value={'pageCount':3,'parserWarnings':True,'pages':[{'number':1,'status':'text','text':'A statement.'},
             {'number':2,'status':'unreadable','text':''},{'number':3,'status':'empty','text':''}]}
        with patch('documents.extract_pdf',return_value=value):
            preview=self.store.documents.preview({'files':[self.item(b'%PDF-synthetic')]})
        self.assertEqual(preview['pdfs'][0]['failedPages'],[2]);self.assertEqual(preview['pdfs'][0]['emptyPages'],[3])
        self.store.documents.commit(preview['id']);source=self.store.documents.listing({})['items'][0]
        text=self.store.knowledge.read_source('document',source['id'],['documents'],{})['text']
        self.assertIn('第 2 页',text);self.assertIn('此页解析失败',text)

    def test_async_preparation_cancel_and_idempotence_do_not_hold_database_lock_or_save_documents(self):
        entered=threading.Event()
        def slow(_data,_path,cancel,**_kwargs):
            entered.set();cancel.wait(3);raise PDFError('cancelled')
        values={'requestID':str(uuid.uuid4()),'files':[self.item(pdf_bytes(['Synthetic text.']))]}
        with patch('documents.extract_pdf',side_effect=slow) as parse:
            job=self.store.documents.prepare(values);self.assertTrue(entered.wait(2))
            self.assertTrue(self.store.lock.acquire(timeout=1));self.store.lock.release()
            self.assertEqual(self.store.documents.prepare(values)['id'],job['id']);self.assertEqual(parse.call_count,1)
            with self.assertRaises(StoreError):self.store.documents.prepare({**values,'files':[self.item(b'changed')]})
            self.store.documents.cancel(job['id']);self.store.documents.worker.join(3)
        self.assertEqual(self.store.documents.preparation(job['id'])['state'],'cancelled')
        self.assertFalse((self.store.data/'document-previews'/f"{job['id']}.json").exists())
        self.assertEqual(self.store.documents.listing({})['count'],0)
        with self.assertRaises(StoreError):self.store.documents.commit(job['id'])

    def test_preparation_restart_recovers_ready_preview_and_marks_incomplete_without_reparsing(self):
        values={'requestID':str(uuid.uuid4()),'files':[self.item(pdf_bytes(['Ready for review.']))]}
        job=self.store.documents.prepare(values);self.store.documents.worker.join(5)
        self.assertEqual(self.store.documents.preparation(job['id'])['state'],'ready')
        interrupted=str(uuid.uuid4()).upper()
        with self.store.db:self.store.db.execute('INSERT INTO document_previews VALUES (?,?,?,?,?)',
            (interrupted,self.store.documents.stamp(),'preparing','','{}'))
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(self.store.documents.preparation(interrupted)['state'],'interrupted')
        self.assertEqual(self.store.documents.preparation(job['id'])['state'],'ready')
        self.assertEqual(self.store.documents.commit(job['id'])['added'],1)

    def test_repeated_prepare_after_completion_or_failure_keeps_request_identity(self):
        values={'requestID':str(uuid.uuid4()),'files':[self.item(pdf_bytes(['Retry-safe document.']))]}
        job=self.store.documents.prepare(values);self.store.documents.worker.join(5)
        result=self.store.documents.commit(job['id'])
        with patch('documents.extract_pdf',side_effect=AssertionError('Must not parse again')):
            repeated=self.store.documents.prepare(values)
            self.assertEqual(repeated['state'],'complete')
            self.assertEqual((repeated['completed'],repeated['total']),(1,1))
            self.assertEqual(self.store.documents.commit(job['id']),result)
        with self.assertRaises(StoreError):
            self.store.documents.prepare({**values,'files':[self.item(pdf_bytes(['Different document.']))]})
        invalid={'requestID':str(uuid.uuid4()),'files':[17]}
        failed=self.store.documents.prepare(invalid);self.store.documents.worker.join(5)
        repeated=self.store.documents.prepare(invalid)
        self.assertEqual(repeated['state'],'failed');self.assertTrue(repeated['problem'])
        self.assertEqual(repeated['id'],failed['id'])
        self.assertEqual(self.store.documents.listing({})['count'],1)

    def test_pdf_export_contains_extracted_pages_and_metadata_without_binary_or_recognized_secret(self):
        # This is a deliberately invalid alphabetical credential fixture, never a real key.
        secret='sk-proj-'+'abcdefghijklmnopqrstuv'
        preview=self.store.documents.preview({'files':[self.item(pdf_bytes(['Project '+secret]))]})
        self.assertEqual(preview['redacted'],1);self.store.documents.commit(preview['id'])
        with zipfile.ZipFile(self.store.exchange.export()) as archive:
            self.assertFalse(any(name.endswith('.pdf') for name in archive.namelist()))
            for name in archive.namelist():self.assertNotIn(secret.encode(),archive.read(name))
            manifest=json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['documents'][0]['documentInfo']['pageCount'],1)

    def test_supervisor_terminates_real_child_on_timeout_cancel_and_memory_budget(self):
        real=subprocess.Popen
        for mode in ['timeout','cancel','memory']:
            folder=self.root/mode;folder.mkdir();cancel=threading.Event();processes=[]
            script=folder/'child.py';script.write_text('import time\nbuffer=bytearray(50*1024*1024)\ntime.sleep(20)\n')
            def spawn(*args,**kwargs):
                process=real(*args,**kwargs);processes.append(process);return process
            if mode=='cancel':cancel.set()
            with patch('pdf_documents.subprocess.Popen',side_effect=spawn):
                with self.assertRaises(PDFError) as error:
                    supervise([sys.executable,str(script)],folder,cancel,seconds=.3 if mode=='timeout' else 3,
                              memory_limit=16*1024*1024 if mode=='memory' else 384*1024*1024)
            self.assertEqual(str(error.exception),{'timeout':'parser_timeout','cancel':'cancelled','memory':'parser_limit'}[mode])
            self.assertIsNotNone(processes[0].poll())

    def test_worker_stops_when_parent_pipe_closes(self):
        source=self.root/'input.pdf';source.write_bytes(pdf_bytes(['A source.']*100))
        output=self.root/'result.json'
        process=subprocess.Popen(worker_command(self.root),stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        process.stdin.close()
        try:self.assertEqual(process.wait(timeout=3),3)
        finally:
            if process.poll() is None:process.kill();process.wait()
        self.assertFalse(output.exists())

    def test_startup_cleans_only_known_parser_cache_files(self):
        scratch=self.store.data/'document-previews';cache=scratch/'.pdf-interrupted';cache.mkdir(parents=True)
        (cache/'input.pdf').write_bytes(b'synthetic cache');(cache/'result.json').write_text('{}')
        protected=scratch/'.pdf-extra';protected.mkdir();(protected/'keep.txt').write_text('Keep this unfamiliar file.')
        outside=self.root/'outside';outside.mkdir();(outside/'input.pdf').write_bytes(b'Keep linked original')
        (scratch/'.pdf-link').symlink_to(outside,target_is_directory=True)
        self.store.close();self.store=Store(self.root/'node')
        self.assertFalse(cache.exists());self.assertTrue((protected/'keep.txt').is_file())
        self.assertEqual((outside/'input.pdf').read_bytes(),b'Keep linked original')

    def test_decoding_budget_rejects_expanding_stream_without_hanging(self):
        writer=PdfWriter();page=writer.add_blank_page(width=612,height=792)
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({
            NameObject('/F1'):DictionaryObject({NameObject('/Type'):NameObject('/Font'),
                NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf '+b' ' * 9_000_000+b'(Synthetic text) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream.flate_encode())
        raw=io.BytesIO();writer.write(raw)
        folder=self.root/'scratch';folder.mkdir();source=folder/'input.pdf';source.write_bytes(raw.getvalue())
        worker=Path(__file__).resolve().parents[1]/'pdf_worker.py'
        value=supervise([sys.executable,str(worker),str(source),str(folder/'result.json')],folder,threading.Event())
        self.assertEqual(value['pages'][0]['status'],'unreadable')
        with patch('documents.extract_pdf',return_value=value):
            preview=self.store.documents.preview({'files':[self.item(raw.getvalue())]})
        self.assertEqual(preview['warnings'][0]['reason'],'invalid_pdf')

    @unittest.skipUnless(sys.platform=='darwin','The native PDF worker runs on macOS.')
    def test_native_worker_preserves_chinese_in_a_system_generated_pdf(self):
        script=self.root/'native.swift';source=self.root/'native.pdf'
        script.write_text('''import AppKit
let data=NSMutableData()
var box=CGRect(x:0,y:0,width:595,height:842)
let context=CGContext(consumer:CGDataConsumer(data:data as CFMutableData)!,mediaBox:&box,nil)!
context.beginPDFPage(nil)
NSGraphicsContext.current=NSGraphicsContext(cgContext:context,flipped:false)
("本人陈述：我每周五回顾项目计划。" as NSString).draw(at:CGPoint(x:40,y:700),withAttributes:[.font:NSFont.systemFont(ofSize:14)])
context.endPDFPage();context.closePDF()
try (data as Data).write(to:URL(fileURLWithPath:CommandLine.arguments[1]))
''')
        subprocess.run(['/usr/bin/swift',str(script),str(source)],check=True,capture_output=True,timeout=30)
        value=extract_pdf(source.read_bytes(),self.root/'scratch')
        self.assertEqual(value['parser'],'apple-pdfkit')
        self.assertIn('本人陈述：',value['pages'][0]['text'])
        self.assertIn('我每周五回顾项目计划。',value['pages'][0]['text'])
        self.assertNotIn('gid',value['pages'][0]['text'])


if __name__=='__main__':unittest.main()
