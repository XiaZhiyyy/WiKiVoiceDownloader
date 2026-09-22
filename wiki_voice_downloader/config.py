from __future__ import annotations
import json
import math
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit
from .errors import ConfigError

@dataclass(frozen=True)
class Config:
    download_dir: str = "downloads"
    log_dir: str = "logs"
    connect_timeout_seconds: float = 10
    read_timeout_seconds: float = 30
    max_attempts: int = 3
    retry_backoff_seconds: float = 1
    retry_backoff_max_seconds: float = 30
    max_retry_after_seconds: float = 300
    max_file_size_mb: float = 256
    max_page_size_mb: float = 16
    proxy: str | None = None
    log_level: str = "INFO"
    site_profile_dir: str | None = None

    def validate(self) -> None:
        if self.site_profile_dir is not None and (
            not isinstance(self.site_profile_dir, str) or not self.site_profile_dir.strip()
            or any(ord(c) < 32 for c in self.site_profile_dir)
        ):
            raise ConfigError("site_profile_dir: expected a local directory path or null")
        for name in ("download_dir", "log_dir"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise ConfigError(f"{name}: 必须是非空有效路径。")
        for name in ("connect_timeout_seconds", "read_timeout_seconds",
                     "retry_backoff_seconds", "retry_backoff_max_seconds",
                     "max_retry_after_seconds", "max_file_size_mb", "max_page_size_mb"):
            value = getattr(self, name)
            allow_zero = name.startswith("retry_") or name == "max_retry_after_seconds"
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0 or (not allow_zero and value == 0)):
                raise ConfigError(f"{name}: 数值范围无效。")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 20:
            raise ConfigError("max_attempts: 必须是 1 至 20 的整数（总尝试次数）。")
        if not isinstance(self.log_level, str) or self.log_level not in {
            "DEBUG", "INFO", "WARNING", "ERROR"
        }:
            raise ConfigError("log_level: 使用 DEBUG / INFO / WARNING / ERROR。")
        if self.proxy is not None:
            try:
                if not isinstance(self.proxy, str) or any(c.isspace() for c in self.proxy):
                    raise ValueError
                p = urlsplit(self.proxy)
                if (p.scheme not in {"http", "https"} or not p.hostname
                    or p.path not in {"", "/"} or p.query or p.fragment
                    or "\\" in self.proxy):
                    raise ValueError
                _ = p.port
            except (ValueError, TypeError):
                raise ConfigError("proxy: 使用合法 HTTP/HTTPS 代理地址或 null；不支持 SOCKS。") from None

def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent

def load_config(config_arg: str | None = None, app_dir: Path | None = None
                ) -> tuple[Config, Path]:
    app = app_dir or application_dir()
    path = Path(config_arg) if config_arg else app / "config.json"
    if not path.is_absolute():
        path = app / path
    if not path.exists():
        if config_arg:
            raise ConfigError(f"找不到配置文件：{path}")
        values = {}
    else:
        try:
            values = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"配置文件读取失败：{path.name}（{type(exc).__name__}）") from None
    if not isinstance(values, dict):
        raise ConfigError("配置顶层必须是 JSON 对象。")
    unknown = set(values) - {f.name for f in fields(Config)}
    if unknown:
        raise ConfigError("未知配置键：" + ", ".join(sorted(unknown)))
    config = Config(**values)
    config.validate()
    base = path.resolve().parent if config_arg else app.resolve()
    return config, base

def output_paths(config: Config, base: Path) -> tuple[Path, Path]:
    def path(value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else base / p
    return path(config.download_dir), path(config.log_dir)
