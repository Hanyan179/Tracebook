"""A single fictional project: distinct sources, explicit scope and a real example download."""
from html import escape
import json


def continuation(lang, data):
    def t(zh, en):
        return zh if lang == 'zh' else en
    def e(value):
        return escape(str(value), quote=True)
    def identity(item):
        return f"{item['kind']} / {item['id']} / v{item['version']}"
    def badge():
        labels = {
            'unselected': t('关联记忆：尚未选择', 'Linked memory: not selected'),
            'candidate': t('关联记忆：待确认', 'Linked memory: pending'),
            'confirmed': t('关联记忆：已确认', 'Linked memory: confirmed'),
            'inactive': t('关联记忆：已停用', 'Linked memory: inactive'),
        }
        attrs = ' '.join(f'data-{key}="{e(value)}"' for key, value in labels.items())
        return f'<span class="memory-badge" data-memory-badge {attrs}>{labels["unselected"]}</span>'

    a, n, i, d = (data[key] for key in ('audio', 'note', 'input', 'document'))
    lines = n['lines_' + lang]
    quote = e(lines[1])
    title = e(n['title_' + lang])
    sid = identity(n)
    prefix = '' if lang == 'zh' else '../'
    arrow = '<span class="arrow" aria-hidden="true"></span>'
    wave = '<svg viewBox="0 0 340 66" preserveAspectRatio="none" aria-hidden="true">' + ''.join(
        f'<path d="M{x} {33 - (5 + (x * 17 % 27)) / 2}v{5 + (x * 17 % 27)}"/>' for x in range(3, 340, 5)
    ) + '</svg>'
    note_paragraphs = ''.join(f'<p>{e(line)}</p>' for line in lines)
    commands = [
        'python3 integrations/lifto-library/scripts/lifto.py search "' + t('声音散步', 'sound walk') + '" --scope notes',
        'python3 integrations/lifto-library/scripts/lifto.py query "' + t('下雨', 'rains') + '" --scope notes',
        'python3 integrations/lifto-library/scripts/lifto.py read note ' + n['id'],
    ]
    tabs = ''.join(
        f'<button type="button" data-command-step="{idx}" data-command="{e(command)}" aria-pressed="{str(idx == 0).lower()}" hidden><span>0{idx + 1}</span>{label}<small>{technical}</small></button>'
        for idx, (command, label, technical) in enumerate(zip(commands, [t('找资料', 'Find material'), t('找原句', 'Find the passage'), t('读全文', 'Read the original')], ['search', 'query', 'read']))
    )
    source_handles = f'''
<button class="source-handle audio-handle" type="button" data-material-select="audio" aria-pressed="false" hidden><span>{t('原音 / ', 'AUDIO / ')}{e(a['title_' + lang])}</span><small>00:42 · {t('待添加文字 · 无播放音频', 'Text not added · No audio playback')}</small>{wave}<span class="mono">{identity(a)}</span></button>
<button class="source-handle note-handle" type="button" data-material-select="note" aria-pressed="true" hidden><small>{t('文字笔记', 'TEXT NOTE')}</small><span>{title}</span><span class="mono">{sid}</span></button>
<button class="source-handle input-handle" type="button" data-material-select="input" aria-pressed="false" hidden><small>Codex / Claude Code · {t('已适配工具会话', 'ADAPTED TOOL SESSION')}</small><span>{e(i['text_' + lang])}</span><span class="mono">{identity(i)}</span></button>
<button class="source-handle document-handle" type="button" data-material-select="document" aria-pressed="false" hidden><small>{t('文字文件 / 修改时间', 'TEXT FILE / MODIFIED')} 10/21 18:05</small><span>{e(d['title_' + lang])}</span><span class="mono">{identity(d)}</span>{arrow}</button>'''
    material_details = f'''
<article class="material-detail" data-material-detail="note" tabindex="-1"><p class="detail-eyebrow">{t('文字笔记', 'TEXT NOTE')} · 10/21 10:35</p><h3>{title}</h3><p class="material-first-line">{e(lines[0])}</p><p class="material-context">{e(lines[1])}</p><a href="#work-source" data-source-route data-route-step="1">{t('找回雨天安排', 'Find the rainy-day plan')}{arrow}</a><span class="mono">{sid}</span></article>
<article class="material-detail" data-material-detail="audio" tabindex="-1"><p class="detail-eyebrow">{t('原音来源', 'ORIGINAL AUDIO')} · 10/21 09:10</p><h3>{e(a['title_' + lang])}</h3>{wave}<p>{t('只有原音，没有文字。这条资料不会替你回答路线问题。', 'Audio only, with no text. This source cannot answer the route question for you.')}</p><span class="mono">00:42 · {identity(a)}</span><small>{t('合成波形示意，无可播放音频。', 'Synthetic waveform illustration. No playable audio.')}</small></article>
<article class="material-detail" data-material-detail="input" tabindex="-1"><p class="detail-eyebrow">{t('归档的用户指令', 'ARCHIVED USER INSTRUCTION')} · 10/21 16:20</p><h3>{e(i['title_' + lang])}</h3><blockquote>{e(i['text_' + lang])}</blockquote><span class="mono">{identity(i)}</span><small>{t('已适配会话的合成摘段，不是在网页执行工具。', 'A synthetic excerpt from an adapted session, not tool execution on this page.')}</small></article>
<article class="material-detail" data-material-detail="document" tabindex="-1"><p class="detail-eyebrow">{t('修改时间', 'MODIFIED')} · 10/21 18:05</p><h3>{e(d['title_' + lang])}</h3><div class="route-lines">{''.join('<p>'+e(line)+'</p>' for line in d['lines_' + lang])}</div><span class="mono">{identity(d)}</span><small>{t('已有文字文件示意，不是地图导航或原始创作时间。', 'An existing text-file illustration, not navigation or an original creation timestamp.')}</small></article>'''
    return f'''
<script id="scenario-data" type="application/json">{json.dumps(data, ensure_ascii=False).replace('<', '&lt;')}</script>
<section class="material-scene" id="library" data-material-scene>
  <div class="material-stage">
    <div class="material-title"><p class="overline">03 / {t('一件事，几种来源', 'ONE PROJECT, DIFFERENT SOURCES')}</p><h2>{t('一场声音散步，<br>四处留下线索。', 'One sound walk.<br>Clues in different places.')}</h2></div>
    <p class="fiction-label">{t('虚构使用情境', 'FICTIONAL SCENARIO')}<span>{t('周六声音散步', 'Saturday sound walk')}</span></p>
    <div class="river-ribbon"><img src="{prefix}assets/river-walk-scene.png" width="1982" height="793" alt="" loading="lazy"><span>{t('AI 场景图 · 非真实出行', 'AI SCENE · NOT A REAL OUTING')}</span></div>
    <div class="material-handles">{source_handles}</div>
    <div class="material-reader" aria-label="{t('合成来源详情', 'Synthetic source details')}">{material_details}<button class="text-action" type="button" data-material-return hidden>{t("回到这条文字笔记", "Back to the text note")}{arrow}</button></div>
    <a class="material-progress" href="#work-source" data-source-route data-route-step="1">{t('带着问题，继续', 'Continue with a question')}{arrow}</a>
  </div>
  <details class="quiet-details section-note"><summary>{t('这四条资料的边界', 'About these four sources')}</summary><p>{t('全部为固定合成资料，不是用户出行或天气预报。次日回顾只含笔记与录音，工具会话和文档在资料库中另有来源。聊天导入归笔记，工具会话归 input；支持已适配 UTF-8 文字与 PDF 已有文字，不做扫描件 OCR，不代表 Office。保留可用时间，未知时间明确标记；导入不等于发送模型。', 'All material is fixed and synthetic, not a real outing or weather forecast. Daily review includes notes and recordings only; sessions and documents keep separate library sources. Imported chats become notes and adapted tool sessions become input. Adapted UTF-8 text and existing PDF text are supported, not scanned-page OCR or general Office files. Available times are retained and unknown times are marked. Importing does not send material to a model.')}</p></details>
</section>
<section class="workbench" id="workbench" data-step="0"><div class="workbench-motion"><div class="workbench-stage">
  <div class="question-heading"><p class="overline">04 / {t('带着来源，接着做', 'CONTINUE WITH THE SOURCE')}</p><h2>{t('如果下雨，<br>当时怎么安排？', 'If it rains,<br>what was the plan?')}</h2><p>{t('换一个工具，仍然能找回当时那句话。', 'A different tool. The words you left are still there.')}</p></div>
  <div class="command-tabs">{tabs}</div>
  <article class="recovered-source" id="work-source" tabindex="-1">
    <p class="recovery-label">{t('取回的原句，不是生成回答', 'RETRIEVED TEXT, NOT A GENERATED ANSWER')}</p><span class="mono">{sid}</span>
    <div data-command-result="0"><span class="match-count">{t('1 条匹配来源', '1 MATCHING SOURCE')}</span><h3>{title}</h3><p>10/21 · 10:35</p><button class="text-action" type="button" data-show-query hidden>{t('找出当时的原句', 'Find the words you left')}{arrow}</button></div>
    <div data-command-result="1" hidden><blockquote class="condition-quote"><mark>{quote}</mark></blockquote><button class="text-action" type="button" data-show-read hidden>{t('展开完整原文', 'Open the full original')}{arrow}</button></div>
    <div data-command-result="2" hidden><div class="full-source-header"><h3>{title}</h3><span>10/21 · 10:35</span></div><div class="full-source-prose"><p>{e(lines[0])}</p><p><mark>{quote}</mark></p><p>{e(lines[2])}</p></div>{badge()}<a class="memory-route" href="#memory">{t('选择要保留的一段', 'Choose what to keep')}{arrow}</a></div>
    <div class="source-nojs">{note_paragraphs}<a href="#memory">{t('手动选择记忆依据', 'Choose the memory evidence')}</a></div>
  </article>
  <div class="technical-rail"><p>{t('只读命令示例 · 不执行命令', 'READ-ONLY COMMAND EXAMPLE · NOT EXECUTED')}</p><pre><code data-command-output>{e(commands[0])}</code></pre></div>
</div></div><details class="quiet-details workbench-conditions"><summary>{t('只读授权与运行条件', 'Read-only access and setup')}</summary><p>{t('从仓库根目录执行，先配置 LIFTO_URL 与独立只读凭证文件路径 LIFTO_TOKEN_FILE。示例编号需替换为检索返回的真实编号。仅 capabilities / search / query / read，不写文件、不批准记忆、不录音、不改设置。query 是检索，不是模型回答；客户端另可明确选片段预览后发送模型。工具会话归档和只读检索是两个方向。', 'Run from the repository root after configuring LIFTO_URL and LIFTO_TOKEN_FILE, the path to a separate scoped credential file. Replace the example ID with the real retrieval result. Only capabilities / search / query / read: no file writes, memory approvals, recording or settings changes. Query retrieves text, not model answers; clients separately offer explicit passage selection and preview before model requests. Archiving sessions and read-only retrieval are separate directions.')}</p></details></section>
<section class="memory-section section-pad" id="memory"><div class="section-heading"><p class="overline">05 / {t('由你决定', 'YOUR DECISION')}</p><h2>{t('有用的这一句，<br>由你决定留下。', 'One useful line.<br>Yours to keep.')}</h2></div>
  <div class="scope-reading"><p class="scope-source">{title} · <span class="mono">{sid}</span> · 10/21 10:35</p><p>{e(lines[0])}</p><button type="button" class="scope-choice" data-scope-select aria-pressed="false" hidden><span>{quote}</span><small data-scope-label data-idle="{t('只保留雨天这一段', 'Keep only the rainy-day part')}" data-selected="{t('已选择雨天这一段', 'Rainy-day passage selected')}">{t('只保留雨天这一段', 'Keep only the rainy-day part')}</small></button><p class="scope-nojs">{quote}</p><p>{e(lines[2])}</p></div>
  <noscript><p class="memory-note">{t("当前为静态阅读：完整原文可读，选择与确认演示需要启用 JavaScript。", "Static reading mode: the complete original is available. Selection and confirmation demonstrations require JavaScript.")}</p></noscript><div class="scope-connector" aria-hidden="true"></div>
  <div class="memory-decision" data-memory-state="unselected"><div class="memory-state-line"><span>{t('仅本次安排', 'THIS OCCASION ONLY')}</span><p role="status" id="memory-state" tabindex="-1" data-memory-status data-unselected="{t('尚未选择依据', 'No evidence selected')}" data-candidate="{t('手动候选 · 待本人确认', 'Manual candidate · Awaiting confirmation')}" data-confirmed="{t('已确认 · 原文依据保留', 'Confirmed · Source retained')}" data-inactive="{t('已停用 · 历史与依据保留', 'Inactive · History and source retained')}">{t('尚未选择依据', 'No evidence selected')}</p></div>
    <p class="memory-placeholder" data-memory-empty>{t('先选择上面的原句。决定留下什么，再确认。', 'Choose the source sentence above. Decide what stays, then confirm.')}</p>
    <div data-candidate-content hidden><h3>{e(data['memory']['text_' + lang])}</h3><p>{t('原文、时间与来源仍然保留。没有推断成长期偏好。', 'The original, time and source remain. No lasting preference is inferred.')}</p><div class="memory-actions"><button type="button" data-memory-action data-state="unselected" data-confirm="{t('确认这次安排', 'Confirm this arrangement')}" data-deactivate="{t('停用这条记忆', 'Deactivate this memory')}" data-restore="{t('恢复这条记忆', 'Restore this memory')}" hidden>{t('确认这次安排', 'Confirm this arrangement')}</button><button class="text-button" type="button" data-memory-reset hidden>{t('重置网页演示', 'Reset web demo')}</button><a href="#example-file">{t('查看示例文件', 'See the example file')}{arrow}</a></div></div>
  </div>
  <p class="memory-note">{t('手动摘段与编辑的网页合成演示，不是客户端运行截图；不会写入真实资料。', 'A synthetic webpage demonstration of manual quotation and editing, not a running client screen. No real material is changed.')}</p>
  <details class="quiet-details"><summary>{t('确认、修订与模型的边界', 'Confirmation, revision and model boundaries')}</summary><p>{t('四类来源可手动提出候选、确认、修订、停用与恢复，保留原文依据、版本与历史。已确认内容不能静默改写；本页重新选择需先重置演示。客户端也可明确发起模型候选与批量整理，仍由人确认；定时批量和自动关系判断尚未实现。', 'Four source kinds support manual candidates, confirmation, revision, deactivation and restoration with evidence, versions and history. Confirmed content must not be silently changed; this webpage requires a demo reset before a new selection. Clients can also explicitly request model candidates and batch organization, still requiring human confirmation. Scheduled batch organization and automatic relationship judgments are not implemented.')}</p></details>
</section>
<section class="files-section section-pad" id="example-file"><div class="section-heading"><p class="overline">06 / {t('带着来处', 'WITH ITS SOURCE')}</p><h2>{t('这一段，<br>带着来处带走。', 'Take it with<br>its source.')}</h2></div><div class="export-artifact"><div class="export-title"><h3>sound-walk-demo.md</h3><span>{t('合成示例 · 非客户端导出', 'SYNTHETIC EXAMPLE · NOT A CLIENT EXPORT')}</span></div><div class="export-layout"><div class="export-original"><p class="file-meta mono">fictional_demo: true<br>source: {sid}<br>date: 10/21 10:35</p>{note_paragraphs}</div><div class="export-memory"><p class="overline">{t('这次选择', 'YOUR SELECTION')}</p><p role="status" data-export-status>{t("尚未选择保留内容", "No memory selected")}</p><p data-export-memory>{t("完整原文保留在此。", "The complete original is retained here.")}</p><span class="mono">{sid}</span></div></div><div class="export-actions"><button type="button" class="outline-button" data-download-demo hidden>{t('下载示例资料', 'Download example material')}<span class="down-arrow" aria-hidden="true"></span></button><p>{t('下载当前网页合成状态，不是你的个人资料。<br>不含原音文件、凭证或备份包。', 'Downloads the current synthetic webpage state, not personal material.<br>No audio binaries, credentials or backup package.')}</p><span role="status" data-download-status></span></div></div></section>'''
