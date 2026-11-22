import json
import re

from fileagent.documents import MAX_EXTRACTED_CHARS, extract
from fileagent.filesystem import MAX_DIRECTORY_ENTRIES, MAX_PATH, FileError, Workspace

MAX_READ_CHARS = 12_000
MAX_SEARCH_FILES = 40
MAX_ARGUMENT_CHARS = 4_096


def string_param(description: str, max_length: int) -> dict:
    return {"type": "string", "description": description, "minLength": 1, "maxLength": max_length}


def int_param(description: str, minimum: int, maximum: int) -> dict:
    return {"type": "integer", "description": description, "minimum": minimum, "maximum": maximum}


DIRECTORY = string_param("Workspace-relative directory. Use '.' for the root.", MAX_PATH)
TOOL_PARAMETERS = {
    "list_files": (
        "Recursively list supported files in a Workspace directory.",
        {
            "directory": DIRECTORY,
            "offset": int_param("Start at this zero-based file index.", 0, MAX_DIRECTORY_ENTRIES),
            "limit": int_param("Number of file paths to return. Defaults to 30.", 1, 50),
        },
        [],
    ),
    "read_file": (
        "Read extracted document text. Use next_offset to continue and cite the relative path.",
        {
            "path": string_param("Workspace-relative filename as listed by list_files.", MAX_PATH),
            "offset": int_param("Start at this zero-based character offset.", 0, MAX_EXTRACTED_CHARS),
            "max_chars": int_param("Max characters to return. Defaults to 6,000.", 1, MAX_READ_CHARS),
        },
        ["path"],
    ),
    "search_files": (
        "Search for a literal phrase in extracted Workspace text. Results show short matching excerpts.",
        {
            "query": string_param("Literal text to find, case-insensitive.", 100),
            "directory": DIRECTORY,
            "limit": int_param("Max excerpts to return. Defaults to 10.", 1, 20),
            "file_offset": int_param("Start scanning at this zero-based file index.", 0, MAX_DIRECTORY_ENTRIES),
        },
        ["query"],
    ),
}
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }
    for name, (description, properties, required) in TOOL_PARAMETERS.items()
]


def unique_keys(pairs: list[tuple]) -> dict:
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ValueError("Duplicate key.")
    return values


def validate_arguments(name: str, raw: str) -> dict:
    if len(raw) > MAX_ARGUMENT_CHARS:
        raise FileError("invalid_arguments", "工具参数过长。")
    try:
        arguments = json.loads(raw, object_pairs_hook=unique_keys)
    except (ValueError, RecursionError):
        raise FileError("invalid_arguments", "工具参数必须是有效的 JSON 对象，且键名不可重复。") from None
    _, fields, required = TOOL_PARAMETERS[name]
    if not isinstance(arguments, dict) or set(arguments) - fields.keys():
        raise FileError("invalid_arguments", "工具参数须为对象，且仅可包含声明的字段。")
    if set(required) - arguments.keys():
        raise FileError("invalid_arguments", "工具参数缺少必填字段。")
    for key, value in arguments.items():
        field = fields[key]
        if field["type"] == "string":
            valid = isinstance(value, str) and bool(value.strip()) and len(value) <= field["maxLength"]
        else:
            valid = type(value) is int and field["minimum"] <= value <= field["maximum"]
        if not valid:
            raise FileError("invalid_arguments", f"参数 {key} 的类型或范围不正确。")
    return arguments


class FileTools:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.handlers = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "search_files": self.search_files,
        }

    def invoke(self, name: str, raw_arguments: str) -> dict:
        handler = self.handlers.get(name)
        if handler is None:
            raise FileError("unknown_tool", "工具不在允许列表中。")
        return handler(**validate_arguments(name, raw_arguments))

    def list_files(self, directory: str = ".", offset: int = 0, limit: int = 30) -> dict:
        paths, partial_scan = self.workspace.scan(directory)
        end = offset + limit
        return {
            "paths": paths[offset:end],
            "total_found": len(paths),
            "next_offset": end if end < len(paths) else None,
            "partial_scan": partial_scan,
        }

    def read_file(self, path: str, offset: int = 0, max_chars: int = 6_000) -> dict:
        result = extract(self.workspace, path)
        if offset > len(result.text):
            raise FileError("offset", "读取起点超出已提取文字的长度。")
        end = offset + max_chars
        return {
            "path": path,
            "offset": offset,
            "text": result.text[offset:end],
            "next_offset": end if end < len(result.text) else None,
            "notices": result.notices,
        }

    def search_files(self, query: str, directory: str = ".", limit: int = 10, file_offset: int = 0) -> dict:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        paths, partial_scan = self.workspace.scan(directory)
        matches = []
        scanned_files = skipped_files = partial_files = 0
        matches_clipped = False
        for path in paths[file_offset:file_offset + MAX_SEARCH_FILES]:
            scanned_files += 1
            try:
                result = extract(self.workspace, path)
            except FileError:
                skipped_files += 1
                continue
            partial_files += int(result.partial)
            for number, line in enumerate(result.text.splitlines(), start=1):
                match = pattern.search(line)
                if match:
                    if len(matches) == limit:
                        matches_clipped = True
                        break
                    start = max(0, match.start() - 45)
                    matches.append({"path": path, "line": number, "excerpt": line[start:start + 200]})
            if len(matches) == limit:
                break
        end = file_offset + scanned_files
        return {
            "matches": matches,
            "scanned_files": scanned_files,
            "skipped_files": skipped_files,
            "partial_files": partial_files,
            "partial_scan": partial_scan,
            "matches_clipped": matches_clipped,
            "next_file_offset": end if end < len(paths) else None,
        }
