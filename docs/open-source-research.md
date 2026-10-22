# 个人记忆系统的开源研究

2026-10-21。沿用原生客户端与用户持有的资料夹，借鉴成熟实现补齐可靠导入和可修订的记忆。公开研究仅包含技术资料，不包含真实用户来源、扫描数量或内部验收记录。

## 核对过的开源实现

以下核对了维护方仓库、当前代码位置、相关实现与许可文件。没有采用第三方榜单分数，也没有执行下载的项目代码。下列链接固定到研究时核对的提交，以便后续复核。

| 项目与证据 | 值得采用的做法 | 对本项目的取舍 |
| --- | --- | --- |
| [Mem0（记忆提取库）](https://github.com/mem0ai/mem0/blob/2c81af3bbe27d9bccb09c772d583df9e9be72ee5/mem0/memory/main.py#L881)，Apache-2.0（阿帕奇许可） | 提取前读取相关已有记忆，区分角色与作用范围；仅把实际存储成功的记录写进成功结果和历史。当前分支已是分阶段的增量提取流程。 | 借鉴明确的记忆写入结果、来源和变更历史。不能沿用旧文章所述的流程，直接假定其当前实现会自动妥善处理所有事实冲突。 |
| [Graphiti（时序知识图谱库）](https://github.com/getzep/graphiti/blob/8e320f880e7d169a907dbf2a7ac4188586c03b55/graphiti_core/edges.py#L263)，Apache-2.0（阿帕奇许可） | 事实带来源事件、参考时间、生效与失效时间，可区分“过去正确”与“现在正确”。 | 先在已有数据库加入来源与有效期语义；现阶段不引入整套图存储服务。其运行时及图数据库依赖见[项目说明](https://github.com/getzep/graphiti/blob/8e320f880e7d169a907dbf2a7ac4188586c03b55/README.md)。 |
| [Letta（有状态智能体框架）当前实现](https://github.com/letta-ai/letta-code/blob/c8042de91a61074d26db20471db2f02b6809d537/src/agent/memory-filesystem.ts)，Apache-2.0（阿帕奇许可） | 记忆可落到可查看的文件系统；[冲突修复](https://github.com/letta-ai/letta-code/blob/c8042de91a61074d26db20471db2f02b6809d537/src/agent/memory-conflict-repair.ts)有状态记录，避免同一冲突反复处理。 | 保留用户文件夹、修改历史与冲突可见性。旧主仓库已[指向新的活跃代码](https://github.com/letta-ai/letta/blob/8816d3a8516893bfdc0fa14bfb1235226012e295/README.md)，不能把旧服务端分支当作现状。 |
| [Docling（文档解析工具）](https://github.com/docling-project/docling/blob/4f00195b8dadbc1a67f4cefae6b619c47e4e515f/docling/document_converter.py)，MIT（宽松许可） | 将多种文档格式转换成统一文档表示，保留结构，再用于下游检索；复杂格式有专门解析流程。 | 文本先用轻量导入；PDF（便携文档）、办公文件和扫描件后续评估独立解析器。当前[要求 Python（编程语言）3.10 或以上](https://github.com/docling-project/docling/blob/4f00195b8dadbc1a67f4cefae6b619c47e4e515f/pyproject.toml#L46)，不能直接放入现有 3.9 节点。 |
| [Khoj（个人资料助手）](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/khoj/processor/content/text_to_entries.py#L142)，AGPL-3.0（网络强互惠许可） | 文档、分段和来源文件分别保存，按内容校验识别增量，检索结果能回到文件。 | 学习来源关联与增量导入，不复制其服务端、模型依赖或授权体系。 |
| [ccusage（编程助手用量分析工具）](https://github.com/ccusage/ccusage/blob/c4fd02581085055c3946e33732297397911e1fcb/rust/adapters/codex/src/parser.rs#L200)，其[实际许可文件为 MIT（宽松许可）](https://github.com/ccusage/ccusage/blob/c4fd02581085055c3946e33732297397911e1fcb/apps/ccusage/LICENSE) | 流式处理日志，分别处理重放前缀与稳定标识，避免将复制的历史重复计算。 | 借鉴逐格式适配和去重验收；用量事件统计不能直接等同于个人记忆提取。 |

官方 [Codex（编程助手）压缩实现](https://github.com/openai/codex/blob/c7234c1174b27d9c55199fba0d546dc27d1b0415/codex-rs/rollout/src/compression.rs#L63)明确读取普通与压缩两类会话文件，并覆盖当前和归档目录。这也是本项目增加压缩历史读取的直接依据。

另检索到 CASS（会话检索工具），但其[许可文件](https://github.com/Dicklesworthstone/coding_agent_session_search/blob/5bfcc15ef3e0789e2f0200d5ee2ca7f1e97faa93/LICENSE)包含额外主体限制，未引入其实现。许可名称及选择只是本次工程取舍记录，不替代发布前的许可核查。

本轮唯一新增运行依赖是 pyzstd（压缩会话解码库）。选择与分发方式见 [第三方依赖](../THIRD_PARTY.md)。

## 记忆应如何形成

```mermaid
flowchart LR
    A[录音、文档、工具输入] --> B[原文与来源档案]
    B --> C[去重、版本、作者和时间]
    C --> D[有引用的记忆候选]
    D --> E[经确认的长期记忆]
    E --> F[检索与回答]
    C --> F
    E --> G[更正、失效与历史版本]
```

资料可以全面归集，记忆需要选择性整理。每条候选应明确谁说的、来自哪一份资料和哪一版、何时成立、属于偏好还是事件或项目状态，以及是否存在相反证据。模型提出的推断与用户直接陈述应分开；修正采用新版本与失效关系，保留旧来源。

现有候选记忆接口仍只支持录音编号，尚未把文档和工具输入统一接入候选生成；对话仍是资料检索，也没有真实模型质量验收。下一步应先补统一来源引用和候选的修订机制，再接入明确启用的模型整理。复杂格式解析与新工具适配按实际覆盖公布，不能靠文件名或用户角色字段宣称全部支持。

## 2026-10-22 补充：主要产品体验参考

新增 [Auday（录音与记忆应用）](https://auday.ai/)的实际界面与公开演示研究。它的 [Auday Skill（桌面接入包）](https://github.com/666ghj/auday-skill)也以 MIT（宽松开源许可）公开；本轮固定到提交 `a39eb2e9bf2f5011ae58706bfe2e5806d23c84b1`，只读核对协议、快照同步、大记录恢复、可读导出与进度输出，没有安装、运行或复制其源码。

具体可采用的体验、需要不同取舍的地方，以及真实应用与演示证据的边界，见[产品体验研究](auday-experience.md)。这项研究没有改变此前不采用电脑本地模型的产品决定，也不代表已经完成真实转写或记忆质量验收。
