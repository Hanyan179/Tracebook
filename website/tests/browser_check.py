"""Optional real Chromium QA; requires Playwright, no website runtime dependencies."""
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
from threading import Thread
import json
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'qa-output'; OUTPUT.mkdir(exist_ok=True)
server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(ROOT/'dist')))
Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
checks=[]
try:
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,chromium_sandbox=True)
  for width,height,label in [(1180,757,'reference-desktop'),(390,844,'mobile')]:
   context=browser.new_context(viewport={'width':width,'height':height},device_scale_factor=1,reduced_motion='reduce')
   context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base) else route.abort())
   page=context.new_page()
   for language,prefix in [('zh',''),('en','/en')]:
    for name,path in [('home','/index.html'),('start','/start.html'),('privacy','/privacy.html')]:
     page.goto(base+prefix+path)
     assert page.locator('h1').count()==1
     assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),f'Overflow {language} {name} {width}'
     page.screenshot(path=str(OUTPUT/f'{language}-{name}-{label}.png'),full_page=True)
     assert page.evaluate('matchMedia("(prefers-reduced-motion: reduce)").matches')
     page.locator('.language').click(); assert ('/en/' in page.url)==(language=='zh')
     page.go_back(); assert page.url==base+prefix+path
     page.keyboard.press('Tab')
     checks.append({'page':language+'/'+name,'viewport':label,'horizontal_overflow':False,'language_roundtrip':True,'reduced_motion':True})
   page.goto(base+'/index.html'); page.keyboard.press('Tab')
   assert page.evaluate('document.activeElement.classList.contains("skip-link")')
   page.keyboard.press('Enter'); assert page.evaluate('document.activeElement.id')=='main'
   capture=page.locator('[data-capture-demo]')
   for state in ['recording','saved','idle']:
    capture.click(); assert page.locator('[data-capture]').get_attribute('data-capture')==state
    if state=='recording':
     page.wait_for_timeout(1200); assert page.locator('[data-counter]').inner_text()!='00:00'
    if state=='saved':
     stopped=page.locator('[data-counter]').inner_text();page.wait_for_timeout(250);assert page.locator('[data-counter]').inner_text()==stopped
   memory=page.locator('[data-confirm-memory]')
   for state in ['confirmed','inactive','confirmed']:
    memory.click(); assert memory.get_attribute('data-state')==state
   page.locator('[data-memory-reset]').click(); assert memory.get_attribute('data-state')=='candidate'
   for index in range(3):
    step=page.locator('[data-cli-step]').nth(index); step.click()
    assert page.locator('[data-cli-command]').inner_text()==step.get_attribute('data-command')
    assert page.locator(f'[data-cli-result="{index}"]').is_visible()
   page.locator('[data-citation]').click(); assert page.evaluate('document.activeElement.id')=='original-example'
   assert page.locator('[data-scene-pause]').is_hidden(), 'Reduced motion must suppress autoplay control'
   assert page.locator('.is-pinned').count()==0
   checks.append({'viewport':label,'capture_cycle':True,'memory_cycle':True,'cli_steps':True,'source_focus':True})
   context.close()
  context=browser.new_context(viewport={'width':390,'height':844},java_script_enabled=False)
  page=context.new_page();page.goto(base+'/');assert page.locator('[data-capture-demo]').is_hidden();assert page.locator('[data-story-step]').count()==3;page.locator('.language').click();assert '/en/' in page.url
  page.locator('nav').first.get_by_text('Get started').click();assert page.url.endswith('/en/start.html')
  context.close();browser.close()
 (OUTPUT/'browser-results.json').write_text(json.dumps(checks,indent=2))
 print('PASS: real Chromium checks recorded in browser-results.json. Includes capture and memory cycles, CLI, reduced motion and no-JS navigation.')
finally:server.shutdown()
