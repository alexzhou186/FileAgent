from contextlib import closing
from dataclasses import replace
from hashlib import sha256

import streamlit as st

from fileagent.backend import DeepSeekBackend, ModelError
from fileagent.config import Settings
from fileagent.credentials import MAX_KEY_LENGTH, KeyStoreError, LocalKeyStore, valid_api_key

KEY_INPUT = "deepseek_key_input"
REMEMBER_INPUT = "deepseek_remember_input"
PENDING_KEY = "deepseek_pending_key"
SESSION_KEY = "deepseek_session_key"
ACCOUNT_CHECK = "deepseek_account_check"
LIVE_WORKSPACE = "deepseek_workspace"
MY_WORKSPACE = "我的工作区"
SAMPLE_WORKSPACE = "示例文件"


def key_id(key: str) -> str:
    return sha256(key.encode()).hexdigest()


def submit_key():
    state = st.session_state
    state[PENDING_KEY] = (state.pop(KEY_INPUT, "").strip(), state[REMEMBER_INPUT])


def show_key_form():
    with st.form("deepseek_connection", clear_on_submit=True):
        st.text_input("DeepSeek API Key", type="password", key=KEY_INPUT, max_chars=MAX_KEY_LENGTH)
        st.checkbox("记住在本机", key=REMEMBER_INPUT, help="保存到项目里的私有文件，下次打开可直接使用。")
        st.form_submit_button("验证并连接", type="primary", on_click=submit_key)


def verify(settings: Settings, key: str) -> bool:
    if not valid_api_key(key):
        raise ModelError("invalid_key_format", "请输入完整的 DeepSeek API Key。")
    with st.spinner("正在验证 DeepSeek 密钥…"):
        with closing(DeepSeekBackend(replace(settings, api_key=key))) as client:
            return client.check_account()


def connect_deepseek(settings: Settings) -> str:
    state = st.session_state
    store = LocalKeyStore(settings.outputs.parent)
    storage_error = None
    try:
        saved_key = store.load()
    except KeyStoreError as error:
        saved_key = ""
        storage_error = str(error)
        st.sidebar.warning(storage_error)

    session_key = state.get(SESSION_KEY, "")
    if session_key or saved_key or storage_error:
        if st.sidebar.button("清除本机密钥", help="断开当前会话并删除 FileAgent 记住的密钥。"):
            try:
                store.clear()
            except KeyStoreError as error:
                st.error(str(error))
            else:
                state.pop(SESSION_KEY, None)
                state.pop(ACCOUNT_CHECK, None)
                saved_key = session_key = ""
                st.success("已清除 FileAgent 保存的密钥。")
                if settings.api_key:
                    st.info("环境变量或 .env 中仍有密钥。如需移除，请在原位置清除后重启。")

    submission = state.pop(PENDING_KEY, None)
    if submission is not None:
        key, remember = submission
        try:
            available = verify(settings, key)
        except ModelError as error:
            st.error(str(error))
        else:
            if not (session_key or saved_key or settings.api_key):
                state[LIVE_WORKSPACE] = SAMPLE_WORKSPACE
            state[ACCOUNT_CHECK] = (key_id(key), available)
            session_key = key
            if remember:
                try:
                    store.save(key)
                except KeyStoreError as error:
                    st.warning(f"{error} 密钥仍可在当前会话使用。")
                else:
                    saved_key = key
                    session_key = ""
            if session_key:
                state[SESSION_KEY] = session_key
            else:
                state.pop(SESSION_KEY, None)
            if available:
                st.success("已连接 DeepSeek，可以开始试用。")

    key = session_key or saved_key or settings.api_key
    if not key:
        st.subheader("连接 DeepSeek")
        st.write("输入你的 API Key，然后验证连接。只想先看界面，可以在侧栏切换到离线演示。")
        show_key_form()
        return ""

    source = "本次会话" if session_key else "本机私有文件" if saved_key else "环境变量或 .env"
    st.sidebar.caption(f"密钥来源 · {source}")
    if st.sidebar.button("重新验证连接"):
        try:
            available = verify(settings, key)
        except ModelError as error:
            state.pop(ACCOUNT_CHECK, None)
            st.error(str(error))
        else:
            state[ACCOUNT_CHECK] = (key_id(key), available)

    check = state.get(ACCOUNT_CHECK)
    available = check[1] if check and check[0] == key_id(key) else None
    if available is True:
        st.sidebar.success("DeepSeek 已连接")
    elif available is False:
        st.warning("密钥验证通过，DeepSeek 账户余额不足。充值后可重新验证连接。")
    else:
        st.sidebar.caption("已载入密钥，可重新验证连接。")

    with st.sidebar.expander("更换密钥"):
        show_key_form()

    return key if available is not False else ""
