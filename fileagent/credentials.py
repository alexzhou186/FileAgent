import os
import re
import stat
import tempfile
from pathlib import Path

KEY_FILENAME = ".fileagent-key"
MAX_KEY_LENGTH = 512
KEY_PATTERN = re.compile(r"[a-zA-Z0-9._-]{1,512}")


class KeyStoreError(Exception):
    pass


def valid_api_key(key: str) -> bool:
    return KEY_PATTERN.fullmatch(key) is not None


class LocalKeyStore:
    """Store the optional browser-managed key in the project directory."""

    def __init__(self, project: Path):
        self.path = project / KEY_FILENAME

    def load(self) -> str:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as file:
                info = os.fstat(file.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_nlink != 1
                    or info.st_mode & 0o077
                ):
                    raise KeyStoreError("本机密钥文件必须是当前用户独享的普通文件。")
                data = file.read(MAX_KEY_LENGTH + 2)
        except FileNotFoundError:
            return ""
        except OSError:
            raise KeyStoreError("无法读取本机密钥文件。") from None

        try:
            key = data.removesuffix(b"\n").decode("ascii")
        except UnicodeError:
            raise KeyStoreError("本机密钥文件格式有误。") from None
        if not valid_api_key(key):
            raise KeyStoreError("本机密钥文件格式有误。")
        return key

    def save(self, key: str):
        if not valid_api_key(key):
            raise KeyStoreError("密钥格式有误。")
        self.load()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent, prefix=f"{KEY_FILENAME}.", mode="w", encoding="ascii", delete=False
            ) as file:
                temporary = Path(file.name)
                file.write(key + "\n")
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(self.path)
        except OSError:
            raise KeyStoreError("无法保存密钥，请检查项目目录的写入权限。") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def clear(self):
        try:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise KeyStoreError("无法清除不属于 FileAgent 的密钥文件。")
            self.path.unlink(missing_ok=True)
        except FileNotFoundError:
            return
        except OSError:
            raise KeyStoreError("无法清除本机密钥，请检查项目目录权限。") from None
