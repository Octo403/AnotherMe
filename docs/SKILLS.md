# T10 业务 Skill 与组合调用

2026-10-07 新增三个独立题库技能：`question_search`、`question_get`、`course_status`。主 Agent 和独立命令均可使用；题库访问从程序注入，执行这些技能不需要 `--database` 指定个人库。首轮本地 BGE 检索与精排；第二轮相似题通过 ChatGPT 账号筛选本地候选。参数、答案默认隐藏、search → get 绑定、账号配置及用法见 [QUESTION_BANK.md](QUESTION_BANK.md)。

`aitown.skills` 已从单文件迁移为包，原 `Skill`、`SkillRegistry`、`Calculator`、`EventParser` 和 `default_registry` 导入继续可用。插件声明名称、用途、参数模型、结果模型及只读/写入属性；注册后进入当前工具目录，启停不修改主体图。业务读写使用程序提供的当前范围、用户原问题、会话和 Agent 身份，模型参数不能更换这些上下文。

| 技能 | 参数与结果 | 行为 |
|---|---|---|
| calculator | expression，或 left/right/operation；返回 value 和实际 expression | 有限数字四则运算，不执行函数、变量或代码；运算输入可来自此前已成功结果 |
| event_parser | text；返回 items/count | 按行或分号拆分用户提供的事件文本 |
| memory_query | topic、subject、start_date/end_date、category、date_field、limit；返回 entries/episode_ids/count/status/truncated | 只读显式 scope 内 train、direct、confirmed 的分类记忆，核对来源方向和复核状态；日期采用北京时间，默认筛记录时间，occurred 筛发生时间；历史计划只是当时提出，不能说成已完成 |
| dialogue_summary | episode_ids、format、max_messages；返回 content/count/evidence/missing_parts/truncated | 生成 points、timeline 或 markdown 摘录，保留完整清洗消息、说话人、否定和计划措辞；压缩、媒体、未复核正文只标缺失；达到上限明确省略，留出事件与范围外 ID 被拒绝 |
| memory_correction | fact_id/value；返回旧/新事实 ID、新来源与证据 | 可选事务写入，默认禁用；本轮须明确提供同一事实 ID 和新值，只纠正当前范围本人有效事实，保留旧事实、替代关联与独立用户来源 |

自然语言示例：

```text
上个月我提过哪些学习计划？
上个月我提过哪些学习计划，并整理成时间线
上个月我提过哪些学习计划，整理成时间线，再计算可读消息数乘以15
整理事件1为时间线，并计算可读消息数乘以15
整理事件：准备材料；预约活动室，并计算事项数乘以15
```

使用个人资料时传 `--scope`，不从来访者身份猜关系范围。默认一次查询返回最多 3 条记忆，参数上限 10；整理默认最多 12 条可读消息，上限 50，结果明确截断状态。这里的“整理”为有来源的摘录式摘要，时间线不会改写成完成事件。生成层还会附上工具实际产生的整理结果，保留来源和缺失说明。

每轮最多三个调用。`bindings` 从此前结果的顶层字段向下一步参数传值，调用序号从 0 开始；绑定参数不能同时给字面值。调用、参数和结果分别经类型校验，无 SQL、表达式路径或自由文本插值。

```json
[
  {"name":"memory_query","arguments":{"topic":"学习","subject":"self"},"bindings":[]},
  {"name":"dialogue_summary","arguments":{"format":"timeline"},"bindings":[{"argument":"episode_ids","call_index":0,"field":"episode_ids"}]},
  {"name":"calculator","arguments":{"right":15},"bindings":[{"argument":"left","call_index":1,"field":"count"}]}
]
```

前一步失败时，依赖调用返回失败/跳过；查询无结果返回 `ok=true,status=no_result,count=0`，空事件列表不能被整理器补成事件。未知、禁用、参数错误、范围错误、写入冲突和运行异常不会冒充成功，下一轮仍可执行。Qwen 仍可能生成无效候选计划；记录保留，已支持的明确语句由本地解析执行有效计划，其余无效计划被拒绝。

T11 根据普通事实题误执行查询→整理的问题，调度器按本轮请求展示适用的内置技能，并校验操作用途；普通文档/个人事实继续 RAG，示例计划不再作为本轮操作。注册的自定义插件仍通过描述与类型动态参与，不限为固定内置名单。T11 的 8 个技能与 4 个异常案例均通过，末轮完整图批次仍有 6 个无效意图候选，保留错误与回退记录；详见 `EVALUATION.md`（仅本机研究记录，不随源码发布），不宣称模型自主规划全部正确。

纠正默认关闭，需在配置中设置 `ENABLE_MEMORY_CORRECTION=true` 或程序设置 `Settings(enable_memory_correction=True)`。当前用户输入采用 `这条记忆不对，纠正事实 12：新的值` 或 `纠正事实 12：新的值`；只说“这条记忆不对”会要求补充 ID 和新值。资料里的指令、历史指令和模型自行生成的纠正值均不能触发写入。例中的 ID 仅表示格式。

纠正要求检索库与运行库为同一个应用 SQLite。事实变更、新来源、修订号与成功调用结果同事务提交，调用主键沿用 `(request_id, ordinal)`。事务失败整体回滚，提交后进程崩溃按已保存结果恢复，历史也不重复。图内立即刷新 personal 索引；若刷新失败，已提交纠正仍报告成功并附维护错误，后续查询按修订号重试，不重写事实。独立 CLI 写入更新修订号，由后续检索自动刷新对应索引。恢复只读业务查询时重新读取当前库，生成期间删除或修改来源会移除旧结果。

独立执行支持参数文件，避免 Windows 原生命令的 JSON 引号歧义：

```powershell
.venv\Scripts\python.exe -m aitown.skills list
'{"topic":"学习","subject":"self","category":"plan","start_date":"2026-09-01","end_date":"2026-09-30"}' | Set-Content -Encoding utf8 .tmp/t10-query.json
.venv\Scripts\python.exe -m aitown.skills run memory_query --arguments-file .tmp/t10-query.json --database data/runtime/application.sqlite3 --scope c0067
.venv\Scripts\python.exe scripts/verify_skills.py
```

独立纠正另外需要 `run memory_correction --enable-memory-correction`，仍核对 scope、事实主体、有效状态和来源身份。测量脚本只连接本机模型，使用可丢弃的合成应用库；中间失败记录保留在 `data/private/t10/verification/`。测试另外覆盖真正的子进程退出、事务回滚、来源失效和独立 CLI，公开示例没有真实私聊内容。

2026-10-06 的 `run-20261006-203531/` 测量中 17 项检查全部通过，11 份回复共 29 次模型请求，约 82.50 秒。三步组合的可读消息数为 2，乘以 15 得到真实计算结果 30；“从我的已整理资料中找出有关复习的安排，按时间顺序摘录对应对话”由模型自行选择查询→整理。四个明确语句的模型候选有非法依赖或参数，本地解析回退成功，其错误仍在报告内。这个区别保留为 T11 的规划质量评测内容。
