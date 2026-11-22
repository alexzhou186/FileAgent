import logging
from contextlib import closing, nullcontext
from dataclasses import replace

import streamlit as st

from fileagent.agent import MAX_PROMPT_CHARS, Agent, Event, Turn
from fileagent.backend import DeepSeekBackend
from fileagent.config import Settings
from fileagent.connection_ui import LIVE_WORKSPACE, MY_WORKSPACE, SAMPLE_WORKSPACE, connect_deepseek, key_id
from fileagent.demo import DEMO_PROMPTS, DemoBackend
from fileagent.filesystem import FileError, Reports, Workspace
from fileagent.tools import FileTools

OFFLINE = "离线演示"
LIVE = "DeepSeek"
LABELS = {
    "model": "模型请求",
    "list_files": "列出文件",
    "read_file": "读取文件",
    "search_files": "搜索文件",
    "agent": "调用循环",
    "unknown_tool": "未允许的工具",
}


def event_line(event: Event) -> str:
    action = LABELS[event.action]
    status = {"ok": "完成", "stop": "返回答复", "tool_calls": "返回工具调用"}.get(event.code, event.code)
    return f"第 {event.step} 步 · {action} · {status} · {event.elapsed_ms} ms"


def render_details(turn: Turn, *, offline: bool, model: str):
    with st.expander("调用记录与费用"):
        st.caption(f"{turn.model_requests} 次模型请求 · {turn.tool_calls} 次工具调用")
        for event in turn.events:
            st.text(event_line(event))
            if event.detail:
                st.text(event.detail)
        if turn.usage_responses == 0:
            st.caption("没有可用的 Token 用量记录。")
            return
        usage = turn.usage
        prefix = "模拟 Token" if offline else "API 返回的 Token"
        st.text(f"{prefix} · 输入 {usage.prompt:,}（缓存命中 {usage.cached:,}）"
                f" · 输出 {usage.completion:,} · 合计 {usage.prompt + usage.completion:,}")
        if offline:
            st.caption("费用 US$0 · 离线演示")
        else:
            cost = usage.peak_usd(model)
            if cost is None:
                st.caption("当前模型暂无费率，无法估算费用。")
            else:
                st.caption(f"费用估算 US${cost:.6f} · 按高峰费率计算，以 DeepSeek 账单为准。")
            if turn.usage_responses < turn.model_requests:
                st.caption("部分请求未返回用量，费用只覆盖有记录的响应。")


def render_turn(prompt: str, turn: Turn, *, offline: bool, model: str):
    with st.chat_message("user"):
        st.text(prompt)
    with st.chat_message("assistant"):
        if turn.failed:
            st.error(turn.answer)
        else:
            st.text(turn.answer)
        render_details(turn, offline=offline, model=model)


def render_save(reports: Reports, content: str, turn_number: int):
    with st.expander("保存报告"):
        with st.form(f"report-form-{turn_number}"):
            name = st.text_input("报告名称", value=f"report-{turn_number}", max_chars=90)
            extension = st.radio("格式", (".md", ".txt"), horizontal=True)
            save = st.form_submit_button("保存到 outputs")
        if save:
            try:
                result = reports.save(f"{name.strip()}{extension}", content)
            except FileError as error:
                st.error(str(error))
            else:
                st.success(f"已保存 {result}")


def main():
    st.set_page_config(page_title="FileAgent", layout="centered")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("fileagent").setLevel(logging.INFO)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    st.title("FileAgent")
    st.caption("在本机工作区里查找、总结和比较文档。")

    try:
        settings = Settings.load()
    except (OSError, ValueError) as error:
        st.error(str(error) if isinstance(error, ValueError) else "无法创建工作区或输出目录，请检查文件夹权限。")
        return

    mode = st.sidebar.radio("运行模式", [LIVE, OFFLINE], key="run_mode")
    offline = mode == OFFLINE
    if not offline:
        key = connect_deepseek(settings)
        if not key:
            return
        settings = replace(settings, api_key=key)

    with st.sidebar:
        if offline:
            use_examples = True
            st.caption("使用随项目提供的示例文件。")
        else:
            st.caption(f"模型 {settings.model}")
            choice = st.radio("文件来源", [MY_WORKSPACE, SAMPLE_WORKSPACE], key=LIVE_WORKSPACE)
            use_examples = choice == SAMPLE_WORKSPACE
            st.info("聊天消息、文件路径及工具读取的片段会发给 DeepSeek。")
        try:
            workspace = Workspace(settings.examples if use_examples else settings.workspace)
            reports = Reports(settings.outputs, workspace)
        except FileError as error:
            st.error(str(error))
            return
        st.caption("当前工作区")
        st.code(str(workspace.root), language=None)
        st.caption("支持 PDF、DOCX、TXT、MD、CSV")

    states = st.session_state.setdefault("conversations", {})
    conversation_id = (mode, str(workspace.root), settings.model, "" if offline else key_id(settings.api_key))
    state = states.setdefault(conversation_id, {"turns": [], "number": 0})
    turns = state["turns"]
    if st.sidebar.button("清空对话", disabled=not turns):
        turns.clear()
        st.rerun()

    selected = None
    if use_examples:
        for column, (label, prompt) in zip(st.columns(3), DEMO_PROMPTS.items()):
            if column.button(label, key=label):
                selected = prompt
    elif not turns:
        st.caption("例如，输入「列出工作区文件」或「比较这两份文件的预算和交付时间」。")

    for prompt, turn in turns:
        render_turn(prompt, turn, offline=offline, model=settings.model)

    typed = st.chat_input("输入你想了解的文件或问题", max_chars=MAX_PROMPT_CHARS)
    prompt = (selected or typed or "").strip()
    if prompt:
        history = [(question, turn.answer) for question, turn in turns if not turn.failed]
        with st.chat_message("user"):
            st.text(prompt)
        with st.chat_message("assistant"):
            with st.status("正在处理…", expanded=False) as progress:
                def report_event(event: Event):
                    st.text(event_line(event))

                session = nullcontext(DemoBackend()) if offline else closing(DeepSeekBackend(settings))
                with session as backend:
                    turn = Agent(backend, FileTools(workspace)).run(prompt, history, report_event)
                progress.update(label="处理未完成" if turn.failed else "处理完成",
                                state="error" if turn.failed else "complete")
            if turn.failed:
                st.error(turn.answer)
            else:
                st.text(turn.answer)
            render_details(turn, offline=offline, model=settings.model)
        turns.append((prompt, turn))
        del turns[:-30]
        state["number"] += 1

    if turns and not turns[-1][1].failed:
        render_save(reports, turns[-1][1].answer, state["number"])


if __name__ == "__main__":
    main()
