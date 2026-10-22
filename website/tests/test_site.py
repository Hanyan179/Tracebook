"""Dependency-free structural checks. These do not replace browser QA."""
import importlib.util
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, unquote
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('site_build',ROOT/'build.py')
site=importlib.util.module_from_spec(spec); spec.loader.exec_module(site)
class Page(HTMLParser):
    def __init__(self,text):
        super().__init__(); self.tags=[]; self.ids=[]; self.refs=[]; self.feed(text)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs); self.tags.append((tag,a))
        if 'id' in a:self.ids.append(a['id'])
        for key in ('href','src','srcset'):
            if key in a:self.refs.append(a[key])
class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        site.build(); cls.paths=sorted((ROOT/'dist').rglob('*.html'))
    def test_exact_six_pages(self):self.assertEqual(len(self.paths),6)
    def test_links_landmarks_and_privacy(self):
        for path in self.paths:
            with self.subTest(page=path.relative_to(ROOT)):
                text=path.read_text(); p=Page(text)
                self.assertEqual(len(p.ids),len(set(p.ids)))
                for tag in ('h1','main','header','footer'):
                    self.assertEqual(sum(t==tag for t,a in p.tags),1)
                self.assertFalse(any(t in ('script','iframe','form','input','video','audio') for t,a in p.tags))
                self.assertFalse(any(k.startswith('on') for t,a in p.tags for k in a))
                self.assertEqual(next(a['lang'] for t,a in p.tags if t=='html'),'en' if path.parent.name=='en' else 'zh-CN')
                self.assertEqual(sum(a.get('aria-current')=='page' for t,a in p.tags),1)
                self.assertTrue(any(t=='a' and a.get('href')=='#main' for t,a in p.tags))
                for t,a in p.tags:
                    if t=='img':self.assertIn('alt',a)
                for ref in p.refs:
                    u=urlsplit(ref)
                    if u.scheme:
                        self.assertEqual(u.scheme,'https'); self.assertEqual(u.hostname,'github.com')
                        self.assertTrue(u.path.startswith('/Hanyan179/Tracebook'))
                        if '/blob/main/' in u.path:self.assertTrue((ROOT.parent/u.path.split('/blob/main/')[1]).is_file(),ref)
                    else:
                        target=(path.parent/unquote(u.path)).resolve() if u.path else path
                        self.assertTrue(target.is_file(),ref)
                        if u.fragment:self.assertIn(u.fragment,Page(target.read_text()).ids)
    def test_content_boundaries(self):
        zh=(ROOT/'dist/index.html').read_text(); en=(ROOT/'dist/en/index.html').read_text()
        for word in ['不会生成模型回答','真实自动转写仍在开发','尚无已签名','不是完整备份','真实模型质量尚未验收']:self.assertIn(word,zh)
        for word in ['does not generate model answers','transcription is still in development','No signed','not a full backup','Real-model quality is not yet validated']:self.assertIn(word,en)
        for path in self.paths:self.assertNotIn('声记',path.read_text())
    def test_illustration_labels_and_source_return(self):
        for relative,label in [("index.html","非运行截图"),("en/index.html","not a running screenshot")]:
            text=(ROOT/"dist"/relative).read_text()
            self.assertIn(label,text)
            for cls in ("story-hero","tap-scene","calendar-spread","source-workbench","terminal-spread","memory-dossier","ownership-spread"):
                self.assertIn(cls,text)
            self.assertIn('href="#original-example"',text)
            self.assertIn('id="original-example"',text)
        for relative in ("privacy.html","en/privacy.html"):
            self.assertIn("ChatGPT Sites",(ROOT/"dist"/relative).read_text())
    def test_scenario_limits_and_photography(self):
        for relative,terms in [("index.html",["次日","轻点背面","原音","不能录音、改文件、确认记忆","手动提出的候选"]),("en/index.html",["THE NEXT DAY","Back Tap","Not scheduled recording","cannot record, edit files, confirm memory","MANUALLY"])]:
            text=(ROOT/"dist"/relative).read_text()
            for term in terms:self.assertIn(term,text)
            self.assertIn("AI",text)
            self.assertIn("integrations/lifto-library/scripts/lifto.py",text)
            self.assertEqual(sum(t=="picture" for t,a in Page(text).tags),3)
    def test_language_switch_roundtrip(self):
        for path in self.paths:
            p=Page(path.read_text()); href=next(a['href'] for t,a in p.tags if a.get('class')=='language')
            target=(path.parent/href).resolve(); q=Page(target.read_text())
            back=next(a['href'] for t,a in q.tags if a.get('class')=='language')
            self.assertEqual((target.parent/back).resolve(),path.resolve())
    def test_assets_and_reduced_motion(self):
        css=(ROOT/'dist/assets/style.css').read_text()
        self.assertIn(':focus-visible',css); self.assertIn('prefers-reduced-motion:reduce',css)
        self.assertNotIn('@import',css); self.assertNotIn('url(',css)
        self.assertEqual((ROOT/'assets/brand-mark.png').read_bytes(),(ROOT.parent/'Apple/Resources/Brand.xcassets/BrandMark.imageset/brand-mark.png').read_bytes())
    def test_repeat_build_is_deterministic(self):
        first={str(p.relative_to(ROOT/'dist')):p.read_bytes() for p in (ROOT/'dist').rglob('*') if p.is_file()}
        site.build()
        second={str(p.relative_to(ROOT/'dist')):p.read_bytes() for p in (ROOT/'dist').rglob('*') if p.is_file()}
        self.assertEqual(first,second)
if __name__=='__main__':unittest.main()
