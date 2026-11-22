import os
import re
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

SUPPORTED = frozenset({".pdf", ".docx", ".txt", ".md", ".csv"})
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PATH = 512
MAX_DEPTH = 12
MAX_DIRECTORY_ENTRIES = 1_000
MAX_REPORT_CHARS = 50_000
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
INVALID_PATH_CHAR = re.compile(r"[\\:\x00-\x1f\x7f]")


class FileError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def path_parts(value: str, *, directory: bool = False) -> tuple[str, ...]:
    if directory and value == ".":
        return ()
    parts = tuple(value.split("/"))
    if (
        len(value) > MAX_PATH
        or len(parts) > MAX_DEPTH
        or INVALID_PATH_CHAR.search(value)
        or any(not part or part.startswith(".") for part in parts)
    ):
        raise FileError("invalid_path", "请输入工作区内的相对路径。隐藏路径、绝对路径和目录回退均不可用。")
    return parts


@contextmanager
def open_directory(root: Path, parts: tuple[str, ...] = ()):
    # Each component is opened relative to an already-open directory.
    fd = os.open("/", DIRECTORY_FLAGS)
    try:
        for part in (*root.parts[1:], *parts):
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def checked_root(path: Path) -> Path:
    try:
        path = Path(os.path.abspath(path))
        root = path.parent.resolve(strict=True) / path.name
        with open_directory(root):
            return root
    except (OSError, RuntimeError):
        raise FileError("invalid_root", "工作区或输出目录不存在、不可访问或存在软链接循环。") from None


def same_or_below(path: Path, ancestor: Path) -> bool:
    try:
        target = ancestor.stat()
        return any(os.path.samestat(parent.stat(), target) for parent in (path, *path.parents))
    except OSError:
        raise FileError("invalid_root", "无法确认工作区和输出目录彼此独立。") from None


class Workspace:
    def __init__(self, root: Path):
        self.root = checked_root(root)

    def read(self, path: str) -> bytes:
        parts = path_parts(path)
        if PurePosixPath(parts[-1]).suffix.lower() not in SUPPORTED:
            raise FileError("unsupported_type", "仅支持 PDF、DOCX、TXT、MD 和 CSV。")
        try:
            with open_directory(self.root, parts[:-1]) as directory:
                original = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1:
                    raise FileError("not_regular", "只允许读取单链接普通文件。")
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                fd = os.open(parts[-1], flags, dir_fd=directory)
                with os.fdopen(fd, "rb") as file:
                    opened = os.fstat(file.fileno())
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or opened.st_nlink != 1
                        or (opened.st_dev, opened.st_ino) != (original.st_dev, original.st_ino)
                    ):
                        raise FileError("not_regular", "文件在读取前发生变化，或不是单链接普通文件。")
                    if opened.st_size > MAX_FILE_BYTES:
                        raise FileError("file_size", "文件超过 8 MiB 的读取上限。")
                    data = file.read(MAX_FILE_BYTES + 1)
        except OSError:
            raise FileError("unavailable", "文件不存在、不可读取或路径经过软链接。") from None
        if len(data) > MAX_FILE_BYTES:
            raise FileError("file_size", "文件超过 8 MiB 的读取上限。")
        return data

    def scan(self, directory: str) -> tuple[list[str], bool]:
        base = path_parts(directory, directory=True)
        pending = [base]
        files = []
        budget = MAX_DIRECTORY_ENTRIES
        limited = False
        while pending and budget:
            parts = pending.pop()
            child_dirs = []
            try:
                with open_directory(self.root, parts) as fd, os.scandir(fd) as entries:
                    for entry in entries:
                        if budget == 0:
                            limited = True
                            break
                        budget -= 1
                        child = (*parts, entry.name)
                        path = "/".join(child)
                        try:
                            path_parts(path)
                            metadata = entry.stat(follow_symlinks=False)
                        except (OSError, FileError):
                            continue
                        if stat.S_ISDIR(metadata.st_mode):
                            if len(child) < MAX_DEPTH:
                                child_dirs.append(child)
                            else:
                                limited = True
                        elif (
                            stat.S_ISREG(metadata.st_mode)
                            and metadata.st_nlink == 1
                            and PurePosixPath(path).suffix.lower() in SUPPORTED
                        ):
                            files.append(path)
            except OSError:
                if parts == base:
                    raise FileError("unavailable", "目录不存在、不可访问或路径经过软链接。") from None
                limited = True
            pending.extend(sorted(child_dirs, reverse=True))
        return sorted(files), limited or bool(pending)


class Reports:
    def __init__(self, outputs: Path, workspace: Workspace):
        self.root = checked_root(outputs)
        if same_or_below(self.root, workspace.root) or same_or_below(workspace.root, self.root):
            raise FileError("overlapping_roots", "工作区与输出目录必须彼此独立。")

    def save(self, filename: str, content: str) -> str:
        if (
            not re.fullmatch(r"[\w][\w .-]{0,94}\.(?:md|txt)", filename)
            or len(filename.encode("utf-8")) > 200
        ):
            raise FileError("report_name", "请输入以 .md 或 .txt 结尾的文件名，可含中文、字母、数字、空格、横线或下划线。")
        if not content.strip() or len(content) > MAX_REPORT_CHARS:
            raise FileError("report_content", "报告必须包含正文，且最多 50,000 个字符。")

        temporary = f".fileagent-{secrets.token_hex(16)}"
        try:
            with open_directory(self.root) as directory:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                fd = os.open(temporary, flags, 0o600, dir_fd=directory)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as file:
                        file.write(content)
                        file.flush()
                        os.fsync(file.fileno())
                    os.link(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory,
                            follow_symlinks=False)
                finally:
                    os.unlink(temporary, dir_fd=directory)
        except FileExistsError:
            raise FileError("report_exists", "同名报告已存在，请换一个文件名。") from None
        except OSError:
            raise FileError("report_write", "无法写入报告，请检查输出目录和可用空间。") from None
        return f"outputs/{filename}"
