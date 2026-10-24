# 第三方依赖

## pyzstd（压缩会话解码库）0.18.0

用于读取本地压缩会话，不用于模型推理。当前苹果节点使用系统 Python（编程语言）3.9，因此固定到仍支持该运行时的 0.18.0；项目最新版本的运行时要求不同。

- [上游项目](https://github.com/Rogdham/pyzstd)
- [固定版本发布元数据](https://pypi.org/pypi/pyzstd/0.18.0/json)
- 上游许可证为 BSD-3-Clause（三条款许可）；分发包含的底层组件许可证以轮包内声明为准。
- `scripts/prepare-node-runtime.py` 下载两个苹果架构的官方轮包，核对固定 SHA-256（安全散列校验值），解包到忽略的 `DerivedData/PythonRuntime/`。工程将该目录及轮包内的完整许可证一并放进桌面应用。
- 未向系统安装软件包，也未依赖用户机器上的压缩命令。其他平台运行节点时使用 `node/requirements.txt` 安装依赖。
- 当前已在本机苹果芯片与系统运行时执行解码；其他架构和发布签名仍需独立验收。

## uniseg（字符分段库）0.10.1

用于长资料的 Unicode（统一字符编码）扩展字素边界，避免在组合字符和表情中间切段。标准库按字符标量切片不能保证这一边界；因此增加固定版本的纯 Python（编程语言）依赖，兼容现有 3.9 运行时，无需额外编译器或系统安装。

- [固定发布](https://pypi.org/project/uniseg/0.10.1/)与[字符边界文档](https://uniseg-py.readthedocs.io/en/latest/graphemecluster.html)。该版本实现 Unicode（统一字符编码）16 的规则，不宣称跟随系统自动升级为更新标准。
- 许可证为 MIT（宽松许可），已核对轮包内 `licenses/LICENSE`。下载校验值固定为 `74fe3f7b4ee46bbd703a70ea1e5cd28e04f63eee5af9a10388d93316c554e358`。
- `scripts/prepare-node-runtime.py` 将通用轮包及原有许可证解包到忽略的 `DerivedData/PythonRuntime/common`，随电脑应用分发。其它平台通过 `node/requirements.txt` 安装固定版本。
- 优先段落边界和 ASCII（基本字符集）快速处理；较复杂文字使用字素边界。超过 200 万字符、过长组合簇或连续超过 256 个组合/格式符号会明确拒绝分段并保留原文，防止极端输入造成长时间阻塞。范围计算在资料库锁外完成，仅缓存最近八个内容版本的范围，不缓存原文。

## PDF（便携文档）解析

苹果电脑使用系统 PDFKit（文档框架），通过项目自己的 `PDFTextWorker` 工具提取文字。实际中文样本对比发现不同解析器的字形结果有差异，因此电脑缺少原生工具时明确失败，不静默切换解析器。

- 其他支持的类 Unix（操作系统）节点使用 [pypdf 6.21.2](https://pypi.org/project/pypdf/6.21.2/)，采用 BSD-3-Clause（三条款许可）；固定轮包校验值为 `1d44d1a3a200f25062adff0085b02040e4646431ac2ccac22e42679e91466084`。
- Python（编程语言）3.9 的类型兼容依赖 [typing_extensions 4.15.0](https://pypi.org/project/typing-extensions/4.15.0/)，采用 PSF-2.0（Python 软件基金会许可）；固定轮包校验值为 `f0fa19c6845758ab08074a0cfa8b7aecb71c999ca73d62883bc25cc018c4e548`。
- 准备脚本保留两份轮包内的完整许可证，随节点运行时打包；不向系统安装。解析使用子进程和资源限额，不包含图片文字识别。能力与限制见[架构说明](docs/architecture.md)。

开源调研中的其他项目只作为设计与实现模式参考，没有把其源码直接复制到产品。具体仓库、版本、许可标识和取舍见 [开源研究](docs/open-source-research.md)。
