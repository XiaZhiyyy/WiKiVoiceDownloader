"""Static checks only: these do NOT claim Windows BAT or EXE execution."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def test_bat_files_are_ascii_crlf_and_preserve_user_data():
    for name in ("setup.bat", "start.bat", "build_exe.bat"):
        data = (ROOT / name).read_bytes()
        data.decode("ascii")
        assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
        assert b"DisableDelayedExpansion" in data
        assert b"rmdir" not in data.lower() and b"del /" not in data.lower()
    start = (ROOT / "start.bat").read_text()
    assert "%*" in start and 'set "RESULT=%ERRORLEVEL%"' in start
    build = (ROOT / "build_exe.bat").read_text()
    assert 'if exist "dist\\WikiVoiceDownloader"' in build

def test_spec_is_syntactically_valid_and_console_onedir():
    text = (ROOT / "WikiVoiceDownloader.spec").read_text(encoding="utf-8")
    compile(text, "WikiVoiceDownloader.spec", "exec")
    assert "console=True" in text and "COLLECT(" in text and "exclude_binaries=True" in text
