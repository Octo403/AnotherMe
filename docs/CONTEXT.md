# T8：运行上下文与图恢复

2026-10-05 完成 T8.1—T8.7。五节点图使用应用库保存实时历史、临时数据和显式用户来源，使用独立 SQLite 保存 LangGraph 检查点。实现位于 `src/aitown/runtime/`，入口为 `AnotherMeAgent` 和 CLI。

## 三类数据的生命周期

| 类别 | 内容与存储 | 刷新和过期 |
|---|---|---|
| 当轮状态 | 输入、开始时间、意图、引用、技能结果、警告、纠错计数，LangGraph state | 每次新输入由 context 清空上一轮结果，重新读取本轮允许的资料 |
| 实时上下文 | `chat_sessions/chat_turns`、来源明确的旧日常摘要 | 默认最近 8 轮，配置不低于 5 轮；原记录保留默认 168 小时，窗口截断不会删除原文 |
| 永久资料 | 微信来源、分类记忆、事实、文档，以及显式自述/纠正的 `source_records` | 由资料维护接口管理；历史和临时缓存的 TTL 不删除这些资料 |

临时检索结果、场景参数和通过 `put_transient(..., kind='plan', ...)` 保存的短期计划在 `transient_context` 有独立 `expires_at`，默认 600 秒，可单独指定期限。过期读取返回空，加载历史时清理数据库中过期数据。归档中的计划保留原记录，但 T7 检索按其有效期判断是否仍适用于当前问题。实时对话提到的虚构任务参数仅属于该会话，不自动转成永久事实。

运行历史按 `[agent_id, session_id]` 构造可读的 `thread_id`，通过普通唯一键绑定；读取原文同时核对 Agent 和会话。旧 `MEMORY_DB` 的有效历史只读导入一次，原 Agent ID 保留；过期记录不因再次启动被重新导入。旧库不被修改。

## 优先级与摘要

当次用户输入定义任务；有来源的当前知识与明确纠正优先于历史猜测。历史用户提供的任务参数保留其上下文及修改顺序；历史助手发言标为模型输出。资料、工具正文和摘要以独立数据消息提供，不拼入系统指令。文档中的命令不能改变资料范围、主体或工具权限。

较旧的低重要日常交互采用摘录式摘要：保留完整用户文本、时间、`turn_id`，省略冗长助手套话，并标明助手文本不是事实。包含否定、日期、变化或本人归属的交互保守地保留为重要记录，不由摘要改写。摘要最多选取窗口外最近 32 条候选；摘要与原文均按实时历史 TTL 管理。重要原文可通过 `history-source` 查询；超过历史 TTL 需要长期保留的内容应先形成显式来源事实。摘要本身不进入个人知识索引。

## 检查点和恢复

依赖 `langgraph-checkpoint-sqlite>=3.0.1,<4`，本机实际版本 3.1.1；图调用使用 `durability='sync'`。`CHECKPOINT_DB` 使用独立数据库与连接，不能与运行库、检索库、风格库或旧历史库重合。参见官方 [持久化说明](https://docs.langchain.com/oss/python/langgraph/persistence)。

`runtime_runs` 先保存本轮输入及请求主键，检查点随后保存节点进度。如果进程在首个输入检查点之前退出，恢复从运行记录重新构建输入；即使线程仍保存上一轮回复，也继续新请求。会话存在未完成轮次时，新问题提示使用 `resume`。

恢复前刷新历史、风格和有效场景参数；恢复生成前再次读取当前知识。只读查询允许重做，因此资料在中断后更新或删除时，不使用旧检索正文作为最终依据。新轮次清空旧工具结果，恢复原轮次则保留已提交结果。

写入技能必须通过 `run_transaction(arguments, connection)` 或上下文版本 `run_transaction_with_context` 使用运行库的同一个 SQLite 事务。业务写入和成功结果以 `(request_id, ordinal)` 唯一关联并同时提交。恢复时复用匹配的已提交结果；未提交写入由 SQLite 回滚后重做。记录存在但参数改变时返回失败。T10 已新增可选的本地记忆纠正，提交后崩溃恢复不会重复修改；禁用技能仍可返回此前已提交的匹配结果，不执行新写入。外部写入默认不支持自动重放，接入具体服务时仍需其幂等能力或明确恢复方式。T8 完成时生产技能只有只读计算器和事件拆分，写入恢复用合成计数器实际测量；T10 业务技能见 [SKILLS.md](SKILLS.md)。

最终回复、历史记录和工具运行记录也在一个事务中保存；历史的 `request_id` 唯一。若历史已经提交而图节点尚未保存，恢复直接读取原回复，不再次生成或保存。未完成轮次保留恢复需要的写入记录；已完成轮次与检查点从历史提交时间起按历史 TTL 清理，长期暂停后刚完成的轮次不会立即失去恢复记录。`cleanup-context` 清理过期历史、缓存及完成线程的检查点，不删除未完成线程。

检查点反序列化只允许本项目已知状态模型，不使用 pickle 回退；这是类实例构造前的安全限制，普通状态类型校验发生在构造之后。

## 显式事实写回

`chat_turns.status` 区分 `model_output`、`demo_output` 和导入的 `legacy_output`。模型回答不会自动抽取为本人事实。

CLI `remember`、`correct` 或 API 的 `UserStatement` 保存独立 `source_records`，分别标为 `user_statement`、`user_correction`。这些来源有用户原文、Agent、会话、显式关系范围和时间，以 `fact_statement_sources` 关联事实。引用用 `source_record_ids` 返回，不冒充微信 `message_id`。纠正仅能修改同范围、同属性的当前本人事实；旧值保留来源与有效期，当前检索只返回新值，`as_of` 可查询旧值。

聊天入口仅接受非常窄的直接格式：`记住：我喜欢…`、`记住：我不喜欢…`、`记住：我住在…`、`记住：我是…`，以及 `记一下：` 前缀；需要显式非空 `--scope`。其他自然语言自述与纠正不自动推断，使用 CLI 或结构化 API。引用的资料和模型输出不进入该解析器。模型写入约束测试中，助手生成“我去过火星”后事实与用户来源均未新增。

本机统一运行库与检索库均为 `data/runtime/application.sqlite3`；这样新自述和纠正可立即参与个人检索。如果另外配置不同的运行库，显式事实保存在运行库，须让个人检索指向该库才能检索这些事实。

## 配置与命令

```dotenv
RUNTIME_DATABASE=data/runtime/application.sqlite3
CHECKPOINT_DB=data/runtime/application-checkpoints.sqlite3
HISTORY_TURNS=8
MEMORY_TTL_HOURS=168
CONTEXT_TTL_SECONDS=600
```

`RUNTIME_DATABASE` 空值时依次使用检索库、风格库或 `MEMORY_DB` 文件名加 `.application.sqlite3`；检查点默认放在运行库旁，文件名加 `-checkpoints`。`MEMORY_DB` 仅用于兼容导入旧历史。

```powershell
# 两条命令在两个 CLI 进程之间暂停、恢复；计算结果为 42。
.venv\Scripts\aitown.exe --mode demo ask "6*7" --session t8-example --pause-after skill
.venv\Scripts\aitown.exe --mode demo resume --session t8-example
.venv\Scripts\aitown.exe history --session t8-example
# 41 是格式示例；使用 history 返回的本会话来源 ID。
.venv\Scripts\aitown.exe history-source 41 --session t8-example
.venv\Scripts\aitown.exe cleanup-context

# 这两条命令会写入个人事实，仅在需要记录真实自述时执行。
.venv\Scripts\aitown.exe remember preference "不喜欢咖啡" --scope "已确认范围" --session personal
# 下面的 1 替换为 remember 返回的 fact_id。
.venv\Scripts\aitown.exe correct 1 "现在喜欢茶" --scope "同一范围" --session personal
```

交互聊天中 `/resume` 恢复当前会话。`Reply.history_source_ids` 和 `summary_source_ids` 可定位本轮使用的历史与摘要原文。

## 实际验证与限制

T8 完成时 **98 项测试通过**，T8 新增 16 项，覆盖清空当轮状态、会话与 Agent 隔离、TTL、摘要原文、来源优先级、纠正与历史值、模型输出不写事实、事务回滚及重放、旧库导入、版本 2→3 迁移、中断后刷新资料、输入检查点缺失以及恢复前历史过期。应用库现为版本 3，支持版本 1→2→3 的事务迁移。

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp/pytest-context
.venv\Scripts\python.exe scripts/verify_context.py
# 不调用模型，仅运行三个独立进程崩溃场景：
.venv\Scripts\python.exe scripts/verify_context.py --skip-live
```

2026-10-05 最终实测记录位于 `data/private/t8/verification/run-20261005-221940/`；汇总为 `report.json`，复现脚本每次新建目录。运行使用合成资料和本地模型，未发送私聊至外部服务。

| 测量 | 结果 |
|---|---|
| 写入事务内直接退出进程 | 未提交 SQL 回滚；新进程恢复后计数、写入结果记录、历史均为 1 |
| 写入已提交、技能节点检查点未保存时退出 | 新进程复用结果；上述三项均为 1 |
| 历史已提交、生成节点检查点未保存时退出 | 三项均为 1；恢复进程生成调用为 0 |
| 再次恢复完成轮次 | 返回同一请求回复，不重复写入或新增历史 |
| 真实 Qwen 连续 6 轮、第 6 轮前重建 Agent | 正确返回方案名称、最新人数 10 及前述时间；历史 12 条消息，另一会话为空 |
| 当前文档与历史猜测冲突 | 实际回答当前容量 7，未沿用猜测的 20 |
| 含命令的文档 | 命令未触发技能执行，回答仍依据文档事实 |
| 用户纠正与助手历史猜测冲突 | 回答包含茶，引用独立纠正来源，旧事实已替代 |

三个崩溃场景用 `os._exit` 真正终止进程，业务 SQL 与恢复均实际执行；调用计划由测试模型控制，不能称为真实模型自主写入测量。三场景合计约 17.53 秒。六轮和冲突回答使用本地 Qwen3-4B；隔离测试库使用 TF-IDF 检索，T7 的 BGE 实测另见 [RETRIEVAL.md](RETRIEVAL.md)。

T8 真实模型在两次六轮测量中都曾虚构名为 `skill` 的调用，最终测量第 6 轮出现该失败提示，答案仍保留正确参数；不把失败调用记为成功。这些历史结果不证明完整调度和自省可靠性。2026-10-06 T9 已加入目录校验、来源指代、有限日历解析、两阶段自省与最多一次补检；新六轮与真实模型案例均通过，122 项测试通过，三种崩溃恢复在新图上重跑通过。详见 [PIPELINE.md](PIPELINE.md)。摘要采用保守摘录，尚未评估长会话质量；CLI 按顺序使用，会话中的同一未完成轮次应由一个调用者恢复。

CLI 另在独立合成库逐项执行暂停、跨进程恢复、重复恢复、历史及原文查询、跨会话原文隔离、自述、纠正、纠正后的个人检索和清理，全部通过；记录为 `data/private/t8/cli/run-20261005-223118/report.json`。正式应用库版本 3、外键错误 0；仍有 1,830 条来源消息、114 条分类记忆、252 组样例、13 条画像、1 条事实，独立用户来源为 0，合成自述未混入正式库。
