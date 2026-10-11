# 本地 Qwen 模型

根据用户“下载一个模型放在本地”的选择，2026-10-04 下载并运行官方 [Qwen3-4B-GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF) 的 Q4_K_M 版本，使用官方 [llama.cpp Windows Vulkan 程序](https://github.com/ggml-org/llama.cpp/releases/tag/b11384)。

模型文件为 `data/runtime/local_model/Qwen3-4B-Q4_K_M.gguf`，实际大小 2,497,280,256 字节；推理程序位于同目录 `llama/`，版本 b11384。二者及日志已由 `data/runtime/` 规则排除，不进入版本控制。下载经本机代理访问官方仓库，模型推理不依赖下载代理。

本机实测约 32 GB 内存、RTX 5070 Laptop 8,151 MiB 显存。Vulkan0 对应 NVIDIA 显卡；当前上下文 8,192、单槽、全层 GPU、关闭 thinking，启用 offline 模式。10-04 初次测量使用 4,096 上下文，服务运行时显卡总占用约 3,759 MiB（包含桌面和其他进程，不能等同模型独占量，也不是当前 8,192 配置的显存测量）。模型数据和推理均在本机。

## 启停和使用

项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_local_model.ps1
.venv\Scripts\aitown.exe --mode live ask "你好" --scope c0067
.venv\Scripts\aitown.exe --mode live chat --session local-chat --scope c0067
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop_local_model.ps1
```

服务仅监听 `127.0.0.1:8765`，后台隐藏启动，启用本地 API 验证及 localhost CORS；不开放远程网络地址，不启用服务端工具、代理或 MCP。启动脚本识别已有本项目进程，停止脚本验证 PID 的实际程序路径，避免结束别的程序。

启动脚本的 `-ContextSize` 默认 8,192，可在 2,048—32,768 内显式配置；调整已运行服务时先执行停止脚本，再以新值启动。2026-10-07 真实微信三步技能演示在 4,096 配置下遇到上下文不足，压缩重复提示并切换到 8,192 后实际运行通过；应用字符预算仍为 6,500。证据见 `WECHAT_UI_DEMO.md`（仅本机研究记录，不随源码发布）。

本地 `.env` 已配置：`LLM_BASE_URL=http://127.0.0.1:8765/v1`、`LLM_MODEL=qwen3-4b-local`、温度 0.3、最多 512 tokens。`LLM_API_KEY` 为仅用于该回环服务的本地配置，不是外部服务密钥。HTTP 适配器在回环地址禁用环境代理，个人提示不会被环境代理转发。

这里 `live` 表示实际模型生成，不表示使用云服务；`--mode demo` 仍可完全离线显示证据。默认知识目录仍为合成资料，个人事实和风格须显式 `--scope`，不自动猜测身份。2026-10-05 T7 已另外下载本地 BGE 中文向量与精排模型，并启用应用库语义检索；使用 CPU 推理，不依赖 Qwen 服务，详见 [RETRIEVAL.md](RETRIEVAL.md)。TF-IDF 保留作演示和回退。

## 实际执行

本地 `/health` 返回正常，JSON object 输出经 Pydantic 校验。用同一模型/参数完成 30 题×3 提示共 **90 份回答，耗时 48.26 秒**；全部非空且写入匿名评分记录。这里只验证成功生成，不代表三种提示的风格评分。

五节点实际调用验证了闲聊、计算（结果 310）、Word 教程与知识库活动室查询（命中 demo.md，返回 20 人）。实测曾把 Word 教程误分给事件工具，补充调度提示后正确走知识路径。自省存在一例修正说明与最终答案不一致，后续 T9 继续评测，不能据几次调用宣称自省已可靠。

私有证据：`data/private/t6/evaluation/generation_report.json` 和 `data/private/t6/local_model_verification.json`。未调用外部模型，没有发送私聊原文或个人画像至远程服务。本人已给出 26 份完整评分和 1 份仅文字批注，并选择结束本轮 T6.6；风格效果不理想，详见 `STYLE_EVALUATION.md`（仅本机研究记录，不随源码发布）。原生成报告中的 user_scores=0 是生成结束时的历史值，当前评分以 evaluation/summary.json 为准。

2026-10-05 T8 又用同一本地模型运行六轮合成方案对话，在第六轮前重建 Agent，仍正确回答名称与修改后的 10 人参数；当前文档和显式用户纠正也覆盖了历史助手猜测。末轮曾虚构技能名，失败被正确记录，完整调度校验留待 T9。实测记录为 `data/private/t8/verification/run-20261005-221940/`，恢复与上下文说明见 [CONTEXT.md](CONTEXT.md)。

2026-10-06 T9 完成能力测量与调度验证。本机 `.env` 使用 `LLM_RESPONSE_FORMAT=json_schema`、`JSON_REPAIR_ATTEMPTS=1`、`CONTEXT_MAX_CHARS=6500`。实测聊天、JSON Schema、强制原生工具调用均支持，embedding 返回 501、错误请求 400、错误鉴权 401；业务图仍采用 `validated_json_plan`，原生探测不执行工具。初始 JSON object 曾复述 Schema，记录保留，使用 JSON Schema 后通过。

完整测量位于 `data/private/t9/verification/run-20261006-193430/`：六轮与重启、两个组合顺序、本人纠正、他人主体、歧义澄清及注入错误初稿后的真实自省均通过，未知经历明确 no_evidence。正常案例没有未知或失败技能执行，最终自省检查为空。服务停止和校验失败的中间测量也保留，不能用离线证据回退冒充真实回答。

正式 c0067 风格接入曾因每条画像附 169 个来源 ID，使请求达到 4324 tokens，超过本机 4096 上下文，服务返回 400。现在模型提示只传画像/样例 ID 和形式描述，完整来源留在资料库、状态和回复；加载 3 条画像、3 条样例的实际生成和自省通过。这里仅验证风格接入，原本人评分不变。命令、成本及限制见 [PIPELINE.md](PIPELINE.md)。
