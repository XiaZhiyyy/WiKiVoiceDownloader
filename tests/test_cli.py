import copy
import json
from pathlib import Path
import subprocess
import sys
import pytest
from wiki_voice_downloader.cli import main
from wiki_voice_downloader.http_client import HTTPClient
from wiki_voice_downloader.storage import read_metadata
from .helpers import Session, Response, PAGE_URL, run_selection, url

def client_factory(html, routes=None):
    def factory(config):
        session = Session({PAGE_URL: [Response(html.encode("utf-8"), headers={"Content-Type": "text/html; charset=utf-8"})],
                           **(routes or {})})
        return HTTPClient(config, session=session, sleeper=lambda _: None)
    return factory

def answers(*values):
    iterator = iter(values)
    def ask(prompt):
        try:
            return next(iterator)
        except StopIteration:
            raise EOFError
    return ask

def snapshots(directory):
    return {p.relative_to(directory): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in directory.rglob("*") if p.is_file()}

def test_no_argument_interactive_start(tmp_path, html):
    output = []
    result = main([], input_fn=answers(PAGE_URL, "1,2"), output_fn=output.append,
                  client_factory=client_factory(html), app_dir=tmp_path)
    assert result == 0
    directory = tmp_path / "downloads" / "离线测试角色"
    assert len(read_metadata(directory)["items"]) == 4
    assert any("本轮全部成功" in line for line in output)
    assert len(list((tmp_path / "logs").glob("*_离线测试角色.log"))) == 1

def test_selection_invalid_reprompt_and_not_all(tmp_path, html):
    result = main([PAGE_URL], input_fn=answers("", "9", "1"), output_fn=lambda _: None,
                  client_factory=client_factory(html), app_dir=tmp_path)
    assert result == 0
    assert len(read_metadata(tmp_path / "downloads" / "离线测试角色")["items"]) == 3

def test_R09_cancel_before_recovery_no_index_audio_or_txt_mutation(tmp_path, html, page):
    directory = tmp_path / "downloads" / "离线测试角色"
    run_selection(directory, page, [0])
    # Give recovery actual work; cancellation must NOT do it.
    (directory / "audio" / "001.mp3").unlink()
    (directory / "audio" / "002.mp3.part").write_bytes(b"unfinished")
    before = snapshots(directory)
    code = main([PAGE_URL], input_fn=answers("A", "3"), output_fn=lambda _: None,
                client_factory=client_factory(html), app_dir=tmp_path)
    assert code == 0 and snapshots(directory) == before

def test_overwrite_confirmation_cancel_does_not_mutate(tmp_path, html, page):
    directory = tmp_path / "downloads" / "离线测试角色"
    run_selection(directory, page, [0])
    before = snapshots(directory)
    code = main([PAGE_URL], input_fn=answers("1", "2", "NO"), output_fn=lambda _: None,
                client_factory=client_factory(html), app_dir=tmp_path)
    assert code == 0 and snapshots(directory) == before

def test_dry_run_creates_no_output_or_logs(tmp_path, html):
    code = main([PAGE_URL, "--dry-run"], input_fn=answers("A"), output_fn=lambda _: None,
                client_factory=client_factory(html), app_dir=tmp_path)
    assert code == 0 and list(tmp_path.iterdir()) == []

def test_dry_run_existing_pending_state_unchanged(tmp_path, html, page):
    directory = tmp_path / "downloads" / "离线测试角色"
    run_selection(directory, page, [0], {url("B"): [Response(status=404)]})
    before = snapshots(directory)
    code = main([PAGE_URL, "--dry-run"], input_fn=answers("1", "1"), output_fn=lambda _: None,
                client_factory=client_factory(html), app_dir=tmp_path)
    assert code == 0 and before == snapshots(directory)

def test_eof_returns_error_without_loop(tmp_path):
    output = []
    code = main([], input_fn=answers(), output_fn=output.append, app_dir=tmp_path)
    assert code == 1 and any("输入已结束" in line for line in output)

def test_ctrl_c_exit_130(tmp_path):
    def interrupt(prompt):
        raise KeyboardInterrupt
    assert main([], input_fn=interrupt, output_fn=lambda _: None, app_dir=tmp_path) == 130

def test_mp3_input_is_not_treated_as_html(tmp_path):
    output = []
    code = main([url("A")], input_fn=answers(), output_fn=output.append, app_dir=tmp_path)
    assert code == 1 and any("音频地址" in line for line in output)

def test_partial_failure_exit_2_and_summary(tmp_path, html):
    output = []
    code = main([PAGE_URL], input_fn=answers("1"), output_fn=output.append,
                client_factory=client_factory(html, {url("B"): [Response(status=404)]}),
                app_dir=tmp_path)
    assert code == 2
    assert any("部分失败" in line for line in output)
    assert any("002.mp3" in line and "404" in line for line in output)

def test_manual_parser_still_rejects_unsupported_host(tmp_path):
    code = main(["https://example.com/character", "--parser", "biligame_blhx"],
                input_fn=answers(), output_fn=lambda _: None, app_dir=tmp_path)
    assert code == 1
    assert not (tmp_path / "downloads").exists()

def test_help_and_version_from_different_cwd(tmp_path):
    project = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, str(project / "main.py"), "--help"], cwd=tmp_path,
                            capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0 and "--dry-run" in result.stdout
    result = subprocess.run([sys.executable, str(project / "main.py"), "--version"], cwd=tmp_path,
                            capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0 and "1.0.0" in result.stdout

def test_alias_name_confirmation_is_generic(tmp_path, html):
    changed = html.replace("<h1 id=\"firstHeading\">离线测试角色</h1>",
                           "<h1 id=\"firstHeading\">其他别名</h1>")
    output = []
    code = main([PAGE_URL, "--dry-run"], input_fn=answers("熟悉的名字", "1"),
                output_fn=output.append, client_factory=client_factory(changed), app_dir=tmp_path)
    assert code == 0
    assert any("熟悉的名字" in line for line in output)
    assert any("名称候选" in line for line in output)

def test_user_name_override_does_not_rename_existing_identity(tmp_path, html, page):
    directory = tmp_path / "downloads" / "离线测试角色"
    run_selection(directory, page, [0])
    code = main([PAGE_URL, "--name", "别名"], input_fn=answers("1", "1"), output_fn=lambda _: None,
                client_factory=client_factory(html), app_dir=tmp_path)
    assert code == 0
    assert not (tmp_path / "downloads" / "别名").exists()
