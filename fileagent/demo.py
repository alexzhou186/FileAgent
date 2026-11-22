import json

from fileagent.backend import Completion, ToolCall, Usage

DEMO_PROMPTS = {
    "总结项目简报": "请总结 brief.md 的目标、预算和验收标准。",
    "比较两个采购方案": "请比较 plan-a.txt 和 plan-b.docx 的预算、交付时间和覆盖人数。",
    "搜索预算": "请在示例文件中搜索“预算”。",
}


class DemoBackend:
    """Scripted Chat Completions responses for the included examples."""

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        user_index = max(index for index, item in enumerate(messages) if item["role"] == "user")
        prompt = messages[user_index]["content"]
        results = [json.loads(item["content"]) for item in messages[user_index + 1:] if item["role"] == "tool"]
        calls = ()
        answer = None

        if prompt == DEMO_PROMPTS["搜索预算"]:
            if not results:
                calls = (ToolCall("demo-search", "search_files", '{"query":"预算"}'),)
            else:
                lines = [f"• {item['path']} · 第 {item['line']} 行 · {item['excerpt']}"
                         for item in results[0].get("matches", [])]
                answer = "“预算”的搜索结果\n\n" + ("\n".join(lines) or "未找到可显示的匹配。")
        elif prompt in (DEMO_PROMPTS["总结项目简报"], DEMO_PROMPTS["比较两个采购方案"]):
            summary = prompt == DEMO_PROMPTS["总结项目简报"]
            paths = ["brief.md"] if summary else ["plan-a.txt", "plan-b.docx"]
            if not results:
                calls = (ToolCall("demo-list", "list_files", "{}"),)
            elif len(results) == 1:
                calls = tuple(
                    ToolCall(f"demo-read-{index}", "read_file", json.dumps({"path": path}))
                    for index, path in enumerate(paths)
                )
            else:
                sections = []
                for path, result in zip(paths, results[1:]):
                    if "error" in result:
                        sections.append(f"{path}\n{result['message']}")
                        continue
                    lines = [line for line in result["text"].splitlines()
                             if line.startswith(("目标", "预算", "验收", "交付", "覆盖人数"))]
                    sections.append(path + "\n" + "\n".join(lines))
                title = "项目简报摘要" if summary else "采购方案比较"
                answer = title + "\n\n" + "\n\n".join(sections)
        else:
            answer = "离线演示包含三项示例任务。选择上方的任务，可查看本地文件读取、工具调用和报告保存。"

        usage = Usage(
            prompt=max(1, sum(len(str(item)) for item in messages) // 4),
            completion=max(1, len(answer or str(calls)) // 4),
        )
        return Completion(answer, calls, usage, "tool_calls" if calls else "stop")
