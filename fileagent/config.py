import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent


def env_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} 必须是 {minimum} 到 {maximum} 之间的整数。") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须是 {minimum} 到 {maximum} 之间的整数。")
    return value


@dataclass(frozen=True)
class Settings:
    workspace: Path
    outputs: Path
    examples: Path
    api_key: str = field(repr=False)
    model: str
    timeout: int
    retries: int

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(PROJECT_DIR / ".env", interpolate=False)
        custom_workspace = os.getenv("FILEAGENT_WORKSPACE", "").strip()
        workspace = Path(custom_workspace).expanduser() if custom_workspace else PROJECT_DIR / "workspace"
        if not workspace.is_absolute():
            workspace = PROJECT_DIR / workspace
        if not custom_workspace:
            workspace.mkdir(exist_ok=True, mode=0o700)
        outputs = PROJECT_DIR / "outputs"
        outputs.mkdir(exist_ok=True, mode=0o700)
        return cls(
            workspace=workspace,
            outputs=outputs,
            examples=PROJECT_DIR / "examples",
            api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            model=os.getenv("DEEPSEEK_MODEL", "").strip() or "deepseek-flash",
            timeout=env_integer("DEEPSEEK_TIMEOUT_SECONDS", 30, 1, 120),
            retries=env_integer("DEEPSEEK_MAX_RETRIES", 2, 0, 3),
        )
