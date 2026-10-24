# 开发与验证

## 环境

- 原生构建使用 Xcode（苹果开发工具）26 或以上，安装手机与手表平台组件；工程使用 Swift（编程语言）6。
- 最低部署目标为 macOS（苹果电脑系统）14、iOS（苹果手机系统）18、watchOS（苹果手表系统）11。最低部署目标不等于每种系统版本均已实测。
- 独立节点支持 Python（编程语言）3.9 及以上；当前桌面应用启动 `/usr/bin/python3`，尚未分发完整解释器。源码构建可用不等于普通用户安装包已完成。

## 从源码构建

```sh
git clone https://github.com/Hanyan179/Tracebook.git
cd Tracebook
python3 scripts/prepare-node-runtime.py
python3 scripts/generate-project.py
xcodebuild -project VoiceMemory.xcodeproj -scheme VoiceMemoryMac \
  -destination 'platform=macOS' -derivedDataPath DerivedData/Mac \
  CODE_SIGNING_ALLOWED=NO build
```

准备脚本下载固定版本及校验值的官方压缩解码与字符分段轮包，放在忽略的 `DerivedData/PythonRuntime/`，随桌面构建打包，不修改系统安装。工程生成器使用标准库，不需要额外项目生成工具。

手机模拟器构建包含手表目标，不需要个人签名：

```sh
xcodebuild -project VoiceMemory.xcodeproj -scheme VoiceMemory \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath DerivedData/Phone CODE_SIGNING_ALLOWED=NO build
```

实际模拟器运行涉及钥匙串时需启用适当签名；真实设备需将 `Config/Local.xcconfig.example` 复制为忽略的 `Config/Local.xcconfig`，配置自己的团队与应用标识。不要提交个人签名、描述文件或设备编号。

## 独立节点与测试

建议使用单独的虚拟环境：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r node/requirements.txt
python -m unittest discover -s node/tests -v
```

苹果平台的核心模块测试：

```sh
swift test
```

以下目录仅用于合成测试，不要连接真实手机或选择个人工具目录：

```sh
python node/server.py --data artifacts/dev/node \
  --output artifacts/dev/library --host 127.0.0.1 --port 18765
```

调试版桌面应用可通过环境变量使用同一隔离目录；正式构建忽略这些覆盖：

```sh
VOICE_MEMORY_NODE_DATA="$PWD/artifacts/dev/node" \
VOICE_MEMORY_CLIENT_DATA="$PWD/artifacts/dev/client" \
VOICE_MEMORY_NODE_PORT=18765 \
DerivedData/Mac/Build/Products/Debug/VoiceMemoryMac.app/Contents/MacOS/VoiceMemoryMac
```

独立节点占用该端口时，桌面会尝试连接它；关闭桌面不会终止独立启动的进程。节点默认文字输出目录可在应用中选择；连接口令留在数据目录，不要写进命令、问题报告或截图。

桌面设置的“模型”分别配置语音转写、每日分析与记忆整理、资料回答。资料回答使用兼容的文字接口，先在对话中本机检索，再选择片段并预览发送；保存模型配置不会自动发送。真实密钥只在应用内填写，禁止放入测试夹具或提交。节点测试使用合成输出验证请求范围、引用、失败和恢复，不能证明真实模型的回答质量。

调试版指定 `VOICE_MEMORY_CLIENT_DATA` 时，资料夹书签保存到该目录的 `folder-selection.json`，发送与接收历史保存到 `FolderTransfers/`，不会借用普通运行环境的资料夹授权或历史队列。每端使用不同客户端目录；共享的仅是两端选择的交换资料夹。`VOICE_MEMORY_QA_FOLDER` 可在隔离验收时指定此交换资料夹，重新启动仍保留同目录任务身份；不要指向真实个人目录。未设置隔离目录的正常运行继续使用已有系统偏好与队列，不做隐式迁移。

## 持续检查与发布范围

`.github/workflows/ci.yml` 检查公开文件范围、文档链接、节点测试、工程可重复生成和无个人签名的三端构建。仅使用合成数据，不配置真实模型、云盘或个人账号。

桌面构建后还会实际启动应用包内的节点，验证合成笔记→候选→确认→历史与文件，避免仅编译成功而遗漏运行时模块。本地可执行：

```sh
/usr/bin/python3 scripts/check-packaged-node.py DerivedData/Mac/Build/Products/Debug/VoiceMemoryMac.app
```

手机与手表的大部分界面检查可使用独立模拟器，不占用实体手机。模拟器不包含应用商店，不能用购买账号下载第三方商店版应用；自有源码构建可安装。后台录音、真实传输时机、耗电、系统手势和云账号仍需真机专项。

在本地暂存文件后执行 `python3 scripts/check-public-tree.py`，检查实际将提交的内容。脚本拦截常见凭证模式、本机私人路径、数据库、签名和本地交接；它不能证明所有隐私风险都已消除，需要同时检查变更。

首次公开为源码开发版本，没有自动发布应用二进制或创建稳定版本承诺。发布安装包前还需干净机器运行、解释器分发、签名公证、升级回滚和真实链路验收。
