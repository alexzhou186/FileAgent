import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from fileagent.filesystem import FileError, Reports, Workspace
from fileagent.tools import FileTools

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "safe.md").write_text("测试预算预算\nNext", encoding="utf-8")
    return Workspace(root)


@pytest.mark.parametrize(
    ("file", "phrase"),
    [
        ("approval.pdf", "Budget ceiling"),
        ("brief.md", "人民币 6,000"),
        ("plan-a.txt", "人民币 4,800"),
        ("plan-b.docx", "人民币 5,900"),
        ("budget.csv", "笔记本支架"),
    ],
)
def test_read_example(file, phrase):
    tools = FileTools(Workspace(EXAMPLES))
    result = tools.invoke("read_file", json.dumps({"path": file}))
    assert phrase in result["text"]
    assert result["next_offset"] is None


def test_search_and_list(workspace):
    subdir = workspace.root / "sub"
    subdir.mkdir()
    (subdir / "plan.csv").write_text("item,cost\nStand,120\n", encoding="utf-8-sig")
    (subdir / "broken.pdf").write_text("invalid pdf")
    (workspace.root / ".internal.txt").write_text("private")
    (workspace.root / "script.py").write_text("not supported")

    tools = FileTools(workspace)
    result = tools.invoke("list_files", '{"limit":2}')
    assert result["paths"] == ["safe.md", "sub/broken.pdf"]
    assert result["next_offset"] == 2
    assert tools.invoke("list_files", '{"offset":2}')["paths"] == ["sub/plan.csv"]
    result = tools.invoke("search_files", '{"query":"stand","directory":"sub"}')
    assert result["matches"] == [{"path": "sub/plan.csv", "line": 2, "excerpt": "Stand,120"}]
    assert result["skipped_files"] == 1
    result = tools.invoke("read_file", '{"path":"safe.md","max_chars":2}')
    assert result["text"] == "测试"
    assert result["next_offset"] == 2
    assert tools.invoke("read_file", '{"path":"safe.md","offset":2}')["text"] == "预算预算\nNext"


@pytest.mark.parametrize("path", ["../outside.txt", "/tmp/outside.txt", "x/../../safe.md", "x//safe.md",
                                  "C:\\outside.txt", ".env", "safe.md\x00", "safe.md/"])
def test_reject_unsafe_paths(workspace, path):
    with pytest.raises(FileError, match="相对路径"):
        workspace.read(path)


def test_links_and_special_files_are_rejected(workspace, tmp_path):
    private = tmp_path / "private.txt"
    private.write_text("outside")
    (workspace.root / "outside.txt").symlink_to(private)
    (workspace.root / "outside-dir").symlink_to(tmp_path, target_is_directory=True)
    os.link(private, workspace.root / "hard.txt")
    os.mkfifo(workspace.root / "pipe.txt")

    for name in ("outside.txt", "outside-dir/private.txt", "hard.txt", "pipe.txt"):
        with pytest.raises(FileError):
            workspace.read(name)
    assert workspace.scan(".") == (["safe.md"], False)
    with pytest.raises(FileError):
        workspace.scan("outside-dir")
    with pytest.raises(FileError):
        Workspace(workspace.root / "outside-dir")


@pytest.mark.parametrize(("name", "raw"), [
    ("read_file", "invalid"),
    ("read_file", "[]"),
    ("read_file", '{"path":"safe.md","path":"a.md"}'),
    ("read_file", '{"path":"safe.md","extra":true}'),
    ("read_file", '{"path":"safe.md","offset":true}'),
    ("read_file", '{"path":"safe.md","max_chars":12001}'),
    ("read_file", '{"path":""}'),
    ("read_file", '{}'),
    ("read_file", '{"path":"safe.md","offset":1.0}'),
    ("search_files", '{"query":" "}'),
])
def test_argument_validation(workspace, name, raw):
    with pytest.raises(FileError) as error:
        FileTools(workspace).invoke(name, raw)
    assert error.value.code == "invalid_arguments"


def test_report_is_atomic_and_never_replaces(workspace, tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    reports = Reports(output_dir, workspace)

    def save(content):
        try:
            return reports.save("项目总结.md", content)
        except FileError as error:
            return error.code

    choices = ["# 第一份\n完整内容", "# 第二份\n完整内容"]
    with ThreadPoolExecutor(max_workers=2) as threads:
        results = list(threads.map(save, choices))
    assert sorted(results) == ["outputs/项目总结.md", "report_exists"]
    assert (output_dir / "项目总结.md").read_text() in choices
    assert len(list(output_dir.iterdir())) == 1

    outside = tmp_path / "existing.txt"
    outside.write_text("original")
    (output_dir / "alias.txt").symlink_to(outside)
    with pytest.raises(FileError) as error:
        reports.save("alias.txt", "replacement")
    assert error.value.code == "report_exists"
    assert outside.read_text() == "original"
    with pytest.raises(FileError):
        reports.save("../outside.txt", "bad")
    with pytest.raises(FileError):
        Reports(workspace.root, workspace)


def test_report_roots_must_be_independent_on_macos(workspace, tmp_path):
    with pytest.raises(FileError) as error:
        Reports(tmp_path, workspace)
    assert error.value.code == "overlapping_roots"
    inside = workspace.root / "nested-output"
    inside.mkdir()
    with pytest.raises(FileError) as error:
        Reports(inside, workspace)
    assert error.value.code == "overlapping_roots"
    case_alias = workspace.root.with_name(workspace.root.name.upper())
    if case_alias.is_dir():
        with pytest.raises(FileError) as error:
            Reports(case_alias, workspace)
        assert error.value.code == "overlapping_roots"
