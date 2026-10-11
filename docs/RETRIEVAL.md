# T7：本地语义检索与精排

2026-10-05 完成 T7.1—T7.7。本机启用 CPU 本地推理的 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)（512 维）和 [BAAI/bge-reranker-base](https://huggingface.co/BAAI/bge-reranker-base)。向量使用 CLS 表示和 L2 归一化；查询添加模型官方中文检索前缀，文档不添加。精排对问题和片段联合编码，将输出 logit 转为 sigmoid 分数。分数用于排序和阈值过滤，不表示事实正确概率。

模型加载只读取本地 safetensors，禁用远程代码和自动下载。模型文件、FAISS 文件、私有查询及应用库均在 Git 忽略目录。没有把私聊、画像或文档发给外部向量/精排服务。

## 安装和本机配置

```powershell
uv pip install --python .venv\Scripts\python.exe -e '.[dev,privacy,retrieval]' --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
.venv\Scripts\python.exe scripts/download_retrieval_models.py
```

下载脚本只请求 BAAI 官方公开模型文件；需要下载代理时可加 `--proxy http://127.0.0.1:7897`。本机已经下载到 `data/runtime/retrieval_models/`，安装了 torch 2.14.1+cpu、transformers 4.57.6。当前 `.env` 已启用以下字段，LLM 配置保留：

```dotenv
RETRIEVAL_DATABASE=data/runtime/application.sqlite3
RETRIEVAL_INDEX_DIR=data/runtime/retrieval
LOCAL_EMBEDDING_DIR=data/runtime/retrieval_models/bge-small-zh-v1.5
LOCAL_RERANKER_DIR=data/runtime/retrieval_models/bge-reranker-base
SEMANTIC_THRESHOLD=0.35
RERANK_THRESHOLD=0.01
RETRIEVAL_TOP_N=8
RETRIEVAL_TOP_K=4
```

`.env.example` 保持可选空路径，不自动启用个人库。未配置本地模型的检索 CLI 使用 TF-IDF；未配置应用库的聊天继续使用原 TXT/MD 演示。模型编码故障可回退 TF-IDF，并返回警告；精排故障返回 `retrieval_error`，不会冒充已精排的结果。模型初始化失败时聊天报告检索故障，个人回答停止生成。已有在线 `EMBEDDING_MODEL` 仅用于旧文档接口，与本地路径分开。

上方为 T11 实测后本机配置；下方原 T7 测量保留其当时阈值 0.45、Top-N 20，不混写实验条件。T11 的 50 个真实诊断问句最终 Recall@4 为 96%，追加 10 题为 90%；Top-N 8 下 86 次查询 P95 为 0.569 秒，同批 Top-N 20 为 1.323 秒。三条漏召回及精排退化仍保留，详见 `EVALUATION.md`（仅本机研究记录，不随源码发布）。这组参数未作为所有模型/资料的通用默认值。

## 用途和来源

| 用途 | 索引内容 | 查询限制 |
|---|---|---|
| `general` | `knowledge_documents.purpose=knowledge` 的 TXT/MD | 段落切片，超长段落默认 420 字、60 字重叠 |
| `personal` | 有来源事实和 train 分类记忆 | 必须显式主体和关系范围；确认记录、推测记录分别处理 |
| `style` | train 表达样例中的 user 输入 | 必须显式关系范围；场景可过滤，目标回复不参与向量或精排 |

个人记忆按单条事件/主体组织，保留发生时间、记录时间、有效期及应用消息 ID。不会把相邻两个人的经历合并为一条事实。`reviewed_memory_train` 导出文档不进入 general，避免通过导出文件绕过主体、时间和集合限制。个人库不读取 dev/test；推测、未知、无期限或过期计划不作为确认事实。

检索先过滤用途、主体、关系和时间，再在允许记录的向量中取 Top-N。`time_from/time_to` 限制发生时间，边界包含端点；`as_of` 同时限制记录时间和有效期，未知发生时间无法满足时间区间。历史偏好在明确有效期内可返回旧值，当前查询排除被替代记录。主体 `self` 额外核对来源方向及说话人；其他人的自述不能成为本人证据。`conversation` 表示整个事件的摘录，不能据此将其中所有经历归为本人。

T11 为独立事实补上 attribute 类别，使计划过滤同时覆盖独立事实。对明确开放的偏好查询（如“我现在喜欢喝什么”），图指定 `category=preference`，在原 scope/主体/来源/有效期约束内列出当前偏好，避免纠正后因问题没有具体对象而漏掉新值；该路径报告 `sqlite-category`，不伪装成向量命中。其他查询仍走粗检与精排。

精排后过滤低分和重复正文，保留 `coarse`、`ranked`、`hits` 与耗时，去重是独立步骤。风格场景标签来自既有训练标签规则；只用于场景过滤，历史目标仍不参与相似度计算。T6 生成提示继续只使用表达指导，风格经历不会成为新事实。

## 持久化与维护

`retrieval/index.py` 将 FAISS 和 `catalog.sqlite3` 放在索引目录；catalog 记录用途、模型名、维度、应用库路径、普通数据修订号、向量位置与实体来源映射。先建立新 FAISS 文件，再通过文件替换和 SQLite 事务切换活动关联；成功后清理旧代索引。模型或维度不一致时重建，不能混用。TF-IDF 词表与 IDF 通过 JSON 保存，不加载 pickle。

每次查询读取当前应用记录，比较实体正文和元数据。重启只加载已有索引；本地语义模型只编码新增或正文改变的记录，纯来源/场景变化复用向量。数据库维护接口会更新修订号，包括分类记忆。查询返回前再次读取当前来源，移除模型运行期间被删除或纠正的缓存正文，调试结果也不保留它。应用库之外的原始备份和旧导出文件仍分别管理。

```powershell
# 以下维护命令明确指定本地模型；不指定模型时为 TF-IDF。
$embed = 'data/runtime/retrieval_models/bge-small-zh-v1.5'
$rerank = 'data/runtime/retrieval_models/bge-reranker-base'
.venv\Scripts\python.exe -m aitown.retrieval --embedding $embed --reranker $rerank import data/knowledge/demo.md
.venv\Scripts\python.exe -m aitown.retrieval --embedding $embed --reranker $rerank refresh
.venv\Scripts\python.exe -m aitown.retrieval --embedding $embed --reranker $rerank search '活动室最多能坐几个人？'
.venv\Scripts\python.exe -m aitown.retrieval --embedding $embed --reranker $rerank search '我的偏好' --purpose personal --subject self --scope c0067
.venv\Scripts\python.exe -m aitown.retrieval --embedding $embed --reranker $rerank search '历史偏好' --purpose personal --subject self --scope c0067 --as-of 1750000000
# 将 123 换成 import 返回的应用文档 ID；删除后刷新 general。
.venv\Scripts\python.exe -m aitown.retrieval --embedding $embed delete-document 123
.venv\Scripts\aitown.exe --mode demo index
.venv\Scripts\aitown.exe --mode demo ask '我的月球旅行经历是什么' --scope c0067
```

`import` 只接收 UTF-8 TXT/MD；相同名字及正文保持原 ID，更新正文会刷新切片。托管记忆导出必须通过记忆导出接口维护，不能被普通文档导入覆盖。来源删除和事实纠正沿用 `aitown.storage` 的事务接口；下次检索或 `refresh` 更新对应索引。

## 未命中状态与聊天

| 状态 | 含义及聊天行为 |
|---|---|
| `ok` | 找到通过过滤和相关性要求的直接依据 |
| `no_personal_data` | 没有相关直接个人资料，说明不知道，跳过回答生成和自省 |
| `inference_only` | 只有推测或待确认记录，不返回确认事实证据 |
| `ambiguous_scope` | 未明确个人主体或关系范围，要求指定范围 |
| `retrieval_error` | 检索或精排故障，说明本轮无法确认 |
| `no_match` | 通用文档没有相关命中；可回答常识并保留未命中说明 |

聊天用保守规则选择个人/通用资料空间，个人问题不从合成文档填补事实；结构化查询 API 提供完整过滤字段。自然语言时间解析、复杂指代和更完整的意图识别仍由 T9 继续实现。`Reply.retrieval_status` 和 `retrieval_details` 提供状态、排序和来源，`Citation` 包含应用消息 ID、主体、时间和精排分数。

## 实测结果和限制

先运行 15 份合成文档、30 个改写问题及 3 个无关问题，再运行已复核本地资料。硬件为 Windows 11、AMD CPU、32 个逻辑处理器、31.3 GiB 物理内存；本次模型采用 CPU、4 个线程，无 GPU 推理。

| 方法 | Recall@3 | MRR |
|---|---:|---:|
| 字符 TF-IDF | 73.3% | 0.7333 |
| 语义粗检 | 100% | 1.0000 |
| 模型精排，未截分 | 100% | 0.9833 |
| 模型精排，分数至少 0.01 | 96.7% | 0.9500 |

本批语义粗检已将所有正确文档排第一，精排没有进一步改善：保持排版的问题将备份文档排在 PDF 文档之前；会议室被占用的问题因精排分数 0.000755 低于阈值而漏召回。三个无关问题均返回 `no_match`。保留这些退化样例；阈值与排序效果仍需 T11 在独立真实问答中扩大测量，不能据合成资料宣称精排普遍有效。

首次模型加载 4.285 秒；15 个合成片段编码与持久化 0.151 秒。30 次检索的中位耗时 0.091 秒、P95 0.161 秒；平均粗检 0.009 秒、精排 0.088 秒。查询后进程 RSS 约 1.03 GiB，包含 Python、模型和索引，并非模型独占或连续采样的峰值。

首批真实资料编码 58 条个人记录和 200 条训练风格输入，耗时 3.336 秒；8 条基于来源原文的查询全部在最终 Top-4 命中，属于来源联动冒烟验证，不是独立召回基准。不存在的月球经历返回 `no_personal_data`；缺少关系范围返回 `ambiguous_scope`；风格查询正常。另将合成 `demo.md` 导入应用库，聊天实际返回活动室 20 人的证据与模型精排分数。

重载合成和真实索引均编码 0 条；改动一个合成文档只编码 1 条、复用 14 条，耗时 0.020 秒；删除后没有返回被删除文档。测试覆盖维度不匹配、建索引失败保留活动代、跨连接及精排期间删除、风格 train/input 隔离、历史偏好、主体和范围过滤、推测状态及未知个人回答不调用生成模型。

本地 Qwen 图联动也已执行：活动室问题返回 20 人、1 个文档证据且无警告；未知月球经历返回 `no_personal_data` 并说明不知道；缺少范围返回 `ambiguous_scope`；本人偏好返回 1 个本人来源证据。最终加载 general/personal/style 的 1/58/200 条记录均编码 0 条。验证脚本为 `scripts/verify_retrieval.py`，使用已启动的回环 Qwen 服务，记录在 `data/private/t7/verification/`。

检索评测复现脚本为 `scripts/evaluate_retrieval.py`；私有记录在 `data/private/t7/evaluation/run-20261005-210320/`，首批测量报告在该目录的 `report.json`。真实原文查询不进入本公开说明。T7 完成时 82 项测试通过；T8 后为 98 项，上下文、独立检查点及用户纠正来源联动见 [CONTEXT.md](CONTEXT.md)。
