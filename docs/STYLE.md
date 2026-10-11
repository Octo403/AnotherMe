# 画像、表达风格与本人评估（T6）

2026-10-04 完成 T6.1—T6.5；按用户选择下载并运行本地 Qwen3-4B，实际生成 T6.6 的 30 题×3 提示共 90 份匿名回答，耗时 48.26 秒。**本人给出 26 份完整评分后选择结束本轮，整体不像；T6.6 评测工作收尾，风格效果未达预期。** 模型安装和实测见 [LOCAL_MODEL.md](LOCAL_MODEL.md)，本人反馈与覆盖限制见 `STYLE_EVALUATION.md`（仅本机研究记录，不随源码发布）。

## 数据、统计与画像

沿用 T3 的排除来源，对 260 组可复核候选检查上下文与本人回复；新增候选由 Codex 本地检查。排除 8 组上下文过弱、答非所问或含不必要具体行程/位置的样例，最终导入 **252 组：train 200、dev 19、test 33**。训练回应标签为同意 48、拒绝 9、安慰 4、解释 10、任务讨论 18、不确定 16，另有日常交流 114；标签可重叠，安慰类仍需扩展。

新增 463 条来源的本地复核，未覆盖已有排除状态。T4 导入再次核对来源、时序、本人目标和附件条件。随机查看 20 条目标与真实来源一致，训练/留出来源交集 0，重复导入新增 0。原 T3/T4 文件保留历史记录，当前权威导入材料是 `data/private/t6/dataset/`，不要用旧 T3 子集覆盖当前样例。

`style/statistics.py` 仅统计训练目标，上下文重叠不增加发言频次；不删除“嗯”“哈哈”或相同真实回复。统计长度、短回复、连续消息、分行、口语、标点、表情、问句线索和礼貌表达。连续消息按目标源消息数计算，不把一条原消息中的换行当成多条。

| 范围 | 训练回复 | 中位字数 | ≤12字 | 分行回复 |
|---|---:|---:|---:|---:|
| c0067 | 116 | 10 | 69 | 41 |
| c0103 | 46 | 9 | 32 | 9 |
| c0177 | 16 | 9 | 9 | 5 |
| c0239 | 22 | 12.5 | 11 | 5 |

四个范围的关系均保持 `unknown`，不从名字猜测朋友/老师。字数为 Python 字符数，口语按子串计数（“嗯嗯”也含两个“嗯”），问句仅为规则线索，不等于完整语义标注。

`data/private/t6/profile.json` 有 13 条描述：每个范围各 3 条长度、分行/表情、口语观察，加上 T5 的 1 条直接偏好。每条有范围、依据、状态、开关、来源和 trait ID；不从频次生成性格或健康结论。统计观察为 `observed`，应用库保存为 `basis=inferred/status=observed`，与直接自述分开；推断候选不能启用为确定画像。直接偏好不进入语气提示充当个人事实。

可修改 JSON 的 `text/enabled`，运行 `apply-profile` 后重启 Agent。来源必须是已复核、同一会话、本人、训练事件的正文。禁用或从完整配置移除条目会禁用托管描述；重复应用相同配置不增加条目或索引修订号。删除来源会使相关画像失效；私有 JSON 快照也需整理。

## 检索与五节点接入

`StyleRetriever.search(query, scope=..., scene=..., limit=3)` 只查 train，按用户显式会话范围和回应场景过滤。空范围返回空结果，不猜来访者身份。输入相似度仅使用历史对方输入，目标不参与向量拟合或排序；目标用于场景标签和形式描述。当前为字符 TF-IDF，不是语义向量或模型 Rerank，也不复用 T4 目标文本索引充当输入检索。

同意、解释、安慰、不确定四个实际查询命中，最高输入相似度约 0.068、0.805、0.287、0.297。命中不代表主观上像本人，低分和规则标签的质量继续由 T6.6/T7 测量。

`compose_prompt()` 分开放事实证据、工具结果、观察画像和表达形式。自动检索样例只贡献长度、分行、问句/表情线索及限定短口语，不传完整历史目标或上下文。第一轮修复另可配置人工选取的去事件化短对话示范：只使用已复核 train 目标的短片段，输入明确标为改写；示范不充当事实。具体地点、日期、人物、计划和经历不能借风格样例进入事实；测试加入虚构地点确认其不出现在提示。

`AnotherMeAgent.ask(..., scope='c0067', scene='agree')` 已可选接入：context 每轮重新加载，reflect 组合提示，Reply 返回形式指导和画像来源。空范围清空上一轮材料，已实际调用验证。默认不加载个人库；demo 仍为离线证据，不声称生成了相似语气。

```powershell
.venv\Scripts\python.exe -m aitown.style stats --output data/private/t6/statistics.json
.venv\Scripts\python.exe -m aitown.style search "Word怎么转PDF" --scope c0103 --scene explain
.venv\Scripts\python.exe -m aitown.style apply-profile data/private/t6/profile.json
```

可选配置如下，然后使用 `aitown --mode demo ask "明天一起去" --scope c0067`：

```dotenv
STYLE_DATABASE=data/runtime/application.sqlite3
STYLE_PROFILE=data/private/t6/profile.json
STYLE_SCOPE=c0067
```

`--scope` 覆盖环境范围，空字符串禁用本轮画像与样例。不同关系宜用独立 session，T8 完善上下文绑定。live 会发送观察摘要与短口语；依现有 task.md 要求先确认服务和资料范围，本次未远程调用。

第一轮修复可选增加 `STYLE_FEEDBACK=data/private/t6/repair_round1/feedback.json`。该配置保存本人反馈来源、规则开关和短示范，仅对配置内的 scope 生效；生成、自省使用相同规则，拒绝示范按代写/回应对方拒绝分别选取。短聊天句末句号处理可通过规则的 `omit_terminal_full_stop` 关闭。修改 JSON 后下一轮调用读取更新，无需覆盖原画像或评分。当前只有 c0067 的本人评测依据，不自动推广到其他会话。实际效果与限制见 `STYLE_EVALUATION.md`（仅本机研究记录，不随源码发布）。

## 本人盲评

准备 30 个未用于训练的合成输入，六类场景各 5 题，比较普通提示、观察画像、画像加表达样例三种版本。统一事实约束、生成适配器和参数；每题 A/B/C 顺序打乱，评分文件不显示版本，映射在私有 `trials_private.json`。不读取留出目标作为检索输入或答案。30 个“画像加风格”试验都有训练输入命中。

90 份回答已由本地模型生成。本人提交 26 份完整评分和 1 份仅文字批注后明确结束本轮，反馈整体不像。`complete=false` 继续表示未评满 90 份；本轮评测状态为 `closed_by_user_with_partial_ratings`。均分、配对比较和改进方向见 `STYLE_EVALUATION.md`（仅本机研究记录，不随源码发布），私有完整汇总为 `evaluation/summary.json`。

```powershell
# 已准备好；重新准备会覆盖该目录记录，已有评分时请用新目录。
.venv\Scripts\python.exe -m aitown.style prepare-evaluation --profile data/private/t6/profile.json --scope c0067 --output data/private/t6/evaluation
# 本地模型已配置，此步骤已实际执行；已生成试验会跳过。
.venv\Scripts\python.exe -m aitown.style generate-evaluation data/private/t6/evaluation
.venv\Scripts\python.exe -m aitown.style score data/private/t6/evaluation
```

生成逐项保存，可继续未完成调用；完成后回答写入匿名 `ratings.json`。本人打开 `data/private/t6/evaluation/review.html`，加载生成后的记录，按自然度、像本人、场景适配各评 1—5 分，写具体理由，下载并保存回评分目录。页面离线运行，没有回答时不能评分。数值汇总要求实际评分者、生成回答、合法的三项分数和理由；仅文字批注保留但不计入均分。工具同时提供同题三组均有评分的配对均分。未评满 90 份不标为全量完成；本人明确选择结束部分评测时，单独记录本轮收尾状态。

70 项测试通过，覆盖统计、输入排序、范围/集合过滤、画像编辑/删除、推断限制、事实/风格分离、图接入及清空、盲评和评分校验。生成已实际完成，评分工具用合成测试验证；本人已选择部分评分结束本轮，不再要求补齐。
