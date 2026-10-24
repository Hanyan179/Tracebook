import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
import uuid
import warnings
import zipfile
from unittest.mock import patch

import test_node as fixtures
from docx_documents import DOCXError, extract_docx
from docx_worker import InvalidDocument, extract
from store import Store, StoreError

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
CT = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'


def package(body, extras=None, relations='', main='word/document.xml', namespace=W, content_type=CT):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/'+main+'" ContentType="'+content_type+'"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="main" Type="'+R+'officeDocument" Target="'+main+'"/></Relationships>')
        z.writestr(main, '<w:document xmlns:w="'+namespace+'" xmlns:r="'+R[:-1]+'" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>'+body+'</w:body></w:document>')
        if relations:
            path = str(Path(main).parent/'_rels'/(Path(main).name+'.rels'))
            z.writestr(path, '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+relations+'</Relationships>')
        for name, text in (extras or {}).items():z.writestr(name, text)
    return result.getvalue()


def paragraph(text):return '<w:p><w:r><w:t xml:space="preserve">'+text+'</w:t></w:r></w:p>'


def relation(kind, target, extra=''):
    return '<Relationship Id="'+kind+'" Type="'+R+kind+'" Target="'+target+'" '+extra+'/>'


class DOCXImportTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown

    def item(self, data=None, name='orchard.docx', role='unknown'):
        return {'name': name, 'sourcePath': '/synthetic/documents/'+name,
                'modifiedAt': '2026-10-24T10:30:00Z', 'contentRole': role,
                'data': base64.b64encode(data if data is not None else package(paragraph('Orchard 合成资料'))).decode()}

    def test_runs_unicode_tabs_and_breaks_preserve_text_order(self):
        body = '<w:p><w:r><w:t>café 👩🏽‍💻</w:t><w:tab/></w:r><w:r><w:t>果园</w:t><w:br/><w:t>next</w:t></w:r></w:p>'
        result = extract_docx(package(body), self.root/'scratch')
        self.assertEqual(result['blocks'][0]['text'], 'café 👩🏽‍💻\t果园\nnext')
        self.assertEqual(result['paragraphCount'], 1)
        self.assertEqual(list((self.root/'scratch').iterdir()), [])

    def test_tables_and_nested_tables_keep_cell_context(self):
        cell = lambda text:'<w:tc>'+paragraph(text)+'</w:tc>'
        nested = '<w:tbl><w:tr>'+cell('Nested')+'</w:tr></w:tbl>'
        body = paragraph('Before')+'<w:tbl><w:tr>'+cell('A')+'<w:tc>'+paragraph('B')+nested+paragraph('C')+'</w:tc></w:tr></w:tbl>'+paragraph('After')
        result = extract(package(body))
        self.assertEqual([b['text'] for b in result['blocks']], ['Before','A','B','Nested','C','After'])
        self.assertEqual([(b.get('table'),b.get('row'),b.get('cell')) for b in result['blocks'][1:5]], [(1,1,1),(1,1,2),(2,1,1),(1,1,2)])
        self.assertEqual(result['tableCount'], 2)
        self.assertIn({'reason':'tables','count':2}, result['warnings'])

    def test_header_footer_notes_comments_are_separate_parts(self):
        extras = {};relations = ''
        for kind, root, child in [('header','hdr',None),('footer','ftr',None),('footnotes','footnotes','footnote'),('endnotes','endnotes','endnote'),('comments','comments','comment')]:
            content = paragraph(kind+' synthetic')
            if child:content = '<w:'+child+' w:id="2" w:author="Unverified author">'+content+'</w:'+child+'>'
            if kind == 'footnotes':content = '<w:footnote w:type="separator" w:id="-1">'+paragraph('Separator')+'</w:footnote>'+content
            extras['word/'+kind+'.xml'] = '<w:'+root+' xmlns:w="'+W+'">'+content+'</w:'+root+'>'
            relations += relation(kind, kind+'.xml')
        result = extract(package(paragraph('Body'), extras, relations))
        self.assertEqual([p['kind'] for p in result['parts']], ['body','header','footer','footnote','endnote','comment'])
        self.assertEqual(len(result['blocks']), 6)
        self.assertNotIn('Unverified author', json.dumps(result))
        self.assertNotIn('Separator', json.dumps(result))
        self.assertIn({'reason':'unreferenced_notes','count':3}, result['warnings'])

    def test_deleted_table_rows_and_cells_are_not_current_text(self):
        deleted_row='<w:tr><w:trPr><w:del/></w:trPr><w:tc>'+paragraph('Removed row fact')+'</w:tc></w:tr>'
        row='<w:tr><w:tc><w:tcPr><w:cellDel/></w:tcPr>'+paragraph('Removed cell fact')+'</w:tc><w:tc>'+paragraph('Kept cell')+'</w:tc></w:tr>'
        result=extract(package('<w:tbl>'+deleted_row+row+'</w:tbl>'))
        self.assertEqual([b['text'] for b in result['blocks']],['Kept cell'])
        self.assertEqual(result['blocks'][0]['row'],2)
        self.assertEqual(result['blocks'][0]['cell'],2)
        self.assertIn({'reason':'revisions','count':2},result['warnings'])

    def test_relationship_based_main_part_and_strict_namespace(self):
        strict = 'http://purl.oclc.org/ooxml/wordprocessingml/main'
        note = '<w:footnotes xmlns:w="'+strict+'"><w:footnote w:id="1">'+paragraph('A note')+'</w:footnote></w:footnotes>'
        result = extract(package(paragraph('Main text'), {'notes/foot.xml':note}, relation('footnotes','../notes/foot.xml'), main='content/main.xml', namespace=strict))
        self.assertEqual([b['text'] for b in result['blocks']], ['Main text','A note'])

    def test_note_references_use_the_same_extracted_note_number(self):
        body='<w:p><w:r><w:t>Statement</w:t><w:footnoteReference w:id="7"/><w:commentReference w:id="3"/></w:r></w:p>'
        extras={'word/foot.xml':'<w:footnotes xmlns:w="'+W+'"><w:footnote w:id="7">'+paragraph('Supporting note')+'</w:footnote></w:footnotes>',
                'word/comments.xml':'<w:comments xmlns:w="'+W+'"><w:comment w:id="3">'+paragraph('Another speaker comment')+'</w:comment></w:comments>'}
        value=extract(package(body,extras,relation('footnotes','foot.xml')+relation('comments','comments.xml')))
        self.assertEqual(value['blocks'][0]['text'],'Statement〔脚注 1〕〔批注 1〕')
        self.assertEqual([(p['kind'],p['number']) for p in value['parts']], [('body',1),('footnote',1),('comment',1)])
        self.assertNotIn('unreferenced_notes', {w['reason'] for w in value['warnings']})

    def test_revision_hidden_field_and_object_coverage_is_explicit(self):
        body = '<w:p><w:del><w:r><w:delText>Old fact</w:delText></w:r></w:del><w:ins><w:r><w:t>New text</w:t></w:r></w:ins><w:r><w:rPr><w:vanish/></w:rPr><w:t>Hidden text</w:t></w:r><w:r><w:instrText>DO NOT EXECUTE</w:instrText><w:t> saved result</w:t><w:drawing><w:t>image text</w:t></w:drawing></w:r></w:p>'
        value = extract(package(body))
        self.assertEqual(value['blocks'][0]['text'], 'New text saved result')
        self.assertEqual({w['reason'] for w in value['warnings']}, {'revisions','hidden_text','fields','images_objects'})

    def test_alternate_representation_does_not_duplicate_text(self):
        body = '<mc:AlternateContent><mc:Choice Requires="x">'+paragraph('Unsupported choice')+'</mc:Choice><mc:Fallback>'+paragraph('Fallback')+'</mc:Fallback></mc:AlternateContent>'
        result = extract(package(body))
        self.assertEqual([b['text'] for b in result['blocks']], ['Fallback'])
        self.assertEqual(result['warnings'], [{'reason':'alternate_content','count':1}])

    def test_external_hyperlink_target_is_not_fetched_or_archived(self):
        body = '<w:p><w:hyperlink r:id="link"><w:r><w:t>Visible link</w:t></w:r></w:hyperlink></w:p>'
        raw = package(body, relations=relation('hyperlink','https://example.invalid/private-target', 'TargetMode="External"'))
        with patch('socket.create_connection', side_effect=AssertionError('No network')):
            result = extract(raw)
        self.assertEqual(result['blocks'][0]['text'], 'Visible link')
        self.assertNotIn('private-target', json.dumps(result))
        self.assertEqual(result['warnings'], [{'reason':'external_links','count':1}])

    def test_invalid_encrypted_macro_and_empty_packages_are_reported(self):
        files = [self.item(b'not a zip','broken.docx'),self.item(bytes.fromhex('D0CF11E0A1B11AE1')+b'fixture','encrypted.docx'),
                 self.item(package(paragraph('Macro'),content_type='application/vnd.ms-word.document.macroEnabled.main+xml'),'macro.docx'),self.item(package('<w:p/>'),'empty.docx')]
        report = self.store.documents.preview({'files':files})
        self.assertEqual([w['reason'] for w in report['warnings']], ['invalid_docx','encrypted_docx','unsupported_docx','no_text'])
        self.assertEqual(report['accepted'],0)

    def test_zip_traversal_duplicate_symlink_and_expansion_limits(self):
        for kind in ['traversal','duplicate','symlink','expanded']:
            with self.subTest(kind=kind):
                raw = io.BytesIO(package(paragraph('Safe')))
                with warnings.catch_warnings(), zipfile.ZipFile(raw,'a',zipfile.ZIP_DEFLATED) as z:
                    warnings.simplefilter('ignore',UserWarning)
                    if kind=='traversal':z.writestr('../outside','x')
                    elif kind=='duplicate':z.writestr('word/document.xml','x')
                    elif kind=='symlink':
                        info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=0o120777 << 16;z.writestr(info,'outside')
                    else:z.writestr('word/media/large.bin',b'0'*(33*1024*1024))
                with self.assertRaises(InvalidDocument):extract(raw.getvalue())
        with self.assertRaises(InvalidDocument):extract(package(paragraph('Safe'),relations=relation('footnotes','../../outside.xml')))

    def test_utf16_doctype_is_rejected(self):
        raw = io.BytesIO(package(paragraph('Safe')))
        with warnings.catch_warnings(), zipfile.ZipFile(raw,'a') as z:
            warnings.simplefilter('ignore',UserWarning)
            z.writestr('word/notes.xml', ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE root [<!ENTITY x "expand">]><root>&x;</root>').encode('utf-16'))
        # This part is referenced, so it must be parsed and rejected before entity expansion.
        value = package(paragraph('Safe'),{'word/notes.xml':zipfile.ZipFile(raw).read('word/notes.xml')},relation('footnotes','notes.xml'))
        with self.assertRaisesRegex(InvalidDocument,'invalid_docx'):extract(value)

    def test_preview_commit_source_ranges_and_manual_edit_protection(self):
        item = self.item(package(paragraph('Orchard café 👩🏽‍💻')+paragraph('Second paragraph')))
        preview = self.store.documents.preview({'files':[item,self.item(base64.b64decode(item['data']),'copy.docx')]})
        self.assertEqual(preview['accepted'],1);self.assertEqual(preview['duplicates'],1)
        self.assertEqual(preview['docxDocuments'][0]['blocks'],2)
        self.assertEqual(self.store.documents.listing({})['count'],0)
        report = self.store.documents.commit(preview['id']);self.assertEqual(report,{'added':1,'skipped':1,'versions':0})
        source = self.store.documents.listing({})['items'][0]
        current = self.store.knowledge.read_source('document',source['id'],['documents'],{})
        info = current['documentInfo'];self.assertEqual(info['format'],'docx');self.assertTrue(info['rangesValid'])
        text = current['text'];block = info['blocks'][0]
        self.assertEqual(text[block['start']:block['end']], 'Orchard café 👩🏽‍💻')
        path = self.store.output/'documents'/(source['id']+'.md');path.write_text('Human text must remain.')
        repeated = self.store.documents.preview({'files':[item]});self.assertEqual(self.store.documents.commit(repeated['id'])['skipped'],1)
        self.assertEqual(path.read_text(),'Human text must remain.')
        reread = self.store.documents.listing({})['items'][0]['documentInfo']
        self.assertFalse(reread['rangesValid']);self.assertNotIn('start',reread['blocks'][0])
        revised = self.store.documents.preview({'files':[self.item(package(paragraph('Updated orchard')))]})
        self.assertEqual(self.store.documents.commit(revised['id'])['versions'],1)
        self.assertEqual(path.read_text(),'Human text must remain.')
        versions = self.store.documents.listing({})['items']
        self.assertFalse(next(x for x in versions if x['id']==source['id'])['documentRevision']['isLatestImport'])
        newest = next(x for x in versions if x['id']!=source['id'])['documentRevision']
        self.assertTrue(newest['isLatestImport']);self.assertEqual(newest['previousID'],source['id'])

    def test_known_secret_redaction_and_text_export_exclude_binary(self):
        secret = 'sk-'+'proj-'+'abcdefghijklmnopqrstuv'
        preview = self.store.documents.preview({'files':[self.item(package(paragraph('Secret '+secret)))]})
        self.assertEqual(preview['redacted'],1);self.assertNotIn(secret,json.dumps(preview))
        self.store.documents.commit(preview['id'])
        with zipfile.ZipFile(self.store.exchange.export()) as archive:
            self.assertFalse(any(name.endswith('.docx') for name in archive.namelist()))
            for name in archive.namelist():self.assertNotIn(secret.encode(),archive.read(name))
        self.assertEqual(list((self.store.data/'document-previews').glob('.docx-*')),[])
        self.assertEqual(self.store.candidates(),[])

    def test_cancel_and_restart_preserve_explicit_preview(self):
        event = threading.Event();event.set()
        with self.assertRaisesRegex(DOCXError,'cancelled'):
            extract_docx(package(paragraph('Cancel')),self.root/'scratch',event)
        self.assertEqual(list((self.root/'scratch').iterdir()),[])
        preview = self.store.documents.preview({'files':[self.item()]})
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(self.store.documents.commit(preview['id'])['added'],1)
        cancelled = self.store.documents.preview({'files':[self.item(package(paragraph('Cancel preview')))]})
        self.store.documents.cancel(cancelled['id'])
        with self.assertRaises(StoreError):self.store.documents.commit(cancelled['id'])

    def test_malformed_worker_result_is_rejected(self):
        value={'format':'docx','parts':[{'id':1,'kind':'body','number':1,'blocks':1}],
               'blocks':[{'number':1,'part':1,'kind':'table_cell','text':'test'}], 'warnings':[], 'paragraphCount':1,'tableCount':1}
        with patch('docx_documents.supervise',return_value=value):
            with self.assertRaisesRegex(DOCXError,'invalid_docx'):extract_docx(b'fixture',self.root/'scratch')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'Requires a local pipe')
    def test_worker_stops_when_supervisor_disappears(self):
        source = self.root/'blocked-input.docx';os.mkfifo(source)
        worker = Path(__file__).resolve().parents[1]/'docx_worker.py'
        process = subprocess.Popen([sys.executable, '-E', '-s', '-B', str(worker), str(source), str(self.root/'result.json')],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            process.stdin.close()
            self.assertEqual(process.wait(timeout=3), 3)
            self.assertFalse((self.root/'result.json').exists())
        finally:
            if process.poll() is None:process.kill()
            process.wait(timeout=3)

    def test_interrupted_cache_cleanup_does_not_follow_links_or_delete_unknown_files(self):
        scratch=self.store.data/'document-previews';folder=scratch/'.docx-interrupted';folder.mkdir(parents=True)
        (folder/'input.docx').write_bytes(b'fixture');(folder/'result.json').write_text('{}')
        unknown=scratch/'.docx-unknown';unknown.mkdir();(unknown/'keep.txt').write_text('Keep')
        outside=self.root/'outside';outside.mkdir();(outside/'input.docx').write_text('Outside')
        (scratch/'.docx-link').symlink_to(outside,target_is_directory=True)
        self.store.close();self.store=Store(self.root/'node')
        self.assertFalse(folder.exists());self.assertEqual((unknown/'keep.txt').read_text(),'Keep')
        self.assertEqual((outside/'input.docx').read_text(),'Outside')

    @unittest.skipUnless(sys.platform=='darwin' and shutil.which('textutil'), 'Requires the native document converter')
    def test_native_generated_office_file(self):
        source=self.root/'source.txt';source.write_text('Native Orchard\n系统生成的合成办公文档。\n')
        destination=self.root/'source.docx'
        subprocess.run(['textutil','-convert','docx',str(source),'-output',str(destination)],check=True,capture_output=True)
        result=extract_docx(destination.read_bytes(),self.root/'scratch')
        self.assertIn('系统生成的合成办公文档。','\n'.join(b['text'] for b in result['blocks']))


if __name__ == '__main__':unittest.main()
