# Windows 下载体验版

[下载便携 ZIP](https://github.com/Octo403/AnotherMe/releases/download/v0.1.0-preview.1/AnotherMe-Portable-Windows-x64.zip) · [预览版说明](https://github.com/Octo403/AnotherMe/releases/tag/v0.1.0-preview.1)

面向 Windows 10/11 x64 的本地 ZIP。解压后双击 `Start.bat`，程序自带独立 Python、所需 Python 依赖和 LibreOffice 文档转换器，自动打开本机浏览器。重复双击复用同目录的现有服务；端口占用时尝试其他端口。`Stop.bat` 只停止对应解压副本的界面，后台资料任务按既有持久化机制继续或恢复。无需安装 Docker、系统 Python、Word 或 Codex CLI。

首次使用引导复用“设置”的模型配置：填写用户自己的 API 地址、Key 与型号，读取型号目录或手动填写，完成合成 JSON 连接测试后保存。勾选加密保存时使用当前 Windows 账户 DPAPI，Key 不写入浏览器存储、公开响应或明文配置；换账户后应重新填写。未配置模型可先选择本地筛选，仍能导入、处理和查询资料。API 负责题库找题意图理解与第二轮筛选，不需要 GPU 或下载本地模型；调用需要联网和自己的服务额度。ChatGPT 登录仍是可选的原有方式，需要用户自己安装、登录 Codex CLI。个人资料回答保持独立配置，便携版默认合成资料、离线模式。

资料保存位置：

```text
解压目录/
  Start.bat
  Stop.bat
  使用说明.txt
  示例资料/                 原创虚构试题，用于首次体验
  runtime/                  独立 Python 与依赖
  tools/libreoffice/        本地 DOC / DOCX 转 PDF
  user-data/                首次启动后创建
    question_bank/          原件、处理结果、题库数据库
    application/            会话、偏好和独立合成资料
    desktop.json            当前副本的端口信息
```

发布包按明确目录构建，仅包含应用、运行环境、转换器、说明和原创示例；不复制项目 `.env`、现有 `data/`、微信资料、模型权重或个人凭证。启动入口直接创建独立设置，不加载开发目录 `.env`；后台 worker 使用同一个包内 Python。

备份应在停止服务且后台处理完成后复制整个 `user-data`。更新时解压新包，再复制旧 `user-data`；不要覆盖为新的空目录。密钥与 Windows 账户绑定，不能把开发者的加密偏好当成可分发凭证。

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
