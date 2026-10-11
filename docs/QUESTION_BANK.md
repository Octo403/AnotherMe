# 集合与图论题库（X3）

2026-10-09：本地收集、处理、持久化和第二轮 ChatGPT 按提问筛选已跑通。页面右上角“课程资料”可输入课程名、查看逐文件状态并打开本地附件；新收集原件默认保留。独立线性代数小批完成两份 Word、16 道题，真实 ChatGPT 筛选与服务重启恢复共 15 项检查通过。原有集合与图论库保持 73 道去重题、105 条可用出处，仍为部分覆盖；题库与个人记忆分库，题目不自动成为本人经历、学习进度或风格样本。

## 本地收集与筛题最小闭环（2026-10-09）

1. 启动 `.venv\Scripts\aitown.exe --mode demo ui`，打开 `http://127.0.0.1:8501`，点击右上角“课程资料”。
2. 输入课程名筛选，或点击“浏览全部课程”，在限定高度的列表内滚动选择课程；支持方向键、Enter 和 Escape。选择处理份数并点击“获取并准备课程”，从 FDU-Sharing 目录收集；目录未收录的课程可用下方“补充课程资料”添加网址或文件来新建。补充文件不受公开目录的 3 / 10 份试跑范围限制。
3. 进度显示真实阶段、当前文件、已处理份数（含失败）、解析状态和可检索题数。文件列表可查看失败原因、打开已保存原件或公开来源；“继续收集 / 重试失败”复用活跃任务，处理其余未完成文件。中断进程和过期未启动任务会显示为中断，重新提交可继续处理。
4. 点击右上角独立的“设置”（或课程面板内“筛题模型设置”），选择“ChatGPT 登录 · 第二轮按提问筛选”，选择型号或使用 CLI 默认值并保存。“设置”放模型、PDF 解析与技能配置；“资料库”分课程收集和个人资料两个页签。需先通过 `codex login` 登录 ChatGPT；本地资料回答的 `demo` 模式不妨碍独立 ChatGPT 筛题。
5. 点击“用这门课找题”，输入“找两道矩阵相关的题”。程序先按课程等明确条件过滤，再做本地 BGE/关键词召回与精排，最后由 ChatGPT 按本次提问筛选候选；无需先选参考题。可继续问“以第一题为参照，再找一道同类型题”。读取某题、刷新进度和重开历史不会重新筛题。

默认根目录仍为 `data/question_bank/`，可用 `QUESTION_BANK_DIR` 配置。SQLite 保存课程、来源、题目和任务；`raw/` 保存原件；`processed/` 保存转换 PDF、原页、题图及提取结果；`indexes/` 保存索引；`jobs/` 保存 worker 日志。准备、重解析和补图不再自动删除原件；显式 CLI `cleanup` 保留既有路径、完整产物与活动任务检查。以前已清理的原件不会自动补回，Word 仍可打开已有转换 PDF，需要原件时应显式重新获取。

ChatGPT 只接收当前找题查询、可选参考题及本地课程候选，输出题目 ID 和匹配理由；程序重新读取本地原题、题图与出处。模型无合适结果时返回空结果；登录、网络或额度失败时明确标注仅有本地候选。支持用户显式提供的公开网址；登录网站、自动遍历网站、云同步、复杂文件夹和自动解题暂未实现。

## 用户补充网址与文件（2026-10-09）

点击右上角“课程资料”，填写或选择课程，在“＋ 补充课程资料”中操作：

- **网址**：每行一个 HTTP / HTTPS 公网地址，每批最多 20 个，点击“添加网址并处理”。支持普通网页和 PDF、Word、MD、TXT、HTML、ZIP、RAR、7z 文件直链；不需要先进入 FDU 目录。网页提取正文、列出最多 50 个资料附件链接，可点击“＋ 添加网页资料链接”继续收集；不会自动递归抓取网站。
- **文件**：多选上传上述格式，每批最多 20 个、合计 100 MiB，点击“上传文件并处理”。先显示上传百分比，再显示后台任务阶段和逐文件结果；可以用尚未收录的课程名新建本地课程。补充入口旁显示当前目标课程及实际资料库路径。
- **重复与重试**：同课程同名上传文件（忽略英文字母大小写）、相同规范化网址默认复用已有来源；其他课程独立存放。不计算内容散列，因此不同文件名仍会登记不同来源，后续原有题目去重保留各出处。需要替换时勾选替换选项；课程正在处理时不能替换正在使用的资料，但可追加新来源，当前任务结束后自动启动后续任务。失败文件有单独重试入口；上传原件未保存成功时需重新上传。替换或重新解析会使旧题目 ID、审校、答案配对失效；替换压缩包时，已移除成员及其嵌套题目也会退出检索。

默认资料位置为项目内 `data/question_bank/`：首次原件按 `raw/<来源ID>/<原文件名>` 保存；替换的新副本在该来源的 `revision-*` 子目录，压缩成员在 `members-*` 子目录，SQLite 指向当前版本，旧原件和先前展开目录保留。这可避免 Windows 在压缩包已读后拒绝覆盖原文件；保留副本会增加本地占用，暂未提供历史副本管理。数据库仍为 `question_bank.sqlite3`，处理结果在 `processed/<来源ID>/`，每门课索引位于 `indexes/course-<课程行号>/`，任务日志在 `jobs/`。上传文件没有伪造的网络出处，题卡打开本地原件；网址保留用户原始地址和重定向后的地址，附件相对路径按最终网页地址解析。上传原件不可从公网恢复，显式 `cleanup` 也会保留。上述运行资料已在 `.gitignore` 排除。

用户补充资料在本机解析，不继承 FDU 公开 PDF 的 MinerU 在线授权。MD/TXT 使用 UTF-8，MD/TXT/HTML 直接显示标准文字题目；PDF/Word 使用现有原题图流程。下载响应给出的文件名重新用于来源分类，标有“答案”的附件不会当成题干。网页文字不能完整恢复公式、表格和图片排版，保存图片占位及缺口提示，需打开原网页核对；没有识别出独立题目时保留正文和原件，不生成虚构题目。找题选择 ChatGPT/API 时，会发送当前候选题干及配置允许的题图，上传前页面已说明。

公网下载在独立 worker 执行：限制大小、时长和重定向，每次 DNS 解析检查全部地址，仅连接解析出的公网 IP，并保持原 Host/TLS SNI；重定向到本机、内网或保留地址会失败。这里的检查用于新增网址访问的网络安全边界。文件名、落盘路径、压缩成员仍受路径约束，原件网页以附件下载，不在本地站点执行其脚本。

真实独立验证 `scripts/verify_course_supplements_runtime.py --source 文件.md --chatgpt` 使用用户给出的期末 MD、生成的单题 PDF 和 FDU 公开 MD 网址，实际上传、排队、下载、本地 BGE、两次 ChatGPT 筛题、原件 HTTP 字节核对及重启恢复，最终 **12 项通过**，约 **57.54 秒**。报告 `data/evaluation/question_bank/course-supplements-f274f4f2d6ef46059b03a3508f3b3b83.json`。首轮验收脚本重复解码失败保留为 `course-supplements-1451b051729c406a9d4ebc771a9c175e.json`；此前成功记录为 `course-supplements-51d46a74dc9c45728dd1a740e3871346.json`。另一次复测公开网址连接失败，原件上传和两次 ChatGPT 筛题仍成功，整体失败记录为 `course-supplements-a3261acead6746889218eff0f10ecc1a.json`；之后给每个公网地址单独分配最多 5 秒连接时间，避免一个不可达地址耗尽全部下载时间，并保留整体下载时限和显式重试入口。

最终全量 pytest **330 项通过**；网络连接时限调整后专项 **22 项**再次通过，课程、文字题卡、课程范围和模型配置 JS/DOM fixture 合计 **29 项通过**，包括上传原件只打开本地链接、原件丢失时明确提示重新上传。嵌套压缩包替换测试实际暴露 Windows 拒绝覆盖已读 ZIP 的问题，改为保存新副本并切换 SQLite 路径后已通过；文件未保存成功时仍保留明确失败状态，不将旧原件当成新结果。

独立无界面 Edge 实际操作目标课程与实际存储路径显示、多文件上传、新课程、重复提示、错误保留、网页附件按钮和 390px 窄屏，最终 **9 项通过**；查看了桌面和窄屏截图，证据在 `data/evaluation/question_bank/browser-supplements-20261009-final/`。此前 8 项成功记录在 `browser-supplements-20261009-complete/`；首次浏览器检查早于网页解析完成，失败记录保留在 `browser-supplements-20261009/`。这些小样本不代表所有格式或题目已审校；扫描 PDF、复杂公式与混排答案仍沿用待审校状态。

真实验证：`scripts/verify_local_course_flow.py` 从公开目录发现线性代数 48 份文件，仅下载 A/B 两份 Word，本机处理为 16 道题，保留两份原件。普通查询和参考题追问分别实际调用 ChatGPT，附 6/7 张题图、各核对 5 道候选，调用约 14.64/12.38 秒，整条验证约 60.49 秒。HTTP 读取附件/题图、会话与模型设置持久化、关闭并重建服务恢复等 **15 项通过**。记录为 `data/evaluation/question_bank/local-course-flow-3a03298a512741788a3e8d98ac736d52.json`，运行数据见报告的 `run` 目录；未写原有题库或个人库。

首轮实测发现显式参考“第一题”误走 `question_get`，已修复并真实重跑；失败记录保留为 `local-course-flow-7495831bd91346d79044632e6b0314c9.json`。最终全量 pytest **299 项通过**；表单、课程范围及模型配置的真实 JS 事件/DOM fixture 合计 **21 项通过**。浏览器自动化连接初始化失败，尚未完成本轮视觉验收；此小批检查不等于数学逐题审校或全库相关性评测。

## Markdown / TXT 标准题目与面板拆分（2026-10-09）

MD 按最外层带题号的标题分大题，内部数字列表、a/b 小节和评分标准保留在所属大题中；代码围栏中的题号、答案字样不作为分题边界。题干、行号和原文件仍保存在独立题库，来源字段区分 Markdown、纯文本和图像试卷。MD/TXT 在卡片上直接展示正文和小问，无需生成原件截图；PDF/Word 继续展示原题图，OCR 只用于内部检索。Markdown 支持标题、列表、强调、代码与安全链接，原始 HTML 作为文字；LaTeX 公式目前保留源文，并未接入完整数学排版库。

用户提供的 `2026春-期末-试题.md` 已实际复测为两道大题：残差网络保留画图与参数量小问；讲座设计保留 a/b 小节与评分标准。独立副本经过本地 BGE、两次真实 ChatGPT 纯文本筛选、HTTP 原件读取和服务重启，**11 项通过**，约 30.31 秒；报告 `data/evaluation/question_bank/markdown-course-b3bb7bd2a3eb4de097ed382cfaf47719.json`。可用 `scripts/verify_markdown_course_runtime.py --source 文件路径 --chatgpt` 复测。

本地已有来源 137 的旧解析错误地把残差网络的两个小问当成大题。已先做普通 SQLite 备份，仅重新整理该来源、重建其课程索引；原件字节和其他资料记录核对未变。题目 ID 由 310/311 替换为 312/313，旧会话引用需重新找题；修复记录 `data/evaluation/question_bank/md-source-repair-73a4937bda7043f89b37aae564469012.json`。当前 `http://127.0.0.1:8501` 已重启，保留此前回答模式与已保存模型配置。

全量 **308 项 pytest**、**28 项真实 JS/DOM fixture** 通过。专用浏览器连接失败后，用独立无界面 Edge 实际完成鼠标滚轮浏览 54 门课程、末项选择、搜索、独立设置/资料页签、真实 MD 两道题和 390px 窄屏检查，**7 项通过**，并查看桌面/窄屏截图。最终证据在 `data/evaluation/question_bank/browser-md-20261009-complete/`，其中第二题末尾截图等待平滑滚动完成后拍摄；首次浏览器尚未启动就绪的失败记录保留在 `browser-md-20261009/`，此前成功截图保留在 `browser-md-20261009-ready/` 和 `browser-md-20261009-final/`。此小样本不代表任意 Markdown 语法或所有试卷内容都已审校。

## 模型分工

| 环节 | 实际实现 |
|---|---|
| 首轮找题 | 本地 `BAAI/bge-small-zh-v1.5` 向量与 FAISS 召回，结合字符关键词，再由本地 `BAAI/bge-reranker-base` 精排 |
| 第二轮按提问筛选 | 使用已登录 ChatGPT 的 Codex CLI，从本地候选中按本次提问筛题；参考题可选，指定“第一题”时绑定会话实际题目并筛同类题；只允许返回候选内的题目 ID |
| 原题展示 | MD/TXT 按标准题目展示原文、标题和小问；PDF/Word 展示原题图及出处，识别文字留作内部检索；不调用自由回答模型改写原题或自动解题 |
| 其他对话 | 沿用项目已配置的本地 Qwen 服务 |
| 文档提取 | PDF 默认 PyMuPDF；DOCX XML 保留段落定位，Word 本地导出 PDF 后补图；可选择独立环境中的本地 MinerU 4 |

`QUESTION_SIMILAR_PROVIDER=codex_chatgpt` 启用第二轮账号调用，`QUESTION_SIMILAR_MODEL` 可留空使用 CLI 默认模型。执行 `codex login` 后用 `codex login status` 检查 ChatGPT 登录状态。程序不读取账号令牌文件，也不退回使用 API key。**本机已完成真实账号调用，无需另提供 API key。**

第二轮发送当前窄查询、可选参考题和本地候选的 ID、课程、题干、标签及题型。`QUESTION_SIMILAR_IMAGES=true` 默认附上已登记的整题原图：通常取精排前 5 道候选，请求数量大于 5 时相应增加，最多附 18 张图。跨页题按区域顺序附图；整题图片超出预算时只传文字并说明，不截去后半题。没有整题图或关闭图像时按文字判断；纯文字模式最多比较 20 道候选。Word 内嵌图可能只是一个公式或插图，不当作完整原题图输入。

图片通过 [Codex CLI 的 `--image` 参数](https://learn.chatgpt.com/docs/developer-commands?surface=cli) 附加，提示中另列图片顺序、参考/候选、题目 ID、页码及区域编号。不会发送会话历史、微信资料、个人画像或独立答案表。Codex 在临时目录运行，禁用 shell 工具和联网搜索，输出经结构与候选 ID 校验。模型只返回题目 ID 和匹配理由，程序再读取当前题干及原图；图片来源不由模型生成。若登录、额度或网络失败，结果明确标为本地候选，附失败说明。这里的第二轮是筛选本地题库；互联网找新题尚未实现。

## 来源与历史覆盖记录（2026-10-08）

以下保留当时的 Word 检查记录；实时覆盖以 `status` 命令或课程资料页面为准，不将不同处理批次的计数当作质量提升。

原仓库：[victkk/FDU-Sharing](https://github.com/victkk/FDU-Sharing)。从实际默认分支 `master` 递归发现 55 门课程、877 个文件的目录信息；仅下载选定的集合与图论资料。

| 当前计数 | 数量 |
|---|---:|
| 本课程原始文件 | 18 |
| ZIP / RAR 内登记的成员 | 13 / 15 |
| 累计下载原件与成员 | 44 |
| 已清理原件缓存 / 暂保留原件 | 25 / 19 |
| 去重后的可检索题目 / 可用来源记录 | 58 / 98 |
| 保留的原始题目记录（含重复与待审校） | 114 |
| 去重卡片可展示题图 | 66 |
| 部分完成 / ready / 不支持 / 待审校 / 跳过 / 已展开 | 23 / 0 / 16 / 3 / 2 / 2 |
| 当前检索题目的确认年份 | 2020、2023、2025 |

`ready` 仅表示解析器未发现缺口，不等于人工审校。重复题只返回一张卡片，`origins` 保留各个文件、压缩包成员、年份和原题号；底层来源记录不删除。累计下载数包含压缩包和成员，不能当成独立试卷数，也不表示原件仍占用本地空间。最新覆盖与逐图 HTTP 检查见 `data/evaluation/question_bank/word-runtime.json`；`sources.json`、`images.json`、`ui.json` 及较早的扫描报告各自记录当时的覆盖。

已检查两份 PDF 原页及一份 DOCX 提取：文件名为“2022年秋”的 A 卷实际考试日为 2023-01-03，B 卷为 2023-02-25，均记录为 2023 年；2025 期中材料带有“数据记不清”等说明，已标为回忆版。其第一大题中有解题批注，题答未分离，暂不进入检索和图片预览。其余不确定条件保留缺失提示。未确认信息保持为空。

PDF 提供实际页序和题目区域图；跨页题保留各页。DOCX 保留真实 XML 段落序号，并用独立的转换页表关联截图；旧式 `.doc` 也通过本地 Word 转换。TXT/MD 使用行号，直接 JPG 和 PPT 仍不解析正文。扫描 PDF 可选择本地 Windows OCR，未审校文字不进入题目检索。2020 期中卷仍定位文件实际页序 1—3，`printed_label` 另记印刷页号。ZIP/RAR/7z 保留父文件和成员路径，校验越界路径、链接、成员数量与总大小后限量解压；RAR/7z 使用本机 7-Zip，不执行附件。

## 题目与原图对应

题目保存 `images` 和可选 `image_regions`，后者记录图片序号、文件页序、PDF 裁剪坐标及区域对应的卷面页码。新增 PDF 提取会记录坐标；旧记录仅在能从原页或文件名确认时补充页码，不编造裁剪坐标。2020 卷的四张已审校题图补齐了左右栏坐标和卷面页码：例如题四为文件第 2 页、卷面第 4 页；查看完整原页时仍标明该页含卷面第 3、4 页。

`get/search` 返回 `image_previews` 与 `source_pages`。题目卡片显示原图、图片序号、原题号及位置，隐藏 OCR/提取文字；点击可放大、切换原始尺寸或查看完整原页。跨页图按顺序展示；Word 转换页明确标注，原段落定位不冒充 PDF 页码。历史回复重新读取当前图片和出处。原页和原文件可能含其他题或答案，默认卡片只显示已登记的题目区域。

`/api/question-image?id=题目ID&index=图片序号` 和 `/api/question-page?id=题目ID&page=文件页序` 都重新读取当前题目。原页仅能访问当前题目涉及的页，图片必须位于题库 `processed/` 内；失效来源、旧 ID 和越界路径不继续提供图片。`scripts/verify_question_bank_images.py` 实测当前 93 条记录、74 张图片均可读取并定位来源页，原页 HTTP 内容与保存图片一致。这项检查不等于所有裁剪内容已经人工审校；浏览器视觉验收仍待完成。

## 安装与命令

项目根目录执行，使用现有本地 BGE 模型目录：

```powershell
uv pip install --python .venv\Scripts\python.exe -e '.[question-bank]'
.venv\Scripts\python.exe -m aitown.question_bank discover
.venv\Scripts\python.exe -m aitown.question_bank prepare --max-files 3
.venv\Scripts\python.exe -m aitown.question_bank status
.venv\Scripts\python.exe -m aitown.question_bank index
.venv\Scripts\python.exe -m aitown.question_bank search --query "等价关系" --limit 3
.venv\Scripts\python.exe -m aitown.question_bank get --ids 题目ID
```

最后一条中的 `题目ID` 应替换为当前搜索返回的整数；重解析后旧 ID 失效。可按 `--year 2023 --exam final --question-type 证明` 过滤，明确条件不会因相似度放宽。默认最多三题，上限十题。选择 `codex_chatgpt` 后普通查询也触发第二轮筛题；`search --reference-ids 当前选定ID` 额外指定参考题，参考题自动从候选排除。

维护命令：

```powershell
# 下载和重处理指定来源；状态中的 source ID 不是题目 ID。
.venv\Scripts\python.exe -m aitown.question_bank prepare --source-id 773
.venv\Scripts\python.exe -m aitown.question_bank resume --job-id 任务ID
# 显式刷新完整远端目录、处理新增/修改并标记已移除来源。
.venv\Scripts\python.exe -m aitown.question_bank prepare --update
# 仅重新解析本地已下载的支持文件，不联网下载。
.venv\Scripts\python.exe -m aitown.question_bank reparse --source-id 773
# 本地补 Word 图、解压尚未展开的压缩包；保留已有题目 ID、答案配对和审校 PDF。
.venv\Scripts\python.exe -m aitown.question_bank complete-previews
.venv\Scripts\python.exe -m aitown.question_bank index
```

`--root` 与 `--extractor` 放在子命令前。`review --source-id ID --file 修正.json` 接受 `ParsedSource` JSON 修正边界、文字和标签；再次重解析会覆盖该修正。当前不保留自动合并人工修订，操作前应保存修正文件。准备逐文件事务提交，中断后复用已完成文件；同课程采用单独 worker。后台 worker 完成后刷新 BGE 索引，显式 CLI `prepare/reparse/review` 后需手动执行 `index`。索引未刷新时明确回退字符关键词，旧题目不会继续作为当前结果返回。

`--ocr` 与 `--ocr-columns` 同样放在子命令前，见下方扫描件用法。`pair-answers` 只写答案关联，不改变题目或向量，不需要重建索引。

目录由 `QUESTION_BANK_DIR` 配置，默认 `data/question_bank/`：`question_bank.sqlite3`、`raw/`、`processed/`、`indexes/`、`jobs/`。此目录默认忽略提交，第三方原卷与答案不随代码公开。题库模型参数不能指定个人库路径或 SQL。

## MinerU 本地提取

已在 `.venv-mineru` 安装并实测 `mineru==4.0.10`，避免改变主项目依赖。使用 [MinerU SDK](https://opendatalab.github.io/MinerU/reference/output_files/) 保存结构化内容和原始产物：

```powershell
uv venv .venv-mineru --python 3.13
uv pip install --python .venv-mineru\Scripts\python.exe 'mineru>=4,<5'
.venv-mineru\Scripts\python.exe scripts/mineru_extract.py "原文件.pdf" "输出目录" --tier flash
```

配置 `MINERU_PYTHON=.venv-mineru/Scripts/python.exe`、`MINERU_TIER=flash`，再运行 `python -m aitown.question_bank --extractor mineru prepare --source-id 来源ID`，可在准备任务中使用 MinerU。`QUESTION_EXTRACTOR=mineru` 设置默认提取器；当前仍默认 `basic`。

本地 flash 实测两页 A 卷约 13 秒，能拆出 8 大题，但仍有特殊字体缺字、公式转为图片和文字顺序错误。因此返回 `partial` 并要求核对原图。flash 是原生提取，不能代替扫描件 OCR。standard 等档位的本地 VLM/OCR 模型尚未下载和验证；不会将其称为已支持。没有接入 MinerU 云端 API；若以后选择云端且需要密钥，再向用户反馈。

## 扫描件 OCR 与原图审校

新增可选 [Windows.Media.Ocr](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine) 桥接，复用本机已安装的 `zh-Hans-CN` 语言包，不下载模型或上传资料。默认 `QUESTION_PDF_OCR=none`；显式 `--ocr windows` 仅识别原生文字不足的 PDF 页。未安装语言包、超时或识别失败时保留原页和失败说明，来源维持 `partial`。

2026-10-08 实测三份真实扫描 PDF（来源 785、786、879），共 14 张文件页，分别耗时 3.79、4.41、1.68 秒，识别文字约 4,946 字符。数学符号、指数和题号存在明显错误，不能作为完整题干。原始识别行及坐标保存在 `ocr/response.json`；页面文字供审校，程序不把未经审校的数学条件加入搜索。没有把 OCR 运行完成等同于数学解析完成。

Codex 对照 2020 期中卷的全部三张扫描图，转录并核对原题号二、四、五、六的条件与裁剪图，新增 4 道可检索题；第一、三大题仍有不清晰记号，保留待办。审校稿保存在 `data/evaluation/question_bank/scan-2020-review.json`，审校者不是用户；4 条定向 BGE 查询命中目标题，记录在 `scan-2020-check.json`，不能当作全量召回率。

以下来源 ID 是本机示例。重解析会覆盖当前审校稿，需要随后重新导入已保存的修正文件：

```powershell
.venv\Scripts\python.exe -m aitown.question_bank --ocr windows --ocr-columns 2 reparse --source-id 879
.venv\Scripts\python.exe -m aitown.question_bank review --source-id 879 --file data/evaluation/question_bank/scan-2020-review.json
.venv\Scripts\python.exe -m aitown.question_bank index
```

`--ocr-columns 2` 仅用于已核对的左右两页扫描；普通单页保持 1。`scripts/verify_question_bank_ocr.py` 将测量产物写到 `ocr_checks/`，不覆盖已入库的审校稿。MinerU 提取器与此 OCR 路径分别使用，不叠加执行。

## PaddleOCR 小样本对照

在独立 `.venv-paddle` 环境安装并实测 PaddleOCR **3.7.0**、PaddlePaddle **3.3.1**、PaddleX **3.7.2**。采用 [PP-StructureV3](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PP-StructureV3.html) 和 [PP-FormulaNet_plus-M](https://www.paddleocr.ai/main/en/version3.x/module_usage/formula_recognition.html)，CPU、4 线程，关闭页面旋转、展平、表格和印章等本次不需要的模块。未修改主项目依赖，也没有将实验输出自动导入题库；PaddleOCR-VL 和 GPU 推理尚未验证。

对照输入来自来源 879 的文件第 1、2 页（各分左右栏）以及来源 785、786 的第 1 页，共 **4 张文件页、6 张区域图**。Windows 与 PaddleOCR 使用同一组 2.5 倍渲染图片，结果另存：

| 项目 | 实测 |
|---|---|
| Windows OCR，6 张区域合计 | 3.54 秒 |
| Paddle 首次初始化及官方模型下载 | 287.27 秒 |
| Paddle 6 张区域推理合计 | 297.54 秒 |
| Paddle 单张区域 | 25.33—102.31 秒 |
| 两道已审校题的 6 个条件保留检查 | Windows 1/6，Paddle 6/6 |

这 6 个检查只覆盖集合大小 8、恰有 3 个等价类、函数空间 B^A、逐点不等式、全称量词与定义域、最大元充要条件，不能换算成字符准确率或全卷准确率。Paddle 在两道清晰印刷题上改善明显；其余页仍有基数记号误变根号、上下标和符号错误。清晰填空页也把 `(A,≤)` 的 A 识成 1，混合手写答案和批注的页面仍存在大量错误，不能自动分离题答。当前结论是适合生成待审校稿，尚不能批量发布为完整数学题。

复现命令：

```powershell
uv venv .venv-paddle --python 3.11
uv pip install --python .venv-paddle/Scripts/python.exe 'paddlepaddle>=3.2,<4' 'paddleocr[doc-parser]>=3,<4' pymupdf
.venv/Scripts/python.exe scripts/verify_question_bank_paddle.py --run
.venv/Scripts/python.exe scripts/assess_question_bank_paddle.py
```

脚本依赖本机示例来源 ID。运行数据在 `data/evaluation/question_bank/paddle/`，模型缓存位于 `data/models/paddle/`，均按运行产物管理。`windows.json`、`output/paddle.json`、结构化区域 JSON/Markdown 和 `assessment.json` 保留实际输入、输出、时间及缺口；只运行准备脚本而不加 `--run` 可重新生成 Windows 对照和输入清单。

## 图像参与相似题判断的实测

`scripts/verify_question_bank_vision.py` 通过真实 ChatGPT 登录调用，用 2020 卷函数偏序题作为参考、2025 回忆卷中同条件题作为相关候选、两道计数题作干扰。遮掉参考和候选的全部题干、标签及题型后，纯文字调用返回空结果；附上 4 张原题图后选中相关候选，明确识别出 B^A、逐点比较、证明偏序关系和最大元充要条件。图像调用耗时 **12.60 秒**。这是一个因果对照样本，不代表整体相关性准确率。

随后运行真实 BGE/关键词/精排 → ChatGPT 图像判断 → 当前题干与原图读取。首次原图复查发现来源 781《期中考.pdf》实际上是两页题目与手写解答混排笔记；其中一条记录还将第四、五、六题混在一起。之前的“答案字段隐藏”检查无法发现截图中的解答，因此保留失败记录 `vision-initial.json`，通过现有 `review` 流程移出本来源的 3 条未分离记录，保留原件和原页并重建 93 条索引。修正后实际检索只返回已核对的相关题图，未再返回该笔记；最新图像检查见 `vision.json`。

```powershell
# 整组真实账号实验；仅使用公开课程题目和题图。
.venv/Scripts/python.exe scripts/verify_question_bank_vision.py
# 来源781的审校文件可在重解析后重新导入；随后重建索引。
.venv/Scripts/python.exe -m aitown.question_bank review --source-id 781 --file data/evaluation/question_bank/annotated-notes-review.json
.venv/Scripts/python.exe -m aitown.question_bank index
```

这次修正依赖 Codex 逐页审校，没有实现通用手写题答分离。重解析会覆盖审校结论，需要重新导入保存的修正文件。其他资料仍需继续抽查原图、题目边界和题答混排情况。

## 已有答案的核对与配对

2023-02-25 B 卷已核对独立 DOCX 答案文件（来源 774）与配套答案 PDF 三页（来源 885），依据课程、考试日期、B 卷、题号和相同题干配对。原卷在目录与 ZIP 内各收录一次（来源 775、886），7 个原题号对应 14 条题目记录；不按 14 道独立题报告。

其中一、三、五、八的来源答案有简略提示或缺少论证步骤，返回 `status=partial` 及逐题 `missing`；“书上作业”按原文提示展示，不冒称完整解答。六因题干旧式嵌入公式缺失暂不配对。配对证明出处对应，不评价原答案正确率。

新增维护命令：

```powershell
.venv\Scripts\python.exe -m aitown.question_bank pair-answers --question-source-id 775 --answer-source-id 774 --file data/evaluation/question_bank/b2023-pairs-775.json
```

文件是 `AnswerPair` 数组，字段为 `question_number`、当前已核对的 `question_text`、答案的实际 `locations`、配对依据 `basis` 和可选 `missing`。答案正文直接读取来源位置，不接受模型生成的解答。命令在同一事务中核对课程、已有考试信息、当前题干与位置；任一配对失败则整组回滚。重解析原卷或答案文件后相关配对失效，需要重新核对并配对；已移除或失败来源不会继续提供答案。

`scripts/verify_question_bank_answers.py` 可复现这份 B 卷样本的核对文本比较、配对及主 Agent 追问；它依赖本机来源 ID。真实记录见 `answers.json`。默认 `get/search` 不含答案正文，只有 `include_answer=true` 才返回答案、原文件、段落序号、配对依据和缺失说明。

## Skill、会话与界面

- `question_search`：独立课程、查询及可选严格条件；返回顶层 `question_ids/results/count/status/coverage/method`，第二轮另含 `model_calls` 与失败提示。
- `question_get`：批量题目 ID；`include_answer=false` 为默认。只有显式请求才返回答案表中已确认配对的来源答案，不生成解答。B 卷已有 7 个原题号的样本配对，其余资料仍有缺口。
- `course_status`：覆盖、准备任务、阶段、已完成文件数及错误。三个技能只读，支持注册、启停、参数/结果校验和 search → get 顶层字段绑定。

独立执行不需要个人应用库：

```powershell
.venv\Scripts\python.exe -m aitown.skills run course_status '{}'
```

聊天示例：“找集合与图论有关等价关系的题，先别给答案” → “把第二题完整给我” → “再找两道同类型题”。顺序绑定只使用本会话实际结果，来源更新后要求重新找题；多候选尚未选定时会先澄清。概念解释不会仅因出现课程名而强制找题。

现有 UI 增加题目卡片、原图、外部出处，以及“资料与设置”中的准备、更新和状态。首次查找未准备课程或明确请求准备时，外层调度器启动独立进程，立即返回任务 ID；只读技能不下载。后台准备期间可以查询已入库部分。此前后台仅配置集合与图论；2026-10-08 已扩为公开目录中的课程自助准备，详见文末。

2026-10-08 根据实际读题反馈，用户界面改为通过原题截图和来源文件展示题目，保留年份、题号与页码。自动提取的题干不再出现在题目卡片、聊天正文、来源展开或技能返回数据中；已有会话重新读取时同样处理。OCR/PDF 提取文字仍保留在独立题库及内部工具结果中，用于检索、相似题筛选和审校，不删除、不重新入库。没有截图时只提供原文件入口；图片加载失败时明确提示打开原文件，内嵌附图不冒充完整题干。用户明确索要已有来源答案的行为保持原样。

本次受影响的题库与界面回归 **31 项通过**，真实 HTTP / Agent / worker **17 项检查通过**，包括界面隐藏识别文本、原图和出处保留、按序追问及答案按请求返回。此前全量 197 项是历史运行记录，本次未重跑全量。

## 验证记录与剩余工作

2026-10-08 新增题库模型设置与在线解析入口，使用方式和本轮验证见下节。下文的 197 项全量测试、16 项 HTTP 检查等均保留为此前图像工程的历史记录。

实测报告位于 `data/evaluation/question_bank/`。`two_rounds.json` 记录首批真实 BGE 与 ChatGPT 账号调用；`extraction.json` 记录三份真实文件和 MinerU 对比；`ocr.json`、`scan-2020-check.json` 与 `answers.json` 记录本轮扫描审校和答案样本。最新 `ui.json` 记录真实 HTTP、题目卡片数据、原图接口、按序绑定、答案默认隐藏/显式返回/位置/缺口、个人事实未写入，以及独立 worker / 自动索引 / 界面响应共 **16 项检查通过**。它们是本机运行产物。

当前题库复测中，三个 BGE 查询约 0.75—7.52 秒；第二轮账号调用约 131.09 秒，返回一道合适的同类型候选，CLI 报告输入 13,710 tokens、输出 105 tokens。该记录包括 CLI 开销，不能等同于窄题目提示的 token 数，也不能据此换算 API 费用。模型可以少于请求数量或返回无匹配，不凑题。

必要回归覆盖来源替换/移除、严格过滤、答案默认隐藏、题答同页裁剪、图像路径限制、技能禁用、错误 ID、会话隔离和真实子进程中断恢复；新增跨页图片位置、区域卷面页码、原页归属、图像与题目 ID 对应、答案不进入提示和整题图片预算检查。本轮全量 **197 项测试通过**；真实 HTTP / Agent / worker 保持 16 项检查通过。图像与原页接口另见 `images.json`，真实图像判断另见 `vision.json`。

尚未完成：扫描资料的其余题目和全部数学符号/图表逐题审校、直接图片/PPT 正文解析，其他答案与混合题答资料的可靠配对/分离，约 30 条带人工相关性标注的检索对比、精确率/召回率及精排参数评测。旧式 Word 转换和 RAR 解压已补齐，实测范围见末节。当前 BGE 阈值 0.50 仍是初始候选阈值，尚未经课程检索标注集调参。

此前浏览器视觉复查连续报 `trusted Node process exited unexpectedly`。随后已恢复当前会话的普通沙箱执行、Node 连续执行和 Windows 窗口枚举；浏览器专用通道仍有独立启动错误，原生窗口截图又因工具无法可靠确认当前浏览器 URL 而被主动停止。尚未取得浏览器视觉验收结果，不能由 HTTP 检查推断布局已通过；视觉验收和 T13 报告继续待办。

## 题库模型设置、上下文纠正与 MinerU API

在“资料与设置 → 题库模型”选择找题方式：

| 方式 | 实际行为 | 所需配置 |
|---|---|---|
| 本地筛选 | 课程、年份、题型等过滤，再按题干/知识点检索；已有 BGE 配置可继续使用 | 无 API Key |
| 模型 API | 模型结合上一轮找题条件整理本轮意图；本地严格过滤，再由模型筛选最多 40 道候选 | 服务商、接口地址、完整型号、API Key |
| ChatGPT 登录 | 首轮本地检索，第二轮同类型题由已登录 Codex CLI 判断 | Codex 登录；下拉选择 CLI 返回的型号 |

用户之前截图中的首轮没有调用 ChatGPT；预览配置为本地筛选，字符匹配也不足以证明数学条件相关。“四角同色”原题确实是证明题，但“我说的是证明题”未接入课程流程，是上下文路由问题。现在纠正题型、换知识点、再找和取消年份条件会保留当前课程与未修改条件；无结果后也能继续放宽条件。无知识点的“找集合与图论的证明题”按题型列题，标注“符合筛选条件”，不再靠“的”等通用字符假装相似。切到个人话题后结束课程上下文。

界面参考 [llm-for-zotero 的配置流程](https://github.com/yilewang/llm-for-zotero#configuration)：选择服务商、Base URL、Key、型号并测试连接。本项目当前支持 OpenAI Chat Completions 兼容协议，包括 [DeepSeek 官方兼容接口](https://api-docs.deepseek.com/)。DeepSeek 模板地址为 `https://api.deepseek.com`；填写 Key 后点击“读取可用型号”，从服务商 `/models` 返回的下拉列表选择，不需要手写型号。切换服务商或地址会清空旧目录；空目录和不支持列表接口会明确报错。还可设置 temperature、输出 Token 上限、JSON 格式和是否附原题图。尚未接入 Anthropic/Gemini 原生协议；须使用提供该兼容协议的端点。服务商目录可能包含不支持当前聊天/JSON/图片任务的型号，需使用连接测试核对。连接测试仅发送合成 JSON 请求，可能计费，不等同于题库质量验收。

ChatGPT 登录方式也改为型号下拉框。进入该方式时自动调用 [Codex App Server 的 `model/list`](https://learn.chatgpt.com/docs/app-server)，显示真实型号 ID、名称及默认推荐项；“刷新可用型号”可重新读取。只读取已登录账户类型和型号目录，不创建推理会话、不读取认证文件、不向页面返回账户信息或令牌。目录读取失败保留已有选择；之前保存但当前目录未返回的型号会单独标注，避免静默替换。选择后点击“保存模型与解析设置”生效。

2026-10-08 本机 Codex CLI 0.154.0 实际返回 `gpt-6-astra`（默认）、`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`。此为当时 CLI 返回的目录，后续随账户与 CLI 更新；没有逐型号执行推理，不据此宣称额度或调用权限已验证。

API 模式覆盖首轮与后续筛题，不要求下载本地 embedding/精排或生成模型。候选只有课程题干、知识点、题型与可选原题图，不含已有答案、微信记忆、风格样本或完整个人聊天历史。图片默认关闭，仅在所填型号支持图片且用户勾选时发送；每次最多 12 张、原图数据合计 8 MiB，不拆分一题的连续图片。候选超出 40 道时报告核对范围，建议补充知识点。模型只能返回当前候选 ID；无匹配允许返回空数组。接口失败会明确标注本地回退，页面显示实际筛题模型，不冒充生成了解答。

个人资料的回答模式仍只允许本机服务，独立于题库 API。Key/Token 使用密码框；保存和读取接口不回传明文，也不写浏览器存储。勾选“用当前 Windows 账户加密保存”后，凭据经 DPAPI 加密后写入本机 UI 元数据，只有当前 Windows 账户可解密；未勾选时重启需重填。切换 API 服务商或地址不会将旧 Key 自动带给新端点。环境变量的题库凭据也与个人模型的 `LLM_API_KEY` 分开。

“PDF 解析”新增 MinerU 在线 API，使用独立 Token，可选 `vlm` 或 `pipeline`。按 [MinerU 官方 v4 文档](https://mineru.net/apiManage/docs) 申请上传地址、上传、轮询并读取结果 ZIP；无须安装 MinerU/Paddle 本地模型。用户于2026-10-08进一步明确授权 `victkk/FDU-Sharing/public/resources/` 下全部公开课程 PDF，包括公开 ZIP/RAR/7z 内的 PDF；不上传个人文档、微信资料或本地批注。上传前重新下载公开副本；压缩成员从新下载的公开包中限量提取，最多三层嵌套，不采用本地已解压或已批注文件。Word 导出 PDF 仍仅在本机处理。Token 只交给 MinerU API，上传/下载签名地址不附 Token。结果 ZIP 仅限量读取 JSON，不解压任意路径；不能关联页码/区域时拒绝入库。数学公式与分题结果仍标为待核对，用户端继续看原图。

先保存 Token，在“小样本原卷”选一份，点击“试解析一份 · 不替换题库”。样本产物独立写入 `question_bank/experiments/mineru-api/<运行目录>/`，仅报告页数、题数、原图数量和缺口，不替换现有记录。保存解析方式后，下一次准备/更新使用所选解析器；更新会按原有规则重新解析并使旧题目 ID、人工审校及答案配对失效，应先核对样本再操作。

本轮新增真实本地 HTTP 配置 → 模拟模型 API → 首轮找题 → 历史显示流程，及凭据加密重启、API 无效候选回退、个人资料/答案隔离、MinerU 公开副本上传/Token 隔离/坐标转换检查。真实供应商鉴权与匹配质量、MinerU 公式识别效果尚未测量：尚无用户 Key/Token，不把模拟服务结果当作线上验证。浏览器布局视觉验收也仍待办。

当前全量 **249 项通过**（`pytest -q`），此前真实 HTTP / Agent / 独立准备 worker **17 项通过**（`scripts/verify_question_bank_ui.py`）记录保留。模型设置的真实 JS 事件与 DOM fixture 扩展为 **7 项通过**（`node scripts/verify_question_model_ui.js`）：API 下拉选择与 Key 保留、保存具体型号并清空 Key、切换服务商清空目录、Codex 自动加载默认项并保存其他型号、读取失败保留选择/可重试、缺失旧型号显式标注，以及失败后保留未保存 Key。不是浏览器视觉验收。

重启预览后，`scripts/verify_question_model_catalog_runtime.py` 完成 **6 项真实 HTTP 检查**：服务健康、两处型号控件均为 select、更新脚本可访问、Codex 目录实际返回、目录不含凭据/账户详情、读取前后设置未变。产物为 `model-catalog.json`；无推理调用，不替代真实模型质量及浏览器布局验收。

重启后的本地预览和真实 93 条题库另完成 **8 项检查**（`scripts/verify_question_model_runtime.py`）：配置接口与脚本可访问、凭据不回传、无知识点的证明题按条件列出、题型追问和换等价关系主题均接续找题。报告为 `model-settings.json`；会话存储独立，未调用远端模型。该复测证明本地流程恢复，不代表相关性评测完成。

复测首次失败暴露了完整新课程查询误继承旧知识点，现已修正；原始失败记录保留为 `model-settings-initial-failed.json`。同时发现新增 HTTP 测试用了默认题库路径，创建一条 `synthetic/test/test.txt` 合成来源；已事务删除该合成来源及其题目，按既有验证记录恢复目录检查时间，公开题库恢复93条，没有删除原始来源，也没有重置单调 ID。测试现使用独立临时题库，最新全量249项前后逐表比较课程、来源、题目、原页、答案和 ID 序列均一致。修复与隔离验证记录分别为 `test-isolation-repair.json`、`test-isolation-after.json`；个人资料未受影响。

## 用 @ 固定当前会话的题库

输入框上方新增“@ 题库”。点击后选择“@集合与图论”，或在输入框键入 `@` 后选择该项，即刻固定当前会话。也可以直接发送 `@集合与图论 排列组合的有哪些`；单独发送 `@集合与图论` 会确认固定并提示继续输入。选择菜单支持 Enter 选中、Escape 关闭和方向下键聚焦；中文输入法组合输入时不拦截确认键。

固定后输入框上方显示课程标签，后续可直接发送“排列组合的有哪些”“等价关系的有哪些”等消息，无须重复课程名或“找题”。后端按本轮保存的课程调用独立题库检索，避免回到普通聊天/个人知识库。原题截图、来源文件和答案的展示规则保持一致。点击课程标签的 × 取消固定，恢复原有自动路由。固定状态随当前会话保存、重启后恢复；其他会话和新会话独立。

固定题库时不加载个人风格上下文，不将个人资料查询或明确自述交给题库模型，也不自动保存为个人事实；此类消息提示先取消固定。在线服务的数据范围仍只包含课程题库候选和找题消息。UI 元数据新增会话课程字段；应用运行记录的 schema 由3升至4，仅新增当轮课程字段，断点恢复按当轮课程执行，即使后来取消了会话固定也不会改查其他资料。历史库迁移与无检查点恢复有实际回归覆盖。

真实复测首次发现：路由已正确，但“排列组合的有哪些”仍为空；当时题干含“排列”，口语问法稀释了字符匹配分数。现去除“有哪些”“有什么”“看看”等检索噪声，不降低匹配阈值、不重写题库。首次失败报告保留为 `question-scope-initial-failed.json`。最新 `scripts/verify_question_scope_runtime.py` 使用现有93条公开题库和独立合成会话完成 **10项 HTTP / Agent 检查**，该问法返回记录143、213、203，继续换等价关系返回130、210、199；新会话与取消固定均不强制走课程题库，各题库表前后不变。报告为 `question-scope.json`。

`node scripts/verify_question_scope_ui.js` 的真实 JS 事件与 DOM fixture **6项通过**，覆盖 @ 菜单选中、保留剩余草稿、Escape/中文输入、切换会话恢复、取消和失败保留输入；型号设置原有7项事件检查仍通过。全量249项通过，包含 API 固定找题不带个人历史和断点恢复。上述检查验证路由与交互逻辑；题库仍是部分覆盖，相关性质量和浏览器视觉布局验收继续待办。

## Word 原排版截图、压缩包与同题合并（2026-10-08）

本机用 Microsoft Word 只读打开 `.doc/.docx` 并执行 [ExportAsFixedFormat](https://learn.microsoft.com/en-us/office/vba/api/word.document.exportasfixedformat)，保留原卷字体、公式、附图和分页。打开前禁用宏、打开/打印时的链接与字段更新，不加入最近文件；关闭时不保存原件。转换在独立空 Word 进程内运行，超时清理仅针对已记录 PID 和创建时间的本轮进程，不终止用户已有 Word 会话；未改 ACL、所有者或持久系统执行策略。禁用宏使用 [AutomationSecurity](https://learn.microsoft.com/en-us/office/vba/api/word.application.automationsecurity)。本机有 Word 和 7-Zip；Word 转换无需下载 OCR/LLM 模型，不发送 MinerU API，也不扩大原有第三方上传授权。

DOCX 题干与答案继续使用原段落定位；完整题号和中文题干能与导出 PDF 对齐后，补入按页排列的裁剪图及独立原页。示例：B 卷原题七保留段落 239 的原出处，截图实际是转换 PDF 第 6 页。裁剪不把装订线、页脚作为题干边界，省略空白作答区域，保留公式、表格与图形；无法确认边界时不按题号猜截图。合并卡片优先显示补齐的 Word 裁剪图，引用 ID 和各个出处保留。已有 93 条记录的 ID、题干、位置、14 条答案配对和 2020 审校裁剪保留，操作前有普通 SQLite 备份。

同题比较保留文字大小写、空格、数学上下标位置与矢量图形；跨 Word/PDF 用已定位的导出文字和排版对应。含栅格图的同题还核对原件字节或实际解码像素，不依靠文字相似度合并。`search/get` 每组只返回一题，`occurrence_ids/origins` 保留所有有效出处；排除一道题时也排除其重复来源。相同来源答案只显示一次，保留多个答案出处。暂不自动合并排版或表述不同而仅在数学意义上相似的题。

ZIP 的 13 个成员和 RAR 的 15 个成员均已实际解压。`/api/question-source?id=题目ID&source=来源ID` 直接提供当前题目或已配对答案的本地原文件，包括解压成员；校验题目归属、来源状态和 `raw/` 目录范围，界面另保留仓库入口。RAR/7z 先校验整个目录，再逐成员限量读取字节，拒绝路径越界、链接、加密成员、大小写路径冲突与超额解压；新成员分别记录处理结果，PPT/JPG 不冒称正文已入库。

实测发现《期中考.doc》《课堂练习2.docx》和 ZIP 内《2007集合与图论期中考试.doc》含已填答案、红字解答或题中分析。3 份来源标为 `needs_review`，其 16 条原始记录保留但不进入检索和截图展示。首次结果与缺图记录保留在 `word-previews-initial-20261008.json`；修正、原件/ID/答案保留检查见 `word-previews-20261008.json`。这批混排资料还需逐题审校、分离题答。

最终可用的 Word 来源记录 **32/32 有截图**；全库 **98 条可用出处合并成 58 道题、66 张题图**，独立 BGE 索引重建为 58 题。`scripts/verify_question_word_runtime.py` 实际检查 **66 张截图、66 次原页、98 次原文件 HTTP 内容**，均与已登记文件一致，并确认去重、Word 页码、答案默认隐藏及重复答案出处；另与操作前备份核对原有93条题目及14条答案关联，均保留。报告 `word-runtime.json`。全量 **268 项测试通过**，测试前后课程、来源、题目、原页、Word 转换页、答案及 ID 序列表一致，报告 `word-test-isolation.json`。真实 HTTP / Agent / 独立 worker 的17项检查、型号设置7项与@交互6项JS检查也通过。预览服务已重启，本机8513端口健康、去重题数及新裁剪图读取复测通过。

视觉检查渲染了 8 份不同 Word 原件的 **32 页**，另核对公式和图形题裁剪。该检查不替代所有数学条件逐题审校，也不等同于浏览器布局验收。维护命令 `complete-previews` 仅补本地 Word 与尚未解压的归档，不重解析已有审校 PDF；完成后运行 `index`。普通 `reparse/prepare --update` 仍会替换来源的题目 ID 和配对，应按既有说明使用。

## 完成后清理原件与全流程复测（2026-10-08）

本节保留历史清理策略及实测。2026-10-09 本地持久化版本已改为默认保留原件，仅显式 `cleanup` 清理；以下自动清理描述不代表当前默认行为。

按用户要求，`prepare/worker`、`reparse`、`review` 和 `complete-previews` 完成后自动清理已处理原件、压缩包及解压成员。清理保留题库记录、全部出处、已配对答案、原页和题图，以及 Word 转换 PDF；PDF/Word 的原页数量和图片可读性检查通过后才删除对应缓存。失败、待审校、不支持或缺少图片的来源保留并报告，不把这些状态当成处理完成。仅逐文件删除 `raw/` 内已登记的普通文件，并清理空目录；拒绝越界路径、链接和正在准备的课程，不递归删除整个目录。

删除前把已确认的重复题关系保存到有外键级联删除的普通关联表；后续来源重处理替换题目时，旧关联随旧 ID 删除。已有版面、原图像素比较仍用于确认同题，精确裁剪像素也支持清理后的再次导入；不会仅凭相近文字合并数学条件不同的题。重复判断增加对多个同文图形候选的比较，避免第一个图形不同就漏掉后面的真正副本。

当前已清理 **25 份、23,699,667 字节（22.6 MiB）**，题库总占用从 **84.7 MiB 降为 62.1 MiB**，`raw/` 剩余 **8.6 MiB**。暂保留 **19 份**：3 份题答混排 Word 和16份尚不支持的 JPG/PPT。58道可检索题、98条可用来源记录、66张卡片题图及14条原答案关联均保持不变。`coverage.downloaded=44` 表示累计下载，`raw_retained=19`、`raw_deleted=25` 表示当前缓存状态；清理记录见 `data/evaluation/question_bank/raw-cleanup.json`。

界面的 Word 来源入口改为读取保留的转换 PDF；已清理 PDF 的入口指向仓库在线原文件，压缩成员指向其来源压缩包并保留成员路径。原题截图和原页仍可离线查看，清理后的原始文件须联网重新获取。准备 worker 可自动恢复被清理的来源或压缩成员，恢复单个成员不会重置兄弟来源的题目或状态；显式执行 `complete-previews --source-id ID` 可恢复 Word 原件并补图，继续保留题目 ID 和答案关联，成功后再次清理。默认 `complete-previews` 不重复下载已经补完的资料。

```powershell
.venv\Scripts\python.exe -m aitown.question_bank cleanup
.venv\Scripts\python.exe scripts/verify_question_cleanup.py
.venv\Scripts\python.exe scripts/verify_question_word_runtime.py
.venv\Scripts\python.exe scripts/verify_question_bank_ui.py
.venv\Scripts\python.exe scripts/verify_question_scope_runtime.py
```

复测使用真实公开题库和独立合成个人会话。HTTP/Agent/worker 的17项检查和@固定题库的10项检查通过；逐图接口检查66张题图、66次原页访问和32次转换PDF访问，另核对66个在线来源入口的状态。重新调用当前 ChatGPT 账号，发送1道参考题、5道候选及其9张登记原图，约21秒返回预期题目132，未发生回退；只发送题库内容与本轮找题消息。全量 **276项测试通过**，真实题库各表（含去重关联）前后不变，记录见 `word-test-isolation.json`。

目前可运行的是下载/解压、整理截图、去重入库、建立索引、找题、多轮选题/换知识点、读取已确认的来源答案和带图相似题判断。并非所有资料都已解析，也未完成检索相关性全量评测和浏览器布局验收。自动生成解题讲解尚未作为已验收流程；DeepSeek 等 API 和 MinerU 在线接口仍需有效 Key 后完成真实鉴权与效果测量，不能用模拟接口测试代替。

## 页面自助新增课程（2026-10-08）

本节保留当时的入口和清理行为；当前操作以文首 2026-10-09 本地闭环说明为准。

刷新本机页面，在“资料与设置 → 新增课程资料与题库”输入或选择课程名，再点击“获取并准备课程”。可选全部资料、先试3份或10份；目录显示公开文件数和已整理题目数。当前保存目录有 **54门有效课程**，包括线性代数、数据结构；此前发现的55个条目包含根目录图片 `xh.jpg`，已排除该伪课程。点击“刷新公开课程目录”只更新目录信息，不下载全部课程。未收录的课程仍需提供资料来源，当前入口尚不能接受任意新仓库或私人文件上传。

也可直接发“帮我准备线性代数知识库”“帮我加入线性代数课程”，或先选择 `@线性代数` 再描述要找的题。单独选择课程只固定会话；首次查找尚未准备的课程才启动后台任务。独立 worker 依次下载、解压、解析/Word转PDF、裁剪、入库、索引并清理已处理原件；界面轮询真实阶段和完成份数，后台处理期间可继续聊天、检索已入库部分。已有课程可继续准备剩余文件；“更新课程资料”会重新解析，旧题目引用、审校及答案配对可能失效，界面已提示。

`@课程名` 菜单、聊天路由、在线意图整理和准备任务都使用实际选定课程；切换课程时清除旧课的题目序号绑定和筛选条件。每门课单独建立向量索引，兼容原有索引路径，避免不同课程的后台任务互相覆盖。集合与图论的知识点词典只用于该课程；新课程没有自动套用图论标签，未标注知识点仍可通过原文/语义筛题。个人事实、风格与微信资料不进入课程处理或在线提示。

真实线性代数小样本：独立库下载 `06秋线性代数期末考试A卷.doc`、B卷，共 **2/48份**，在本机Word转换、裁剪和BGE索引，整理 **16道题**，约 **26.98秒**。两份已处理原件均清理，截图及转换PDF保留。“@线性代数 找两道题”“把第一题完整给我”“换成矩阵相关的题”均命中新课程；原图和转换PDF可通过HTTP读取。**19项检查通过**，报告 `course-self-service.json`；不表示48份全量完成、数学条件逐题审校或检索准确率已测量。短年份文件名与卷面学年不作猜测，该样本年份仍标未确认。

第一次流程检查虽然通过，但人工查看截图发现行列式底行被装订线文字截断，另有题号同一行的公式片段误分到上一题。已过滤装订线/页脚边界，并按重叠的题号行调整PDF片段归属；相同两份原件重跑后检查底行、定界线和跨页归属通过，复看两张目标裁图完整。首轮失败证据保留为 `course-self-service-initial-crop-issue.json`，不以图片能读取代替内容完整性。

本轮同时发现主库22:43的一次MinerU更新把10份压缩包PDF标失败，可检索题目曾降到38。逐项比较已有备份的来源、题目/原页/答案记录，以及新下载公开PDF与登记原页的逐像素内容，均一致后只恢复来源状态，保留2020审校与题目ID；清理10份恢复的原件，释放4,629,960字节。主库当前为 **105条可用出处、73道去重题目**，19份待审校/不支持资料继续保留。另按完全相同的题干、出处与实际答案段落恢复14条更新失效的配对，不重写题干。记录为 `mineru-rejected-recovery.json`、`answer-links-recovery.json`。旧更新已改变部分来源的题目ID及解析结果，不能将当前73题与上轮58题直接视为质量提升。

后续复测暴露“排列组合”在长题干中的字符分数过低，按字面展开“排列/组合/错排”候选，不降低所有查询的阈值；第一次失败保存为 `question-scope-after-online-update-failed.json`。公开上传授权已按用户新选择扩到全部课程及压缩成员；非本仓库或未授权来源仍改走本地PDF解析，不上传。扩大范围只改变哪些公开文件可使用服务，不代表MinerU识别结果已经审校。

扩权后用当前已保存Token进行一次真实MinerU `vlm`调用：线性代数 `22 1-4题.pdf`（148,858字节），约 **17.25秒**返回 **2页、4题、5张题图**，状态为partial。样本写入 `experiments/mineru-api/`，没有替换主库。抽查第二题和第四题原图可读，但第二题向量的OCR/LaTeX串丢了分量分隔，第四题数值13被提取成 `1 3`；原图仍是正确读题依据，不能声称公式已经准确识别。报告 `mineru-public-course-runtime.json`。直接公开PDF、其他课程PDF、公开ZIP成员及嵌套ZIP成员的完整上传/轮询/解析协议另有模拟服务回归，确认新下载公开字节与上传字节一致、私有批注不上传、Token仅交给MinerU。

最终全量 **293项测试通过**，测试前后真实题库各表（含去重关联、答案和ID序列）一致。原有真实HTTP/Agent/worker **17项**、课程固定 **10项**均重跑通过；新增课程表单JS事件 **6项**、@事件 **6项**、模型设置事件 **7项**通过。主库状态恢复和答案配对恢复是上述测试前单独记录的有意修复，不冒称整个工作过程未写主库。当前预览 `http://127.0.0.1:8513` 已重启，54门课程目录、73题和新脚本可读取；浏览器布局验收、逐题数学审校及全量相关性评测继续待办。
