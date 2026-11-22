# FileAgent v0.1

FileAgent 是一个在 macOS 上运行的文件助手。你可以在本机浏览器里聊天，让它列出工作区文件、按关键词搜索内容、总结文档、比较多份资料，再把答复保存为 Markdown 或纯文本报告。

## 快速开始

需要 macOS 和 Python 3.11 或更新版本。在项目根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m streamlit run app.py
```

打开 [http://127.0.0.1:8501](http://127.0.0.1:8501)，在「连接 DeepSeek」中输入 API Key，再点「验证并连接」。验证只查询 DeepSeek 账户是否有可用余额，无需发送文件或调用模型。也可在侧栏切换到「离线演示」，试用随项目提供的虚构样例。

### 保存和清除密钥

- 默认只在当前浏览器会话使用密钥。
- 勾选「记住在本机」后，验证通过的密钥保存到项目根目录的 `.fileagent-key`。这是仅当前用户可读写的明文文件（权限 `0600`），已加入 Git 忽略规则。打开新会话时会自动加载。
- 侧栏的「清除本机密钥」会清除当前会话中的密钥，并删除 FileAgent 记住的密钥文件。也可手动移除该文件。进程环境变量或 `.env` 中已有的密钥可在原位置管理，修改后重启应用。

### 用 DeepSeek 试跑

1. 首次在界面验证成功后，文件来源会选为「示例文件」。点击「比较两个采购方案」可看到真实工具调用、模型答复及费用估算。
2. 使用自己的资料时，在侧栏选「我的工作区」，把允许发送给 DeepSeek 的文档放入 `workspace/`。也可在 `.env` 中设置 `FILEAGENT_WORKSPACE`，指定项目外已存在的专用目录。
3. 在最新的成功答复下展开「保存报告」，填写名称并选择格式。报告写入项目根目录的 `outputs/`，同名文件会提示改名。

如需手动配置，可把 `.env.example` 复制为 `.env` 并填写 `DEEPSEEK_API_KEY`。也支持通过环境变量提供密钥。默认模型为 `deepseek-flash`，可通过 `DEEPSEEK_MODEL` 设置。

## 文件范围与限制

- **文件工具**支持递归列出、分页读取，以及大小写不敏感的字面文本搜索。TXT、MD 和 CSV 使用 UTF-8，可包含 BOM。DOCX 读取正文段落及正文表格。PDF 提取文本并标注页码，扫描件需要先 OCR。
- **读取上限**为 8 MiB、每次返回最多 12,000 字符、每个文件最多提取 240,000 字符。最多处理 PDF 前 120 页及 CSV 前 1,000 条数据记录，DOCX 解压总量上限为 40 MiB。目录枚举最多检查 1,000 个条目及 12 层路径。每次搜索最多检查 40 个文件，匹配行号对应提取后的文本。
- **访问规则**仅允许工作区内的受支持普通文件，拒绝绝对路径、路径遍历、隐藏文件、软链接和硬链接。源文件只读。报告需要在界面点击保存，使用独占发布防止覆盖。工具只有 `list_files`、`read_file` 和 `search_files`。
- **数据流向**由运行模式决定。离线演示在本机处理样例。DeepSeek 模式会发送聊天消息、工具结果中的相对路径及内容片段，供模型生成答复。保持 Workspace 专用，放入前先确认资料可发送。聊天在内存中保留，日志记录调用类型、耗时和状态，不保存密钥、工具参数或文件正文。
- **Git 发布**前请检查准备提交的文件。`.gitignore` 排除了 `.env`、`.fileagent-key`、默认 `workspace/`、`outputs/` 和虚拟环境。将私有工作区配置在项目外，或使用默认的 `workspace/`。随项目提交的数据仅来自 `examples/` 中的虚构样例。

## 调用与费用

OpenAI Python SDK 调用 DeepSeek Chat Completions API。模型返回原生 `tool_calls` 后，本机校验 JSON 参数并执行白名单工具，将每条结果附上对应的 `tool_call_id` 回传模型。每条用户消息最多发起 6 次模型请求和 12 次工具调用，支持一次响应中的多个工具调用。后续对话会携带最近 6 条成功问答。

HTTP 请求默认每次尝试 30 秒，SDK 对暂时性故障最多重试 2 次。界面中的模型请求数按 Agent Loop 计数，SDK 内部重试包含在对应请求的耗时中。

费用根据 API 返回的输入 Token、缓存命中 Token 和输出 Token，按美元高峰费率估算（费率核对于 2026-11-22）。没有缓存统计时，输入按未命中估算。没有用量记录或未配置费率的模型，界面会说明估算范围。参见 [DeepSeek 模型与定价](https://api-docs.deepseek.com/quick_start/pricing/) 和 [Tool Calls 文档](https://api-docs.deepseek.com/guides/tool_calls/)。

## 测试

```bash
python -m pytest -q
```

测试以临时工作区、SDK 模拟 HTTP 响应和 Streamlit 测试接口覆盖浏览器密钥验证与保存、文件解析、路径安全、调用循环、异常处理、用量统计及报告保存。测试无需 API Key，也不发送文档或外部请求。

## 许可

项目使用 AGPL-3.0-only，许可证全文见 `LICENSE`。PDF 读取依赖 [PyMuPDF](https://pymupdf.readthedocs.io/en/latest/about.html)，其开源版本使用 AGPL。
