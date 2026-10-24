#!/usr/bin/env python3
"""Build an isolated local visual slice. Does not change the production dist."""
from pathlib import Path
from html import escape
import json, math, shutil
try:
    from .sections import continuation
except ImportError:
    from sections import continuation
ROOT=Path(__file__).resolve().parent
OUT=ROOT.parent/'qa-output'/'precision-slice'
DATA=json.loads((ROOT/'source.json').read_text())

def waveform():
    bars=[]
    for i in range(121):
        x=10+i*8
        envelope=.12+.70*math.exp(-((i-42)/15)**2)+.42*math.exp(-((i-88)/12)**2)
        amplitude=(.28+.72*abs(math.sin(i*1.73)*math.cos(i*.41)))*envelope
        h=10+amplitude*180
        bars.append(f'<path d="M{x:.0f} {120-h:.2f}V{120+h:.2f}"/>')
    return '<svg class="audio-wave" viewBox="0 0 980 240" role="img" aria-label="Synthetic audio waveform"><g>'+''.join(bars)+'</g></svg>'

def render(lang):
    def t(a,b):return a if lang=='zh' else b
    a,n=DATA['audio'],DATA['note'];p='' if lang=='zh' else '../';title=escape(n['title_'+lang]);quote=escape(n['quote_'+lang])
    return f'''<!doctype html><html lang="{'zh-CN' if lang=='zh' else 'en'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{t('迹库 · 个人资料与记忆库','Tracebook · Your personal library')}</title><link rel="stylesheet" href="{p}assets/tokens.css"><link rel="stylesheet" href="{p}assets/slice.css"><link rel="stylesheet" href="{p}assets/whole.css"><script src="{p}assets/motion.js" defer></script><script src="{p}assets/whole.js" defer></script></head><body>
<a class="skip" href="#main">{t('跳到内容','Skip to content')}</a><header class="site-nav"><a class="brand" href="#main"><img src="{p}assets/brand-mark.png" alt="" width="28" height="28"><span>{t('迹库','Tracebook')}</span></a><nav aria-label="{t('页面导航','Page navigation')}"><a href="#review" data-review-nav>{t('来源与回顾','Sources & review')}</a><a href="start.html">{t('开始使用','Get started')}</a><a href="{'en/index.html' if lang=='zh' else '../index.html'}" lang="{'en' if lang=='zh' else 'zh-CN'}">{t('English','中文')}</a></nav></header>
<main id="main"><section class="precision-story" data-story aria-label="{t('从片刻到回顾','From a moment to a review')}"><div class="story-viewport" tabindex="-1">
<div class="photo-field"><img src="{p}assets/precision-tap-v1.png" width="1672" height="941" alt="" fetchpriority="high"><div class="contact-rings" aria-hidden="true"><i></i><i></i></div></div><div class="silver-field" aria-hidden="true"></div>
<div class="intro-copy" data-panel="intro"><p class="overline">TRACEBOOK</p><h1>{t('片刻留下。<br>线索接上。','Keep the moment.<br>Continue the thread.')}</h1><p class="intro-definition">{t('你的个人资料与记忆库。','Your personal library, with memory.')}</p><button type="button" class="outline-button" data-action-demo hidden>{t('体验敲两下','Try two taps')}<span class="arrow" aria-hidden="true"></span></button><a class="no-js-action" href="#audio-source">{t('查看这条合成来源','Explore this synthetic source')}<span class="arrow" aria-hidden="true"></span></a><p class="demo-label">{t('网页动作演示，不使用麦克风','Web gesture demo. No microphone used.')}</p><p class="action-status" role="status" data-action-status data-idle="" data-running="{t('动作已触发：两下轻点。','Gesture triggered: two taps.')}" data-saved="{t('固定合成原音已展开。不是现场录音。','The fixed synthetic audio is expanded. This is not live recording.')}"></p></div>
<div class="audio-copy" data-panel="audio"><p class="overline">01 / {t('原音来源','AUDIO SOURCE')}</p><h2>{t('保留原音。<br>保留来处。','Keep the sound.<br>Keep its source.')}</h2><p>{t('不是一段凭空生成的总结。','Not a summary made from thin air.')}</p><button type="button" class="reset-gesture" data-reset-gesture hidden>{t("重看动作","Replay the gesture")}</button></div>
<article class="audio-object" id="audio-source" tabindex="-1" data-source-id="DEMO_AUDIO"><div class="audio-heading"><span>10/21 · 09:10</span><h3>{escape(a['title_'+lang])}</h3></div>{waveform()}<div class="audio-meta"><strong>00:42</strong><span class="mono">recording / DEMO_AUDIO / v1</span><span>{t('原音 · 待添加文字','Original audio · Text not added')}</span></div></article>
<div class="review-copy" data-panel="review"><p class="overline">02 / {t('次日回顾','THE NEXT DAY')}</p><h2>{t('明早再看。<br>昨天还在。','Come back tomorrow.<br>Yesterday is here.')}</h2><p>{t('原音与已有笔记，各自带着来处。','Audio and existing notes, each with their own source.')}</p><p class="condition">{t('节点运行时，按时间与时区回顾此前一天。','While the node runs, revisit the previous day on your schedule and timezone.')}</p></div>
<section class="review-surface" id="review" aria-label="{t('日回顾合成示例','Synthetic daily review')}"><div class="review-header"><span>07:30</span><p>10/22 <span aria-hidden="true">/</span> {t('回看 10/21','Review 10/21')}</p></div><div class="audio-dock" aria-hidden="true"></div><button class="note-choice" type="button" data-note-select aria-controls="note-original" aria-expanded="false" hidden><span class="note-meta"><span>{t('已有文字笔记','EXISTING TEXT NOTE')}</span><time>10:35</time></span><strong>{quote}</strong><span class="mono">note / NOTE_ID / v1</span><span class="memory-badge" data-memory-badge data-candidate="{t('关联记忆：待确认','Linked memory: pending')}" data-confirmed="{t('关联记忆：已确认','Linked memory: confirmed')}" data-inactive="{t('关联记忆：已停用','Linked memory: inactive')}">{t('关联记忆：待确认','Linked memory: pending')}</span><span class="note-link">{t('沿着这条笔记，看原文','Follow this note to its source')}<span class="arrow" aria-hidden="true"></span></span></button><a class="note-fallback" href="#note-original">{t('阅读已有文字笔记','Read the existing text note')}</a></section>
<section class="note-original" id="note-original" tabindex="-1"><div class="note-reading"><p class="overline">{t('另一条已有来源，不是原音转写','A DIFFERENT EXISTING SOURCE, NOT AN AUDIO TRANSCRIPT')}</p><div class="reading-header"><h2>{title}</h2><span class="mono">note / NOTE_ID / v1</span></div><p class="reading-date">10/21 · 10:35</p><blockquote>{quote}</blockquote><p>{t('让观众先看见依据，再解释整理过程。','Let the audience see the evidence before explaining the organization.')}</p><span class="memory-badge" data-memory-badge data-candidate="{t('关联记忆：待确认','Linked memory: pending')}" data-confirmed="{t('关联记忆：已确认','Linked memory: confirmed')}" data-inactive="{t('关联记忆：已停用','Linked memory: inactive')}">{t('关联记忆：待确认','Linked memory: pending')}</span><a class="note-onward" href="#work-source" data-source-route>{t("让工具读取这条原文","Let a tool read this source")}<span class="arrow" aria-hidden="true"></span></a><a href="#review" data-return-review>{t('返回日回顾','Back to daily review')}<span class="arrow" aria-hidden="true"></span></a></div></section>
<div class="story-footer"><a href="#audio-source" data-advance>{t('往下，接上这一天','Continue into your day')}<span class="down-arrow" aria-hidden="true"></span></a><p>{t('AI 摄影 · 固定合成资料 · 非产品运行截图','AI photography · Fixed synthetic material · Not an app screenshot')}</p></div><div class="story-progress" aria-hidden="true"><span>{t('留下','MOMENT')}</span><i><b></b></i><span>{t('回看','REVIEW')}</span></div>
</div></section>

{continuation(lang,DATA)}
<section class="closing-section section-pad"><p class="overline">TRACEBOOK / OPEN SOURCE</p><h2>{t('把资料留下。<br>把决定留给自己。','Keep your material.<br>Keep the choice yours.')}</h2><div class="closing-actions"><a href="https://github.com/Hanyan179/Tracebook">{t('查看源码','View source')}<span class="arrow" aria-hidden="true"></span></a><a href="start.html">{t('开始使用','Get started')}<span class="arrow" aria-hidden="true"></span></a></div><p class="release-note">{t('MIT 开源 · 源码开发版 · 暂无已签名安装包','MIT open source · Development source · No signed installer yet')}</p><details class="quiet-details"><summary>{t('记录、模型与开发边界','Capture, models and development boundaries')}</summary><p>{t('手机轻点背面需系统绑定快捷指令、解锁前台与麦克风授权；手表是独立录音入口。网页只展示固定合成资料，不录音、不上传、不调用模型。客户端可以明确选择片段，预览后发送模型回答，或发起记忆候选与批量整理，仍需本人确认。语音服务与文字模型需独立配置，可能收费；真实服务质量和跨设备后台链路仍待验收。','Phone Back Tap requires a bound system shortcut, an unlocked foreground app and microphone permission; the watch is a separate recording entry. This webpage uses fixed synthetic material: no recording, uploads or model calls. Clients can explicitly select passages, preview and send them for model answers, or request memory candidates and batch organization, still subject to human confirmation. Speech and text services require separate configuration and may charge fees; real-service quality and cross-device background workflows still need validation.')}</p></details><details class="quiet-details"><summary>{t('文件、备份与隐私','Files, backups and privacy')}</summary><p>{t('便携文字导出不含原音和凭证。另有独立备份与恢复，可包含已接收原音、归档文字、记忆、来源与历史；备份未加密，不含凭证、外部原件或未确认导入。恢复为独立副本并暂停自动任务，跨机器和干净系统恢复仍待验收。','Portable text export excludes original audio and credentials. Separate backup and restoration can include received audio, archived text, memories, sources and history. Backups are unencrypted and exclude credentials, external originals and unconfirmed imports. Restoration creates an independent copy with automatic tasks paused; cross-machine and clean-system restoration still need validation.')}</p><a href="privacy.html">{t('完整隐私说明','Read the privacy explanation')}</a></details></section>
</main>{footer(lang)}</body></html>'''

def footer(lang):
    zh=lang=='zh'
    return '<footer class="site-footer"><a href="index.html">'+('迹库 / Tracebook' if zh else 'Tracebook / 迹库')+'</a><nav><a href="start.html">'+('开始使用' if zh else 'Get started')+'</a><a href="privacy.html">'+('隐私说明' if zh else 'Privacy')+'</a><a href="https://github.com/Hanyan179/Tracebook/blob/main/ROADMAP.md">'+('路线图' if zh else 'Roadmap')+'</a><a href="'+('en/index.html' if zh else '../index.html')+'">'+('English' if zh else '中文')+'</a></nav></footer>'

def build():
    (OUT/'assets').mkdir(parents=True,exist_ok=True);(OUT/'en').mkdir(exist_ok=True)
    for name in ['tokens.css','slice.css','motion.js','whole.css','whole.js']:shutil.copy2(ROOT/name,OUT/'assets'/name)
    for name in ['brand-mark.png','precision-tap-v1.png']:shutil.copy2(ROOT.parent/'assets'/name,OUT/'assets'/name)
    for lang in ['zh','en']:(OUT/('index.html' if lang=='zh' else 'en/index.html')).write_text(render(lang))
    frames={'moment':0,'gesture':.22,'source':.42,'transition':.64,'review':.88,'note':.88}
    for name,progress in frames.items():
        text=render('zh').replace('data-story aria-label=',f'data-story data-preview-progress="{progress}" aria-label=')
        if name=='note':text=text.replace('data-preview-progress=', 'data-preview-note data-preview-progress=')
        text=text.replace('<title>',f'<title>STATE FRAME {name} / ')
        (OUT/f'frame-{name}.html').write_text(text)
    (OUT/'frames.json').write_text(json.dumps({'kind':'HTML state definitions, not rendered screenshots','frames':frames,'audio':DATA['audio'],'note':DATA['note']},ensure_ascii=False,indent=2)+'\n')
    print(OUT)
if __name__=='__main__':build()
