# Windows 下载体验版

新增的微信入口见 [微信资料同步说明](WECHAT_SYNC.md)：在“资料 → 个人资料”添加一个会话，读取本机导出并复核入库。个人微信不上传题库模型或 MinerU；自动采集需另行准备上游 v1.3.5 工具环境。

preview.4 已用实际 preview.3 ZIP 应用补丁验收，47.9 秒完成旧资料、会话、加密凭据保留和微信复核后本机 Qwen 问答；Edge 桌面及 390px 界面通过。上游导出格式使用虚构数据库验证，未实际采集真实微信。[验收记录](verification/wechat-sync-20261011.json)

[下载便携 ZIP](https://github.com/Octo403/AnotherMe/releases/download/v0.1.0-preview.4/AnotherMe-Portable-Windows-x64.zip) · [预览版说明](https://github.com/Octo403/AnotherMe/releases/tag/v0.1.0-preview.4)

已下载 preview.1 / preview.2 / preview.3 的用户可用 [小补丁 ZIP](https://github.com/Octo403/AnotherMe/releases/download/v0.1.0-preview.4/AnotherMe-preview1-2-3-to-preview4-patch.zip)：先停止界面并等待后台资料处理结束，把补丁内文件复制到原解压目录，覆盖同名文件，再双击 `Start.bat`。补丁不含 `user-data`；保留原来的资料目录。

面向 Windows 10/11 x64 的本地 ZIP。解压后双击 `Start.bat`，程序自带独立 Python、所需 Python 依赖和 LibreOffice 文档转换器，自动打开本机浏览器。重复双击复用同目录的现有服务；端口占用时尝试其他端口。`Stop.bat` 只停止对应解压副本的界面，后台资料任务按既有持久化机制继续或恢复。无需安装 Docker、系统 Python、Word 或 Codex CLI。

首次使用引导复用“设置”的模型配置：填写用户自己的 API 地址、Key 与型号，读取型号目录或手动填写，完成合成 JSON 连接测试后保存。勾选加密保存时使用当前 Windows 账户 DPAPI，Key 不写入浏览器存储、公开响应或明文配置；换账户后应重新填写。未配置模型可先选择本地筛选，仍能导入、处理和查询资料。API 负责题库找题意图理解与第二轮筛选，不需要 GPU 或下载本地模型；调用需要联网和自己的服务额度。ChatGPT 登录仍是可选的原有方式，需要用户自己安装、登录 Codex CLI。个人资料回答保持独立配置，便携版默认合成资料、离线模式。

Windows 新配置默认勾选加密保存；旧用户已选择的“仅本次有效”保持不变。填写 MinerU Token 后，勾选“用当前 Windows 账户加密保存 Key / Token”，点击“保存模型与解析设置”。状态应显示“已用当前 Windows 账户加密保存，重启可用”；输入框始终为空，避免回显凭证，空白不表示丢失。若显示“仅本次有效”，关闭服务后确实需要重填。密钥保存在 `user-data/application/ui.sqlite3` 的加密偏好中，更新时必须保留原 `user-data`，也不能跨 Windows 账户直接解密。

个人资料的“真实模型 · 本机”需要单独连接本机已运行的 OpenAI 兼容服务。在“设置 → 个人资料回答模式”填写 API 地址、读取或填写实际模型型号，测试连接并保存后即可选择真实模型模式。无鉴权服务 Key 可留空；有鉴权时填写本机服务自己的 Key。只接受 `127.0.0.1`、`localhost`、`::1`，不会复用在线题库模型发送个人资料。源码开发机已有的 Qwen 服务示例为 `http://127.0.0.1:8765/v1`、`qwen3-4b-local`，JSON 格式选 JSON Schema；该模型权重约 2.5 GB，未随便携版分发。连接配置保存不等于模型已启动，请先启动自己的服务。

资料保存位置：

```text
解压目录/
  Start.bat
  Stop.bat
  Diagnose.bat              仅检查运行环境依赖
  launcher.py               启动与诊断入口
  使用说明.txt
  示例资料/                 原创虚构试题，用于首次体验
  runtime/                  独立 Python 与依赖
  tools/libreoffice/        本地 DOC / DOCX 转 PDF
  user-data/                首次启动后创建
    question_bank/          原件、处理结果、题库数据库
    application/            会话、偏好和独立合成资料
    desktop.json            当前副本的端口信息
    startup.log             启动阶段与长时间等待的线程位置
    startup-error.log       启动异常（发生异常时生成）
```

发布包按明确目录构建，仅包含应用、运行环境、转换器、说明和原创示例；不复制项目 `.env`、现有 `data/`、微信资料、模型权重或个人凭证。启动入口直接创建独立设置，不加载开发目录 `.env`；后台 worker 使用同一个包内 Python。

备份应在停止服务且后台处理完成后复制整个 `user-data`。更新时解压新包，再复制旧 `user-data`；不要覆盖为新的空目录。密钥与 Windows 账户绑定，不能把开发者的加密偏好当成可分发凭证。

启动窗口会立即显示阶段。网页未自动打开时，复制窗口里的 `http://127.0.0.1:端口` 到浏览器。启动卡住时查看 `user-data/startup.log`，也可双击 `Diagnose.bat` 检查依赖；此诊断不启动服务、不读取课程资料。解压目录不可写时，诊断日志退回 Windows 临时目录，实际路径会显示在窗口中。

ChatGPT 模式提示找不到 CLI 时，先在朋友电脑上新开一个 **Windows PowerShell**：

```powershell
where.exe codex
codex --version
codex login status
```

修复版每次检测都会读取更新后的 Windows PATH、相关环境变量，并检查常见安装目录。在程序已打开后安装 CLI，也可直接点击“重新检测 CLI / 刷新型号”，无需重启。若仍找不到，在“设置 → ChatGPT 登录 → Codex CLI 路径”粘贴 `where.exe codex` 返回的完整路径（如 `C:\Users\用户名\AppData\Roaming\npm\codex.cmd`），再刷新并保存。这个路径同时用于型号读取与第二轮筛题，重启后保留。可选环境变量为 `AITOWN_CODEX_PATH`。CLI 必须能在当前 Windows 账户中运行并登录；仅在 WSL、编辑器插件或其他 Windows 账户里登录不足以供当前便携版使用。不要复制或上传登录凭证文件。

`codex login status` 用于查看当前认证状态，参见 [OpenAI 官方认证说明](https://learn.chatgpt.com/docs/auth)。

免 CLI 的官方浏览器登录已有接入路线：[Sign in with ChatGPT — ChatGPT plan usage](https://developers.openai.com/siwc/token-sharing-open-source)。开源本地应用可通过官方 OAuth 请求使用符合资格的用户订阅额度；仅身份登录成功不足以授权模型调用，须获得相应权限并实际完成请求，仍受资格和额度限制。AnotherMe 当前尚未接入此独立流程，界面的 ChatGPT 选项仍通过 Codex CLI；仅打开 chatgpt.com 登录不能让应用自动调用模型。实现需要注册、回调校验、加密保存与刷新凭证，以及 Responses API 适配，参见 [官方集成示例](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)。

“刷新全部课程目录”需要访问 GitHub。失败时现在会显示具体 HTTP 状态码或连接原因，保留原目录和已收集资料：403 / 429 可能是限流，按提示的时间重试；其他 403 表示访问被拒绝，407 表示代理要求认证，404 表示仓库或分支未找到。不要连续点击刷新。公开接口的匿名额度与网络出口相关，参见 [GitHub 官方排错说明](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api)。无法联网刷新时，仍可使用已有题库或上传本地文件；添加网址仍需网络。

公开目录不要求 GitHub 登录。程序的 HTTP 客户端不读取浏览器 Cookie，浏览器登录不会自动给程序的 API 请求授权。匿名公开 REST 请求通常为每个出口 IP 每小时 60 次，学校网络、共享代理可能共用额度，参见 [GitHub 官方限流说明](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)。登录浏览器后重开程序恰好成功，不能据此判定需要登录；具体原因须看当次状态码与限流提示。

文档处理支持 PDF、DOC、DOCX、MD、TXT、HTML 与 ZIP；若构建时带入 7-Zip，则还支持 RAR / 7z。DOC / DOCX 使用包内 LibreOffice，独立临时配置禁止宏。转换在短临时目录完成，校验生成 PDF 后复制回资料库，避免解压路径较长导致转换器失败；原件始终保留。转换后字体、公式和分页可能与 Word 不同，仍需对照原件。DOCX 转换失败时保留可读文字、公式、附图与处理提示。扫描 PDF、旧式嵌入公式、混排答案等仍按原有缺失和待审校规则处理，不把未识别内容当作完整题目。

构建机需要 Windows x64、项目开发环境和 uv。先从 LibreOffice 官方下载 Windows x64 MSI，管理解压到独立临时目录（只提取，不安装），然后执行：

```powershell
.venv\Scripts\python.exe scripts/build_portable.py --libreoffice-dir .tmp\portable-libreoffice
# 可选：--sevenzip-dir "C:\Program Files\7-Zip"
```

构建使用独立 Python 安装目录，默认开发 venv 的 `sys.base_prefix`，可通过 `--python-home` 指定 uv 管理的独立 Python。安装实际依赖并检查包内导入与静态资源后生成 ZIP；`components.json` 记录本包依赖版本，各组件许可文件保留在发行包中。输出目录必须是新目录，版本和输出可通过 `--output` 指定。`dist/` 和构建临时数据不纳入源码。

验证命令：

```powershell
.venv\Scripts\python.exe scripts/verify_portable_runtime.py dist\AnotherMe-Portable-Windows-x64.zip --browser
```

验证会从 ZIP 解压到新的中文空格目录，限制 PATH 为系统目录，通过真实 `Start.bat` / `Stop.bat`，处理原创 MD、生成 PDF、真实 OOXML 和转换生成的 DOC，再用本机 Qwen API 做型号读取、JSON 测试与第二轮筛题，最后检查重启后的会话、模型偏好和 Key。只写独立验证副本，不修改主题库。结果写入 `data/evaluation/question_bank/portable-*.json`；失败记录保留。此验证不能替代干净虚拟机、其他 Windows 版本或不同模型服务商的验证。

2026-10-09 最终交付文件为 `dist/AnotherMe-Portable-Windows-x64.zip`，708,478,157 字节（约 708 MB）。真实 ZIP 验收 **14 项通过，用时 102.94 秒**：[公开验收摘要](verification/portable-20261009.json)；独立 Edge 首次配置、保存、跳转、本地筛选、重载与窄屏 **8 项通过**：[同一摘要中的浏览器检查](verification/portable-20261009.json)。已查看桌面与 390px 截图。四个文件均可检索：MD 2 道、DOCX 1 道、DOC 1 道、PDF 1 道；Word 转换 PDF 实际生成。全量回归 335 项，短路径转换修复后专项 26 项，JS/DOM 30 项通过。

早期失败记录保留：`portable-20261009-230309.json`、`230403.json` 是验证脚本把依赖自带测试数据或 Torch 适配代码误判为项目资料/模型；`230440.json` 暴露长路径 Word 转换失败，已通过短临时路径修复；`231643.json` 暴露快速重新打开设置与型号加载冲突，已修复后完成最终浏览器验收。以上失败不能当作成功证据。

构建依据：[uv 独立 Python](https://docs.astral.sh/uv/concepts/python-versions/#managed-python-distributions)、[LibreOffice 命令行转换与独立用户配置](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html)、[LibreOffice 官方下载](https://download.documentfoundation.org/libreoffice/stable/)。
