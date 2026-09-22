import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
import requests
from wiki_voice_downloader.errors import StorageError
from wiki_voice_downloader.models import TextVariant
from wiki_voice_downloader.storage import DirectoryLock, Store, read_metadata
from wiki_voice_downloader.selection import selected_candidates
from .helpers import run_selection, service, Response, url, mp3
from .test_numbering import records

class Crash(RuntimeError):
    pass

@pytest.mark.parametrize("point", [
    "after_plan", "after_stage", "after_audio_replace", "after_metadata_complete",
])
def test_R01_R02_R03_R04_crash_windows_recover(point, tmp_path, page):
    directory = tmp_path / "离线测试角色"
    def checkpoint(name):
        if name == point:
            raise Crash(point)
    with pytest.raises(Crash):
        run_selection(directory, page, [0], checkpoint=checkpoint)
    data = read_metadata(directory)
    assert data["next_id"] == 4
    if point in {"after_stage", "after_audio_replace"}:
        assert "pending_commit" in data["items"][0]
    summary, session = run_selection(directory, page, [0])
    assert summary.cumulative == 3 and summary.failed == 0
    assert [r[0] for r in records(directory)] == ["001.mp3", "002.mp3", "003.mp3"]
    assert all("pending_commit" not in i for i in read_metadata(directory)["items"])
    assert len(session.calls) == (3 if point == "after_plan" else 2)

def test_R01_unverified_part_restarts_from_byte_zero(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0], {url("B"): [Response(status=404)]})
    part = directory / "audio" / "002.mp3.part"
    part.write_bytes(b"unverified old bytes")
    summary, session = run_selection(directory, page, [0])
    assert summary.downloaded == 1
    assert (directory / "audio" / "002.mp3").read_bytes() == mp3()
    assert not part.exists()
    assert "Range" not in session.calls[0][1]["headers"]

def test_R04_txt_view_rebuilt_after_failed_txt_replace(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    count = 0
    def checkpoint(name):
        nonlocal count
        if name == "before_txt_replace":
            count += 1
            if count == 3:  # recover, plan, then first successful commit
                raise Crash("TXT write window")
    with pytest.raises(Crash):
        run_selection(directory, page, [0], checkpoint=checkpoint)
    assert read_metadata(directory)["items"][0]["status"] == "completed"
    run_selection(directory, page, [0])
    assert len(records(directory)) == 3

@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_R05_bad_completed_file_repaired_only_if_selected(tmp_path, page, damage):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0, 1, 2])
    target = directory / "audio" / "004.mp3"
    if damage == "missing":
        target.unlink()
    else:
        target.write_bytes(b"corruption")
    summary, session = run_selection(directory, page, [0])
    assert len(session.calls) == 0 and summary.cumulative == 5
    assert "004.mp3" not in {r[0] for r in records(directory)}
    summary, session = run_selection(directory, page, [1])
    assert summary.downloaded == 1 and summary.cumulative == 6
    assert [call[0] for call in session.calls] == [url("D")]

@pytest.mark.parametrize("damage", ["json", "schema", "next_id", "duplicate", "filename", "journal"])
def test_R06_corrupt_metadata_preserves_scene(tmp_path, page, damage):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0])
    path = directory / "metadata.json"
    data = read_metadata(directory)
    if damage == "json":
        path.write_text("{broken", encoding="utf-8")
    else:
        if damage == "schema":
            data["schema_version"] = 999
        elif damage == "next_id":
            data["next_id"] = 1
        elif damage == "duplicate":
            data["items"].append(copy.deepcopy(data["items"][0]))
        elif damage == "filename":
            data["items"][0]["filename"] = "../outside.mp3"
        else:
            new = copy.deepcopy(data["items"][0])
            new["id"], new["filename"] = 200, "200.mp3"
            data["items"][0]["pending_commit"] = {"new": new, "staged_at": data["updated_at"]}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    with pytest.raises(StorageError):
        run_selection(directory, page, [0])
    after = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    assert before == after

def test_R07_overwrite_failure_keeps_old_audio_and_text(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0])
    before_audio = (directory / "audio" / "001.mp3").read_bytes()
    before_text = records(directory)
    modified = copy.deepcopy(page)
    modified.groups[0].candidates = [modified.groups[0].candidates[0]]
    modified.groups[0].candidates[0].texts = [TextVariant("zh", "不应在失败时写入的新正文")]
    summary, _ = run_selection(directory, modified, [0], {url("A"): [Response(b"<html>Error</html>")]}, overwrite=True)
    item = read_metadata(directory)["items"][0]
    assert summary.failed == 1 and summary.downloaded == 0
    assert item["status"] == "completed" and item["last_error"]
    assert (directory / "audio" / "001.mp3").read_bytes() == before_audio
    assert records(directory) == before_text
    assert "旧有效文件已保留" in summary.failures[0][1]

def test_R08_overwrite_success_only_selected_and_id_stable(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0, 1])
    other = (directory / "audio" / "004.mp3").read_bytes()
    modified = copy.deepcopy(page)
    modified.groups[0].candidates = [modified.groups[0].candidates[0]]
    modified.groups[0].candidates[0].texts = [TextVariant("zh", "覆盖后的正文")]
    summary, session = run_selection(directory, modified, [0],
                                     {url("A"): [Response(mp3(payload=1))]}, overwrite=True)
    assert summary.downloaded == 1 and len(session.calls) == 1
    assert read_metadata(directory)["items"][0]["id"] == 1
    assert records(directory)[0][3] == "覆盖后的正文"
    assert (directory / "audio" / "004.mp3").read_bytes() == other
    assert len(records(directory)) == 4

def test_continue_preserves_original_text_but_fills_missing(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0, 2])
    modified = copy.deepcopy(page)
    modified.groups[0].candidates[0].texts = [TextVariant("zh", "远端改动不自动同步")]
    modified.groups[2].candidates[0].texts = [TextVariant("ja", "補った台詞")]
    modified.groups[2].candidates[0].text_status = "present"
    summary, session = run_selection(directory, modified, [0, 2])
    assert len(session.calls) == 0
    lines = records(directory)
    assert lines[0][3] == "你好，指挥官！"
    assert lines[3][3] == "補った台詞"

def test_R10_ctrl_c_leaves_numbered_task_and_closed_response(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    broken = Response(mp3(), error=KeyboardInterrupt())
    summary, session = run_selection(directory, page, [0], {url("A"): [broken]})
    assert summary.exit_code == 130 and summary.unexecuted == 3 and broken.closed
    assert read_metadata(directory)["next_id"] == 4
    assert records(directory) == []
    summary, _ = run_selection(directory, page, [0])
    assert summary.cumulative == 3

def test_R10_index_persistence_failure_stops_before_network(tmp_path, page, monkeypatch):
    directory = tmp_path / "离线测试角色"
    original = Store.save
    def fail_on_allocated(self):
        if self.data["items"]:
            raise StorageError("Injected disk full")
        return original(self)
    monkeypatch.setattr(Store, "save", fail_on_allocated)
    runner, session, _ = service()
    candidates, _, _ = selected_candidates(page.groups, [0])
    with pytest.raises(StorageError):
        runner.run(page, candidates, directory, page.info.character_name, "biligame_blhx")
    assert session.calls == []
    assert not list((directory / "audio").glob("*.mp3"))

def test_R10_locked_audio_replace_retains_old_and_pending_commit(tmp_path, page, monkeypatch):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0])
    modified = copy.deepcopy(page)
    modified.groups[0].candidates = [modified.groups[0].candidates[0]]
    original = os.replace
    def occupied(src, dst):
        if Path(dst).suffix == ".mp3":
            raise PermissionError("injected occupied file")
        return original(src, dst)
    monkeypatch.setattr(os, "replace", occupied)
    with pytest.raises(StorageError):
        run_selection(directory, modified, [0], {url("A"): [Response(mp3(payload=1))]}, overwrite=True)
    assert (directory / "audio" / "001.mp3").read_bytes() == mp3()
    assert "pending_commit" in read_metadata(directory)["items"][0]
    monkeypatch.setattr(os, "replace", original)
    summary, session = run_selection(directory, modified, [0])
    assert (directory / "audio" / "001.mp3").read_bytes() == mp3(payload=1)
    assert len(session.calls) == 0

def test_R10_part_write_permission_failure_is_fatal(tmp_path, page, monkeypatch):
    directory = tmp_path / "离线测试角色"
    original = Path.open
    def blocked(self, *args, **kwargs):
        if str(self).endswith(".mp3.part") and args and args[0] == "wb":
            raise PermissionError("injected")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", blocked)
    with pytest.raises(StorageError):
        run_selection(directory, page, [0])
    assert read_metadata(directory)["items"][0]["status"] == "pending"
    assert records(directory) == []

def test_R11_same_directory_lock_prevents_second_writer(tmp_path):
    with DirectoryLock(tmp_path):
        with pytest.raises(StorageError):
            with DirectoryLock(tmp_path):
                pass
    with DirectoryLock(tmp_path):
        pass

def test_R11_crashed_process_lock_released_by_os(tmp_path):
    project = Path(__file__).resolve().parent.parent
    script = ("from pathlib import Path; import os; "
              "from wiki_voice_downloader.storage import DirectoryLock; "
              f"lock=DirectoryLock(Path({str(tmp_path)!r})); lock.__enter__(); os._exit(0)")
    result = subprocess.run([sys.executable, "-c", script], cwd=project, timeout=10)
    assert result.returncode == 0
    with DirectoryLock(tmp_path):
        pass

def test_R12_rerun_idempotent_no_duplicate_download(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0, 1, 2])
    original = records(directory)
    summary, session = run_selection(directory, page, [2, 1, 0])
    assert len(session.calls) == 0 and summary.existing == 6
    assert summary.total == summary.downloaded + summary.existing + summary.failed + summary.unexecuted
    assert records(directory) == original

def test_persistent_site_error_does_not_hammer_later_files(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    routes = {url("A"): [Response(status=429, headers={"Retry-After": "0"}) for _ in range(3)]}
    summary, session = run_selection(directory, page, [0, 1, 2], routes)
    assert len(session.calls) == 3
    assert (summary.failed, summary.unexecuted, summary.exit_code) == (1, 5, 1)

def test_three_transient_failures_continue_next_file(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    summary, session = run_selection(directory, page, [0], {url("B"): [requests.Timeout()] * 3})
    assert (summary.downloaded, summary.failed, summary.exit_code) == (2, 1, 2)
    assert len(session.calls) == 5
    assert [r[0] for r in records(directory)] == ["001.mp3", "003.mp3"]

def test_untrusted_symlink_audio_not_written(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0])
    target = directory / "audio" / "001.mp3"
    other = tmp_path / "outside.mp3"
    other.write_bytes(b"outside")
    target.unlink()
    try:
        target.symlink_to(other)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not available in this environment")
    with pytest.raises(StorageError):
        run_selection(directory, page, [0])
    assert other.read_bytes() == b"outside"

def test_corrupted_staged_overwrite_rolls_back_to_valid_old(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0])
    def checkpoint(name):
        if name == "after_stage":
            raise Crash("after_stage")
    with pytest.raises(Crash):
        run_selection(directory, page, [0], {url("A"): [Response(mp3(payload=1))]},
                      overwrite=True, checkpoint=checkpoint)
    (directory / "audio" / "001.mp3.part").write_bytes(b"corrupted staging")
    summary, session = run_selection(directory, page, [0])
    assert len(session.calls) == 0 and summary.existing == 3
    assert (directory / "audio" / "001.mp3").read_bytes() == mp3()
    assert "pending_commit" not in read_metadata(directory)["items"][0]

def test_index_is_durable_before_any_download(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    def first_response():
        data = read_metadata(directory)
        assert data["next_id"] == 4
        assert [item["id"] for item in data["items"]] == [1, 2, 3]
        return Response()
    summary, _ = run_selection(directory, page, [0], {url("A"): [first_response]})
    assert summary.downloaded == 3

def test_boolean_schema_is_not_version_one(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0])
    data = read_metadata(directory)
    data["schema_version"] = True
    (directory / "metadata.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StorageError):
        read_metadata(directory)

def test_two_distinct_urls_redirecting_to_one_resource_remain_two_ids(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    subset = copy.deepcopy(page)
    subset.groups[0].candidates = subset.groups[0].candidates[:2]
    summary, _ = run_selection(directory, subset, [0], {
        url("A"): [Response(status=302, headers={"Location": url("B")})]
    })
    data = read_metadata(directory)
    assert summary.downloaded == 2
    assert [i["source_key"] for i in data["items"]] == [url("A"), url("B")]
    assert {i["resolved_audio_url"] for i in data["items"]} == {url("B")}
