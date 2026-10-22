# Tracebook website

中英文静态官网：产品首页、开始使用、隐私说明。内容依据 2026-10-22 的公开 README、ROADMAP、website brief、开发与安全说明。仅修改 `website/`，不运行资料节点，不部署。

## Build and preview

Python 3.9+，只使用标准库，无需安装前端依赖、账号或密钥。

```sh
python3 website/build.py
python3 -m http.server 4317 --bind 127.0.0.1 --directory website/dist
```

在运行服务器的同一台机器打开 `http://127.0.0.1:4317/`。英文入口为 `/en/`。输出 `website/dist/` 可由普通静态服务器提供，不依赖 SPA rewrite；未配置任何托管或部署。

## Checks

```sh
python3 -m unittest discover -s website/tests -v
# Stage only the intended website files before checking the public index:
git add website
python3 scripts/check-public-tree.py
```

可选真实浏览器检查（不属于网站运行时依赖）：检查环境需已安装 Python Playwright 与 Chromium。

```sh
python3 website/tests/browser_check.py
```

脚本用本机临时端口提供静态输出，Chromium sandbox 保持开启。生成桌面 1440 px、窄屏 390 px 的六页截图至忽略目录 `website/qa-output/`，检查无横向溢出、语言往返、键盘跳过链接、减少动态效果和无 JavaScript 导航。测试浏览器使用临时上下文，不连接个人账户。截图仍须人工查看，自动检查不替代视觉和读屏验收。完整执行状态见 [QA.md](QA.md)。

## Structure and maintenance

- `build.py`: 双语文案、共享页面结构和静态生成器。
- `assets/style.css`: 系统字体、响应式排版、清晰焦点和 reduced-motion 规则。
- `assets/brand-mark.png`: 原样复用项目品牌符号，未重画。
- `assets/favicon.png`: 原样复用项目现有 64 px Mac 图标。
- `tests/`: 结构、链接、能力边界与可选浏览器检查。
- `dist/`: 可重复生成输出，忽略、不提交。

语言切换是对应页面之间的普通链接；所有内容和导航无需 JavaScript。没有统计脚本、收集表单、账号、Cookie、外部字体或运行时第三方资源。链接均为真实仓库或维护文档，不提供下载、等待名单或假产品演示。流程图仅是文字说明，并明确标注不是运行画面。

更新产品能力时先核对源码和实际验收，再同步中英文、日期及边界测试。不要将官网静态检查称为客户端、真实模型或同步验收。新增域名、托管、表单、分析或收费服务需另行明确授权。
