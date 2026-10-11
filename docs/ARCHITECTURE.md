# AnotherMe 应用数据库与来源追溯

2026-10-04 完成 T4.1—T4.5，T5 新增分类记忆及来源表，详见 [MEMORY.md](MEMORY.md)。T3 私有文件导入独立应用 SQLite；源清洗库和原始导出保持不变。2026-10-05 的 T7 已将个人事实/事件和训练风格接入可选本地语义检索；当前本机配置已启用，FAISS 与 catalog 持久化并支持增量维护，见 [RETRIEVAL.md](RETRIEVAL.md)。T8 将应用库升级至版本 3，统一运行历史、临时数据和显式用户来源，图检查点使用独立 SQLite，见 [CONTEXT.md](CONTEXT.md)。

## 数据结构

`src/aitown/storage/schema.py` 使用 `PRAGMA user_version` 管理版本；当前版本 3，支持空库初始化与版本 1→2→3 的事务迁移，保留已有记录和主键。版本 3 新增运行轮次、临时缓存、摘要来源和用户来源关联，历史新增请求主键与重要性字段。每次打开连接启用外键，较新版本拒绝用旧程序打开，不自动删除已有表或内容。

| 数据 | 表与约束 |
|---|---|
| 导入批次 | `import_batches`，批次主键、来源类别、时间覆盖与导入状态 |
| 清洗消息 | `message_refs`，应用主键、批次＋清洗消息 ID 唯一键、会话代称、时间、方向、正文与复核状态 |
| 事件 | `episodes`、`episode_messages`，批次＋原事件 ID 唯一键、消息关联和顺序 |
| 引用 | `message_links`，引用消息和被引用消息外键，未知状态不虚构关联 |
| 事实 | `facts`、`fact_sources`，主体、属性、值、有效时间、状态及来源 |
| 画像 | `persona_traits`、`trait_sources`，直接自述/推断、场景和来源 |
| 表达样例 | `style_examples`、`style_sources`，上下文、本人目标、场景、集合和来源角色/顺序 |
| 文档 | `knowledge_documents`、`document_sources`、`chunks`，用途、来源、分片与顺序 |
| 索引 | `embedding_items`，类型明确的实体外键、向量位置、模型、维度和数据版本 |
| 运行历史 | `chat_sessions`、`chat_turns`，Agent 会话与实时对话 |
| 运行状态 | `runtime_sessions`、`runtime_runs`、`runtime_skill_calls`，会话线程、输入与原子写入结果 |
| 临时数据与摘要 | `transient_context`、`context_summaries`、`summary_sources`，过期时间与历史原文关联 |
| 显式用户来源 | `source_records`、`fact_statement_sources`，独立自述/纠正来源，不伪造微信消息 ID |
| 反馈与工具 | `feedback`、`skill_runs`，对话外键、反馈与工具运行状态 |
| 删除与更新 | `deleted_sources` 保存必要来源键；`data_revisions` 保存普通递增数据版本 |

清洗消息 ID 只在一个导入批次内使用；不同批次不因为 ID 或文本相同就合并。跨备份去重属于 X1。应用中默认会话代称为清洗会话 ID，不读取真实联系人映射。

## 导入与查询

```powershell
.venv\Scripts\python.exe -m aitown.storage import-t3 data/private/t3
.venv\Scripts\python.exe -m aitown.storage counts
.venv\Scripts\python.exe -m aitown.storage sources style 1
.venv\Scripts\python.exe -m aitown.storage query style "课程"
.venv\Scripts\python.exe -m aitown.storage query fact "课程"
.venv\Scripts\python.exe -m aitown.storage query history "课程" --session default
```

默认库为 `data/runtime/application.sqlite3`，可用全局 `--database` 参数指定。导入读取 T3 的消息、事件、引用、复核记录及 train/dev/test；一次导入的所有数据库写入都在同一事务中。错误批次整体回滚，不留下部分消息、事件或样例。

同批次重复导入通过唯一键保持幂等。更新已有消息会失效依赖旧内容的事实、画像、样例和文档；重新切分允许更新事件关联，输入中已消失的样例会被移除。没有提供的原消息不被当成用户删除；删除须调用明确的维护接口。已删除来源键在重导入时继续跳过，避免旧副本恢复被删除的应用资料。

表达样例入库时核对：来源已复核且是普通可读文本，目标全部由本人发送，上下文早于目标、来源顺序和角色与消息方向一致，文本与来源原文一致。风格样例不会自动变成事实。

`Repository.sources(kind, id)` 返回应用消息 ID、清洗批次/消息 ID、清洗会话代称、时间、方向、说话人、正文和事件 ID。关联仍可追溯到 T3 和清洗库，不输出原服务端标识或私有原名映射。T8 的显式用户来源用 `source_kind` 与 `record_id` 区分，消息/清洗批次 ID 为空。

三个查询路径返回不同 Pydantic 类型：`query_facts` 返回当前有效事实，`query_styles` 默认只查询 train，`query_history` 按 Agent 与会话查询实时历史。画像查询默认只读 `direct` 自述，推断须显式选择。`save_history`、`add_feedback`、`record_skill` 提供运行记录接口；实时历史不会自动插入归档事实。

## 纠正与删除

```powershell
# ID 是应用数据库主键，来源 ID 可从 sources 返回结果中取得。
.venv\Scripts\python.exe -m aitown.storage correct-fact 1 "修正值" --source 10
.venv\Scripts\python.exe -m aitown.storage delete-source 10
.venv\Scripts\python.exe -m aitown.storage delete-document 1
```

这些命令会修改指定应用库，应按实际资料执行。上面的数字只表示调用格式，不代表本机已经存在对应事实。

事实纠正使用明确的新来源，在一个事务中新增事实并将旧事实标为 `superseded`；旧事实保留来源和替代关联，默认查询及索引只使用当前有效事实。此接口是存储维护，不替代 T5 的事实归属与准确性复核。

删除来源会完整移除依赖它的事实、画像、表达样例和派生文档，包括相关切片和向量关联；不会仅删关联后留下无法解释的旧正文。引用目标删除后标记缺失。删除文档会移除文档、切片和向量关联。

`storage/index.py` 提供本地字符 TF-IDF＋FAISS 索引，供当前数据库维护验证与后续接入。数据版本变化时重建当前索引；搜索结果再从当前数据库解析实体内容，因此另一个连接删除或纠正资料后，已打开的旧索引也不返回缓存旧正文。风格索引只读 train，画像索引默认只读明确自述。向量记录关联通过实体外键与普通版本维护，不使用新增散列或冻结文件。

维护范围是应用数据库及由它管理的索引。它不删除原始备份、旧清洗副本、T3 私有文件或现有聊天 CLI 的独立知识目录；不能据此声称所有源副本已经被物理擦除。上述 `storage/index.py` 仍是内存 TF-IDF 回退；T7 的 `retrieval/index.py` 已保存 FAISS 文件和独立 SQLite catalog，搜索从当前应用记录确认来源、状态与删除，详见检索说明。

## 本机实测

- 1 个清洗批次导入 1,830 条消息、596 个事件、110 个样例，源关联条数与消息数一致。
- 重复导入新增计数全部为 0；训练风格索引包含 84 个样例，不包含 dev/test。
- 随机 20 个真实样例的来源正文、时间、方向、批次和事件与 T3 消息逐项核对一致；外键检查无错误。
- 在应用库副本上故意导入缺少来源的批次，整批回滚；验证了纠正后的当前事实、另一连接删除后旧索引的失效、删除后重导入不恢复来源。
- 40 项测试通过，含数据库迁移与重启、外键、重复导入、事务回滚、类型隔离、来源追溯、事实纠正、派生文档删除和跨连接旧索引。

以上是 T4 完成时的测量。T5 后真实库新增 114 条分类记忆、1 条本人直接事实、1 个托管训练知识文档，T5 当时画像为 0。T6 后样例扩至 252（train 200），新增 13 条带来源观察/偏好画像，70 项测试通过。T7 接入个人检索，T8 接入统一运行历史与独立图检查点，当前 98 项测试通过；版本 2→3 迁移与恢复测量见上下文说明。事实、画像与运行历史的 T4 接口用合成资料验证；真实 T4 维护测量使用 `data/private/t4/maintenance.sqlite3` 副本，未删除正式应用库来源。

私有核对记录为 `data/private/t4/source_checks.json`、`verification.json`；正式应用库和私有产物继续被 Git 忽略，未向外部服务发送聊天内容。
