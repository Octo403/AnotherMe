# 2026-10-11 源码发布验证

从 Git 待发布文件集创建独立副本，不复制 `.env`、现有资料库、模型权重或虚拟环境；使用新的 Python 3.13.3 虚拟环境离线安装 `.[dev,privacy,question-bank]`。

全量回归 **335 项通过，耗时 75.85 秒**。测试使用独立临时目录，不修改正式资料库。源码内联系人清洗测试使用虚构姓名。

```powershell
uv venv .venv --python 3.13
uv pip install --python .venv\Scripts\python.exe -e ".[dev,privacy,question-bank]"
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <已存在父目录下的新临时目录>
```

待发布数据文件仅为 `data/knowledge/demo.md`、`data/knowledge/memory_demo.md` 两份虚构示例。已检查 `.env`、环境变体、私有资料、评测输出、模型目录、`user-data/` 和 `dist/` 均被忽略，源码未命中本机实际私密凭证。便携 ZIP 顶层无 `data/`、`user-data/`、`.env` 或 `.git`；包内 113 个应用文件与本次源码逐文件比较一致（仅归一化换行）。

便携版真实运行与浏览器检查为 2026-10-09 的独立验证，见 [验收摘要](portable-20261009.json)。本次没有把历史检查冒充为新设备验证，也未重新构建不变的便携包。
