# 迹库 / Tracebook（迹库）

**用户自己的资料与记忆系统。** 把录音、本地工具输入和已有文字资料归集到自己的文件夹，保留来源、版本和人工修改，再逐步整理成可确认、可更正的个人记忆。

An early-stage personal memory system with native Apple clients and user-owned files.

[开发说明](docs/development.md) · [架构与能力](docs/architecture.md) · [持续迭代清单](ROADMAP.md) · [参与开发](CONTRIBUTING.md) · [安全说明](SECURITY.md)

产品体验以 [Auday（录音与记忆应用）的流程研究](docs/auday-experience.md)为主要对照，保留本项目的多来源资料、用户文件夹和个性化采集入口。后续官网开发可从[云电脑协作交接](docs/website-brief.md)开始；官网目前尚未实现。

## 当前能做什么

| 能力 | 当前状态 |
| --- | --- |
| 录音与回放 | 手机、手表采集，支持暂停、保存、恢复与回放；多种快捷入口共用采集链路 |
| 本地工具输入 | Codex（编程助手）与 Claude Code（编程助手）目录发现、预览确认、增量归档和统计；支持已适配的压缩历史、暂停与恢复 |
| 文字资料导入 | 支持 `.md`、`.markdown`、`.txt`、`.text`，多文件预览、去重、来源版本、阅读和检索 |
| 聊天导入 | 读取用户导出的 JSON（结构化文本）或 ZIP（压缩包）；区分用户与助手，保留原日期与来源 |
| 文件持有 | 用户自选资料夹；保留历史，人工修改发生冲突时另存新版本；文字可导出 |
| 资料对话 | 本地检索、来源片段与原文阅读，当前不会生成模型回答 |
| 每日分析 | 已有可配置文字模型适配器、引用和失败保护；真实模型质量尚未验收 |
| 记忆 | 四类文字来源可手动提出候选；确认、修订、停用、恢复、原文片段及历史已实现；自动模型记忆整理仍在开发 |
| 传输与同步 | 持久队列、校验、去重、接收回执与资料夹交换已有；真实云盘异地及后台链路仍待验收 |
| 外部只读接入 | 独立凭证、明确资料范围、到期与撤销；附客户端和接口描述 |

**这是早期源码版本。语音转写适配器与任务队列已实现，真实识别质量仍待验收；复杂文档解析、统一模型记忆整理和真实云端同步尚未全部完成。** 不提供已公证安装包，也不承诺克隆后即可获得完整自动记忆服务。具体缺口和验收标准见 [ROADMAP（迭代清单）](ROADMAP.md)。

## 从源码开始

需要 Xcode（苹果开发工具）26 或以上及手机、手表平台组件。当前桌面内置节点依赖系统 `/usr/bin/python3`。最低部署目标为 macOS（苹果电脑系统）14、iOS（苹果手机系统）18、watchOS（苹果手表系统）11。

```sh
git clone https://github.com/Hanyan179/Tracebook.git
cd Tracebook
python3 scripts/prepare-node-runtime.py
python3 scripts/generate-project.py
xcodebuild -project VoiceMemory.xcodeproj -scheme VoiceMemoryMac \
  -destination 'platform=macOS' -derivedDataPath DerivedData/Mac \
  CODE_SIGNING_ALLOWED=NO build
```

手机、手表构建，独立节点、隔离测试和个人签名设置见[开发说明](docs/development.md)。压缩依赖从官方包仓库下载并校验，见[第三方依赖](THIRD_PARTY.md)。

## 资料与隐私

- 来源目录只读，先预览再确认。登录文件与完整配置不归档；常见凭证过滤不代表全部敏感信息识别。
- 导入资料不会自动发送给模型，也不会自动成为本人事实。文档作者、他人发言和助手回复需要区分。
- 节点默认只监听本机。新资料类型不会静默扩大旧的外部读取授权。
- 原音、数据库、个人配置、真实来源统计、本机交接和验收截图不在公开仓库中。
- 文字导出是便携资料，不是完整备份恢复；真实资料需要独立备份。

## 项目结构

```text
Apple/                    原生手机、手表、电脑界面
Sources/VoiceMemoryCore/   录音、导出、校验与持久队列
Tests/                    核心模块测试
node/                     本机资料节点与节点测试
integrations/             外部只读接入包
scripts/                  工程、依赖与公开文件检查
docs/                     公开架构、开发和研究资料
```

已有自动测试覆盖导入去重、压缩历史恢复、人工编辑保护、导出、引用约束和权限隔离。持续检查的状态以对应提交为准；合成测试不能代替真实模型、云盘或实体设备验收。

## 开源与研究

采用 [MIT（宽松开源许可）](LICENSE)。保留现有标志与内部工程标识，第三方依赖遵守各自许可。

参考了记忆提取、时序事实、文件记忆、文档解析和增量检索的开源实现，具体来源与取舍见[开源研究](docs/open-source-research.md)。当前优先把资料导入、证据引用与记忆修订做可靠，欢迎按[迭代清单](ROADMAP.md)参与。
