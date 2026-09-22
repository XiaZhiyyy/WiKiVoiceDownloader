import json
from pathlib import Path
import pytest
from wiki_voice_downloader.config import Config, load_config, output_paths, application_dir
from wiki_voice_downloader.errors import ConfigError, StorageError
from wiki_voice_downloader.storage import safe_name, validate_directory_path

@pytest.mark.parametrize("values", [
    {"max_attempts": True}, {"max_attempts": 0}, {"max_attempts": 21},
    {"max_attempts": 1.2}, {"connect_timeout_seconds": 0},
    {"read_timeout_seconds": "30"}, {"max_file_size_mb": -1},
    {"proxy": "socks5://host:1"}, {"log_level": "info"},
    {"download_dir": ""}, {"log_dir": 3}, {"retry_backoff_seconds": float("nan")},
])
def test_config_invalid_values(values):
    with pytest.raises(ConfigError):
        Config(**values).validate()

def test_defaults_no_config_file_generated_and_no_cwd_drift(tmp_path, monkeypatch):
    app = tmp_path / "中文 应用"
    app.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    config, base = load_config(app_dir=app)
    assert base == app
    assert output_paths(config, base) == (app / "downloads", app / "logs")
    assert not (app / "config.json").exists()

def test_explicit_config_bom_and_relative_paths(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    config_dir = tmp_path / "独立 配置"
    config_dir.mkdir()
    file = config_dir / "settings.json"
    file.write_text(json.dumps({"download_dir": "素材", "log_dir": "日志"}, ensure_ascii=False), encoding="utf-8-sig")
    config, base = load_config(str(file), app)
    assert output_paths(config, base) == (config_dir / "素材", config_dir / "日志")

def test_relative_config_argument_resolves_against_app(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text('{"download_dir":"output"}', encoding="utf-8")
    cfg, base = load_config("configs/settings.json", tmp_path)
    assert base == config_dir
    assert output_paths(cfg, base)[0] == config_dir / "output"

def test_unknown_config_key_is_error(tmp_path):
    (tmp_path / "config.json").write_text('{"max_workers":3}', encoding="utf-8")
    with pytest.raises(ConfigError, match="max_workers"):
        load_config(app_dir=tmp_path)

def test_missing_explicit_config_does_not_fall_back(tmp_path):
    with pytest.raises(ConfigError):
        load_config("missing.json", tmp_path)

def test_exe_base_not_meipass(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "程序 文件" / "WikiVoiceDownloader.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", "/temporary/extraction", raising=False)
    assert application_dir() == tmp_path / "程序 文件"

def test_long_windows_path_clear_error(tmp_path):
    with pytest.raises(StorageError, match="路径过长"):
        validate_directory_path(tmp_path / ("a" * 230))

def test_unicode_safe_name_preserves_chinese_and_japanese():
    assert safe_name("信浓 しなの") == "信浓 しなの"
