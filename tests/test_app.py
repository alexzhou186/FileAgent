import stat
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from fileagent.backend import Completion, DeepSeekBackend, ModelError
from fileagent.config import Settings
from fileagent.credentials import KEY_FILENAME

PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    outputs = tmp_path / "outputs"
    workspace.mkdir()
    outputs.mkdir()
    settings = Settings(workspace, outputs, PROJECT / "examples", "", "deepseek-flash", 30, 0)
    monkeypatch.setattr(Settings, "load", lambda: settings)
    return settings


def start_app():
    return AppTest.from_file(PROJECT / "app.py", default_timeout=15).run()


@pytest.mark.parametrize("extension", [".md", ".txt"])
def test_demo_compare_and_save(settings, extension):
    outputs = settings.outputs
    app = start_app()
    app.radio(key="run_mode").set_value("离线演示").run()
    assert not app.exception
    app.button(key="比较两个采购方案").click().run()
    assert not app.exception
    assert len(app.chat_message) == 2
    page_text = "\n".join(item.value for item in app.text)
    assert "4,800" in page_text and "5,900" in page_text
    assert "模拟 Token" in page_text
    assert any("US$0" in item.value for item in app.caption)
    app.text_input[0].set_value("采购对比")
    next(radio for radio in app.radio if radio.label == "格式").set_value(extension)
    next(button for button in app.button if button.label == "保存到 outputs").click().run()
    assert not app.exception
    assert any("已保存" in item.value for item in app.success)
    saved = (outputs / f"采购对比{extension}").read_text()
    assert "plan-a.txt" in saved and "plan-b.docx" in saved

    next(button for button in app.button if button.label == "保存到 outputs").click().run()
    assert any("已存在" in item.value for item in app.error)


def submit_key(app, key, *, remember):
    app.text_input(key="deepseek_key_input").set_value(key)
    app.checkbox(key="deepseek_remember_input").set_value(remember)
    next(button for button in app.button if button.label == "验证并连接").click().run()
    assert not app.exception
    assert app.text_input(key="deepseek_key_input").value == ""


def test_connect_session_only_and_try_sample(settings, monkeypatch):
    requests = []

    def check(backend):
        assert backend.client.api_key == "sk-session-test"
        return True

    def complete(backend, messages, tools):
        assert backend.client.api_key == "sk-session-test"
        requests.append(messages)
        return Completion(text="示例运行成功")

    monkeypatch.setattr(DeepSeekBackend, "check_account", check)
    monkeypatch.setattr(DeepSeekBackend, "complete", complete)
    app = start_app()
    assert not app.exception
    assert app.text_input(key="deepseek_key_input").proto.type == 1
    assert not app.chat_input
    assert not app.checkbox(key="deepseek_remember_input").value

    submit_key(app, "sk-session-test", remember=False)
    assert any("已连接 DeepSeek" in message.value for message in app.success)
    assert not (settings.outputs.parent / KEY_FILENAME).exists()
    assert app.radio(key="deepseek_workspace").value == "示例文件"
    assert not requests
    app.button(key="比较两个采购方案").click().run()
    assert len(requests) == 1
    assert any(text.value == "示例运行成功" for text in app.text)

    fresh_browser = start_app()
    assert not fresh_browser.chat_input
    assert fresh_browser.text_input(key="deepseek_key_input").value == ""


def test_remember_across_sessions_and_clear(settings, monkeypatch):
    monkeypatch.setattr(DeepSeekBackend, "check_account", lambda backend: True)
    app = start_app()
    submit_key(app, "sk-saved-test", remember=True)
    key_file = settings.outputs.parent / KEY_FILENAME
    assert key_file.read_text() == "sk-saved-test\n"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    fresh_browser = start_app()
    assert not fresh_browser.exception
    assert fresh_browser.chat_input
    assert any("本机私有文件" in text.value for text in fresh_browser.caption)
    assert not fresh_browser.success

    next(button for button in fresh_browser.button if button.label == "清除本机密钥").click().run()
    assert not fresh_browser.exception
    assert not fresh_browser.chat_input
    assert not key_file.exists()
    assert any("已清除" in message.value for message in fresh_browser.success)

    app.run()
    assert not app.chat_input


def test_unverified_key_is_not_saved(settings, monkeypatch):
    def refuse(backend):
        raise ModelError("http_401", "DeepSeek 身份验证失败，请检查 API Key。")

    monkeypatch.setattr(DeepSeekBackend, "check_account", refuse)
    app = start_app()
    submit_key(app, "sk-rejected-test", remember=True)
    assert not app.chat_input
    assert any("身份验证失败" in error.value for error in app.error)
    assert not (settings.outputs.parent / KEY_FILENAME).exists()


def test_balance_warning_and_recheck(settings, monkeypatch):
    checks = iter((False, True))
    monkeypatch.setattr(DeepSeekBackend, "check_account", lambda backend: next(checks))
    app = start_app()
    submit_key(app, "sk-balance-test", remember=False)
    assert not app.chat_input
    assert any("账户余额不足" in warning.value for warning in app.warning)
    next(button for button in app.button if button.label == "重新验证连接").click().run()
    assert not app.exception
    assert app.chat_input
    assert any("DeepSeek 已连接" in message.value for message in app.success)
