"""Structural and privacy checks; not a substitute for real browser inspection."""
import importlib.util
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, unquote
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('site_build',ROOT/'build.py')
site=importlib.util.module_from_spec(spec);spec.loader.exec_module(site)
class Page(HTMLParser):
    def __init__(self,text):
        super().__init__();self.tags=[];self.ids=[];self.refs=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);self.tags.append((tag,a))
        if 'id' in a:self.ids.append(a['id'])
        for key in ('href','src'):
            if key in a:self.refs.append(a[key])
class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):site.build();cls.paths=sorted((ROOT/'dist').rglob('*.html'))
    def test_six_pages_and_explicit_asset_whitelist(self):
        self.assertEqual(len(self.paths),6)
        self.assertEqual({p.name for p in (ROOT/'dist/assets').iterdir()},{'tokens.css','slice.css','whole.css','motion.js','whole.js','brand-mark.png','favicon.png','precision-tap-v1.png'})
    def test_links_landmarks_and_resources(self):
        for path in self.paths:
            p=Page(path.read_text());self.assertEqual(len(p.ids),len(set(p.ids)))
            for tag in ('h1','main','header','footer'):self.assertEqual(sum(t==tag for t,a in p.tags),1)
            self.assertFalse(any(t in ('iframe','form','input','video','audio') for t,a in p.tags))
            self.assertFalse(any(k.startswith('on') for t,a in p.tags for k in a))
            scripts=[a for t,a in p.tags if t=='script'];self.assertEqual(len(scripts),2 if path.name=='index.html' else 0)
            for script in scripts:self.assertIn('defer',script)
            for t,a in p.tags:
                if t=='img':self.assertIn('alt',a)
                if t=='button':self.assertEqual(a.get('type'),'button')
            for ref in p.refs:
                u=urlsplit(ref)
                if u.scheme:
                    self.assertEqual(u.scheme,'https');self.assertEqual(u.hostname,'github.com');self.assertTrue(u.path.startswith('/Hanyan179/Tracebook'))
                else:
                    self.assertFalse(u.path.startswith('/'));target=(path.parent/unquote(u.path)).resolve() if u.path else path
                    self.assertTrue(target.is_file(),ref)
                    if u.fragment:self.assertIn(u.fragment,Page(target.read_text()).ids)
    def test_current_product_boundaries(self):
        zh=(ROOT/'dist/index.html').read_text();en=(ROOT/'dist/en/index.html').read_text()
        for word in ['PDF 已有文字','仍需本人确认','备份未加密','跨机器','不使用麦克风','非产品运行截图','暂无已签名安装包']:self.assertIn(word,zh)
        for word in ['existing PDF text','human confirmation','unencrypted','No microphone','Not an app screenshot','No signed installer']:self.assertIn(word,en)
        for path in self.paths:
            for stale in ['声记','资料对话是检索，不生成回答','automated model organization remains in development']:self.assertNotIn(stale,path.read_text())
    def test_single_continuous_audio_and_independent_note(self):
        for relative in ['index.html','en/index.html']:
            text=(ROOT/'dist'/relative).read_text();page=Page(text)
            self.assertEqual(sum(a.get('id')=='audio-source' for t,a in page.tags),1)
            self.assertIn('recording / DEMO_AUDIO / v1',text);self.assertIn('note / NOTE_ID / v1',text)
            for old in ['day-carousel','capture-theatre','day-product','source-reflections','opening-sequence']:self.assertNotIn(old,text)
            self.assertEqual(sum(t=='img' for t,a in page.tags),2)
    def test_languages_and_navigation(self):
        for path in self.paths:
            p=Page(path.read_text());self.assertEqual(next(a['lang'] for t,a in p.tags if t=='html'),'en' if path.parent.name=='en' else 'zh-CN')
            target=('../' if path.parent.name=='en' else 'en/')+path.name
            self.assertTrue(any(a.get('href')==target for t,a in p.tags))
    def test_privacy_and_no_external_runtime(self):
        for name in ['motion.js','whole.js']:
            js=(ROOT/'dist/assets'/name).read_text()
            for forbidden in ('fetch(', 'XMLHttpRequest','WebSocket','getUserMedia','localStorage','sessionStorage','sendBeacon'):self.assertNotIn(forbidden,js)
        for path in [ROOT/'dist/privacy.html',ROOT/'dist/en/privacy.html']:self.assertIn('ChatGPT Sites',path.read_text())
    def test_source_brand_and_motion_fallback(self):
        self.assertEqual((ROOT/'dist/assets/brand-mark.png').read_bytes(),(ROOT.parent/'Apple/Resources/Brand.xcassets/BrandMark.imageset/brand-mark.png').read_bytes())
        css=(ROOT/'precision/tokens.css').read_text();self.assertIn(':focus-visible',css);self.assertIn('prefers-reduced-motion:reduce',css)
        self.assertIn('[hidden]{display:none!important}',css)
    def test_no_qa_attributes_or_recording_claims_in_output(self):
        for path in self.paths:self.assertNotIn('data-preview-progress=',path.read_text());self.assertNotIn('data-preview-note',path.read_text())
        text=(ROOT/'dist/index.html').read_text();self.assertIn('原音 · 待添加文字',text);self.assertIn('另一条已有来源，不是原音转写',text)
    def test_repeat_build_is_deterministic(self):
        first={str(p.relative_to(ROOT/'dist')):p.read_bytes() for p in (ROOT/'dist').rglob('*') if p.is_file()};site.build()
        self.assertEqual(first,{str(p.relative_to(ROOT/'dist')):p.read_bytes() for p in (ROOT/'dist').rglob('*') if p.is_file()})
if __name__=='__main__':unittest.main()
