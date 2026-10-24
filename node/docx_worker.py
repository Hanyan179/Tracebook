"""Read bounded WordprocessingML packages without rendering or following links."""
import collections
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import sys
import threading
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET

MAX_FILE = 2 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
MAX_BLOCKS = 20000
WORD = {'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
        'http://purl.oclc.org/ooxml/wordprocessingml/main'}
REL = ('http://schemas.openxmlformats.org/officeDocument/2006/relationships/',
       'http://purl.oclc.org/ooxml/officeDocument/relationships/')
MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
MAIN_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'


class InvalidDocument(Exception):
    pass


def split_tag(tag):
    if not isinstance(tag, str):return '', ''
    if tag.startswith('{'):return tuple(tag[1:].split('}', 1))
    return '', tag


def word_tag(element):
    namespace, tag = split_tag(element.tag)
    return tag if namespace in WORD else ''


def attribute(element, key, default=''):
    return next((element.get('{'+ns+'}'+key) for ns in WORD if element.get('{'+ns+'}'+key) is not None), default)


def safe_name(name):
    return (bool(name) and not name.startswith('/') and '\\' not in name and ':' not in name
            and '\0' not in name and '..' not in PurePosixPath(name).parts)


class Reader:
    def __init__(self, raw):
        if not raw or len(raw) > MAX_FILE:raise InvalidDocument('too_large')
        if raw.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):raise InvalidDocument('encrypted_docx')
        self.zip = zipfile.ZipFile(io.BytesIO(raw))
        infos = self.zip.infolist()
        if not 1 <= len(infos) <= 4096:raise InvalidDocument('parser_limit')
        self.entries = {}
        expanded = 0
        for info in infos:
            if not safe_name(info.filename) or info.filename in self.entries:raise InvalidDocument('invalid_docx')
            if info.flag_bits & 1:raise InvalidDocument('encrypted_docx')
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):raise InvalidDocument('invalid_docx')
            if (info.external_attr >> 16) & 0o170000 == 0o120000:raise InvalidDocument('invalid_docx')
            expanded += info.file_size
            if expanded > MAX_EXPANDED:raise InvalidDocument('parser_limit')
            self.entries[info.filename] = info
        self.warnings = collections.Counter()
        self.blocks = []
        self.parts = []
        self.paragraphs = 0
        self.tables = 0
        self.characters = 0
        self.references = {}
        self.used_references = set()

    def xml(self, name):
        info = self.entries.get(name)
        if not info or info.is_dir():raise InvalidDocument('invalid_docx')
        if info.file_size > MAX_XML:raise InvalidDocument('parser_limit')
        with self.zip.open(info) as source:
            raw = source.read(MAX_XML + 1)
        if len(raw) > MAX_XML:raise InvalidDocument('parser_limit')
        if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            text = raw.decode('utf-16')
        elif raw.startswith(b'\x00<'):
            text = raw.decode('utf-16-be')
        elif raw.startswith(b'<\x00'):
            text = raw.decode('utf-16-le')
        else:text = raw.decode('utf-8-sig')
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise InvalidDocument('invalid_docx')
        root = ET.fromstring(text)
        count = 0
        stack = [(root, 0)]
        while stack:
            node, depth = stack.pop();count += 1
            if count > 200000 or depth > 64:raise InvalidDocument('parser_limit')
            stack.extend((child, depth + 1) for child in node)
        return root

    def relationships(self, part=''):
        name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part)+'.rels') if part else '_rels/.rels'
        if name not in self.entries:
            if not part:raise InvalidDocument('invalid_docx')
            return []
        root = self.xml(name)
        if root.tag != '{http://schemas.openxmlformats.org/package/2006/relationships}Relationships':raise InvalidDocument('invalid_docx')
        result = []
        for item in root:
            kind = item.get('Type', '')
            kind = next((kind[len(prefix):] for prefix in REL if kind.startswith(prefix)), '')
            if not kind:continue
            if item.get('TargetMode', '').lower() == 'external':
                self.warnings['external_links'] += 1
                if not part and kind == 'officeDocument':raise InvalidDocument('invalid_docx')
                continue
            target = urllib.parse.unquote(item.get('Target', ''))
            if not target or any(x in target for x in ('\\', '\0', ':', '?', '#')):raise InvalidDocument('invalid_docx')
            target = target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(part), target)
            target = posixpath.normpath(target)
            if not safe_name(target):raise InvalidDocument('invalid_docx')
            result.append((kind, target))
        return result

    def alternate(self, node):
        self.warnings['alternate_content'] += 1
        fallback = next((x for x in node if split_tag(x.tag) == (MC, 'Fallback')), None)
        if fallback is not None:return list(fallback)
        # A choice can require unsupported rendering; do not double-count or guess.
        return []

    def inline(self, node):
        tag = word_tag(node)
        namespace, local = split_tag(node.tag)
        if namespace == MC and local == 'AlternateContent':
            return ''.join(self.inline(x) for x in self.alternate(node))
        if tag in ('del', 'moveFrom'):
            self.warnings['revisions'] += 1
            return ''
        if tag in ('ins', 'moveTo'):self.warnings['revisions'] += 1
        if tag == 'r':
            props = next((x for x in node if word_tag(x) == 'rPr'), None)
            if props is not None and any(word_tag(x) in ('vanish', 'webHidden') and attribute(x, 'val', '1') not in ('0', 'false', 'off') for x in props):
                self.warnings['hidden_text'] += 1
                return ''
        if tag == 't':return node.text or ''
        if tag == 'tab':return '\t'
        if tag in ('br', 'cr'):return '\n'
        if tag in ('footnoteReference', 'endnoteReference', 'commentReference'):
            kind = tag[:-len('Reference')]
            number = self.references.get((kind, attribute(node, 'id')))
            if number is None:
                self.warnings['unsupported_content'] += 1
                return '〔未解析附注〕'
            self.used_references.add((kind, attribute(node, 'id')))
            return '〔'+{'footnote':'脚注', 'endnote':'尾注', 'comment':'批注'}[kind]+' '+str(number)+'〕'
        if tag == 'noBreakHyphen':return '\u2011'
        if tag == 'softHyphen':return '\u00ad'
        if tag in ('instrText', 'fldChar'):
            self.warnings['fields'] += 1
            return ''
        if tag in ('drawing', 'pict', 'object'):
            self.warnings['images_objects'] += 1
            return ''
        if namespace in ('http://schemas.openxmlformats.org/officeDocument/2006/math', 'http://purl.oclc.org/ooxml/officeDocument/math'):
            self.warnings['equations'] += 1
            return ''
        if tag in ('sym', 'altChunk'):
            self.warnings['unsupported_content'] += 1
            return ''
        if tag == 'sdt':
            content = next((x for x in node if word_tag(x) == 'sdtContent'), None)
            return self.inline(content) if content is not None else ''
        if tag.endswith('Pr') or tag in ('sectPr', 'delText'):return ''
        return ''.join(self.inline(x) for x in node)

    def add_paragraph(self, node, part, location):
        self.paragraphs += 1
        text = self.inline(node).replace('\r\n', '\n').replace('\r', '\n')
        if not text.strip():return
        if '\0' in text:raise InvalidDocument('invalid_docx')
        self.characters += len(text.encode())
        if self.characters > MAX_FILE or len(self.blocks) >= MAX_BLOCKS:raise InvalidDocument('text_limit')
        self.blocks.append({'number': len(self.blocks)+1, 'part': part,
                            'kind': 'table_cell' if location else 'paragraph', 'text': text, **location})

    def removed_table_item(self, node, property_name):
        props = next((x for x in node if word_tag(x) == property_name), None)
        if props is None:return False
        revisions = [word_tag(x) for x in props if word_tag(x) in ('del', 'ins', 'cellDel', 'cellIns', 'cellMerge')]
        self.warnings['revisions'] += len(revisions)
        return any(x in ('del', 'cellDel') for x in revisions)

    def walk(self, node, part, location=None):
        location = location or {}
        tag = word_tag(node)
        if tag == 'p':self.add_paragraph(node, part, location);return
        if tag in ('del', 'moveFrom'):
            self.warnings['revisions'] += 1
            return
        if tag in ('ins', 'moveTo'):self.warnings['revisions'] += 1
        if split_tag(node.tag) == (MC, 'AlternateContent'):
            for child in self.alternate(node):self.walk(child, part, location)
            return
        if tag == 'sdt':
            for child in node:
                if word_tag(child) == 'sdtContent':self.walk(child, part, location)
            return
        if tag == 'tbl':
            self.tables += 1;table = self.tables
            self.warnings['tables'] += 1
            rows = [x for x in node if word_tag(x) == 'tr']
            self.warnings['unsupported_content'] += sum(word_tag(x) not in ('tr', 'tblPr', 'tblGrid') for x in node)
            for row_number, row in enumerate(rows, 1):
                if self.removed_table_item(row, 'trPr'):continue
                self.warnings['unsupported_content'] += sum(word_tag(x) not in ('tc', 'trPr', 'tblPrEx') for x in row)
                for cell_number, cell in enumerate((x for x in row if word_tag(x) == 'tc'), 1):
                    if self.removed_table_item(cell, 'tcPr'):continue
                    for child in cell:
                        self.walk(child, part, {'table': table, 'row': row_number, 'cell': cell_number})
            return
        if tag.endswith('Pr'):return
        if tag == 'altChunk':self.warnings['unsupported_content'] += 1;return
        for child in node:self.walk(child, part, location)

    def part(self, node, kind, number):
        identifier = len(self.parts)+1
        before = len(self.blocks)
        self.walk(node, identifier)
        self.parts.append({'id': identifier, 'kind': kind, 'number': number, 'blocks': len(self.blocks)-before})

    def read(self):
        mains = [target for kind, target in self.relationships() if kind == 'officeDocument']
        if len(mains) != 1:raise InvalidDocument('invalid_docx')
        main = mains[0]
        types = self.xml('[Content_Types].xml')
        if types.tag != '{http://schemas.openxmlformats.org/package/2006/content-types}Types':raise InvalidDocument('invalid_docx')
        main_types = [x.get('ContentType') for x in types if x.get('PartName', '').lstrip('/') == main]
        if main_types != [MAIN_TYPE]:raise InvalidDocument('unsupported_docx')
        document = self.xml(main)
        if word_tag(document) != 'document':raise InvalidDocument('invalid_docx')
        body = [x for x in document if word_tag(x) == 'body']
        if len(body) != 1:raise InvalidDocument('invalid_docx')
        seen = {main};counts = collections.Counter();related = []
        for kind, target in self.relationships(main):
            if kind not in ('header', 'footer', 'footnotes', 'endnotes', 'comments'):continue
            if target in seen:continue
            seen.add(target)
            node = self.xml(target)
            expected = {'header': 'hdr', 'footer': 'ftr', 'footnotes': 'footnotes', 'endnotes': 'endnotes', 'comments': 'comments'}[kind]
            if word_tag(node) != expected:raise InvalidDocument('invalid_docx')
            if kind in ('header', 'footer'):
                counts[kind] += 1;related.append((node, kind, counts[kind]))
            else:
                leaf = {'footnotes': 'footnote', 'endnotes': 'endnote', 'comments': 'comment'}[kind]
                for child in node:
                    if word_tag(child) != leaf:continue
                    if attribute(child, 'type') in ('separator', 'continuationSeparator', 'continuationNotice'):continue
                    key = (leaf, attribute(child, 'id'))
                    if not key[1] or key in self.references:raise InvalidDocument('invalid_docx')
                    counts[leaf] += 1
                    self.references[key] = counts[leaf]
                    related.append((child, leaf, counts[leaf]))
        self.part(body[0], 'body', 1)
        for node, kind, number in related:self.part(node, kind, number)
        self.warnings['unreferenced_notes'] = len(set(self.references)-self.used_references)
        if not self.blocks:raise InvalidDocument('no_text')
        return {'format': 'docx', 'parser': 'wordprocessingml-v1', 'parts': self.parts,
                'blocks': self.blocks, 'paragraphCount': self.paragraphs, 'tableCount': self.tables,
                'warnings': [{'reason': key, 'count': value} for key, value in sorted(self.warnings.items()) if value]}


def extract(raw):
    try:
        reader = Reader(raw)
        try:return reader.read()
        finally:reader.zip.close()
    except InvalidDocument:raise
    except (ValueError, UnicodeError, KeyError, OSError, RuntimeError, zipfile.BadZipFile, ET.ParseError):
        raise InvalidDocument('invalid_docx')


def main():
    # The parent owns stdin. Exit even if the node disappears while XML or ZIP
    # parsing is still running; the parent's sampled monitor alone cannot do this.
    def parent_closed():
        try:os.read(0, 1)
        finally:os._exit(3)
    threading.Thread(target=parent_closed, daemon=True).start()
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        resource.setrlimit(resource.RLIMIT_FSIZE, (4 * MAX_FILE, 4 * MAX_FILE))
        try:resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        except (ValueError, OSError):pass
    except (ImportError, ValueError, OSError):pass
    try:value = extract(Path(sys.argv[1]).read_bytes())
    except InvalidDocument as error:value = {'error': str(error)}
    Path(sys.argv[2]).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':main()
