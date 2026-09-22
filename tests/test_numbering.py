import copy
import json
import pytest
from wiki_voice_downloader.errors import StorageError
from wiki_voice_downloader.models import VoiceCandidate
from wiki_voice_downloader.storage import read_metadata, Store, DirectoryLock, find_directory, safe_name
from wiki_voice_downloader.selection import selected_candidates
from wiki_voice_downloader.text_format import parse_record
from .helpers import run_selection, Response, url

def records(directory):
    data = read_metadata(directory)
    text = (directory / data["txt_filename"]).read_text(encoding="utf-8")
    return [parse_record(line) for line in text.splitlines()]

def test_N01_cross_skin_one_sequence(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    summary, session = run_selection(directory, page, [1, 0])
    data = read_metadata(directory)
    assert [v["filename"] for v in data["items"]] == ["001.mp3", "002.mp3", "003.mp3", "004.mp3"]
    assert (summary.downloaded, summary.duplicates, summary.cumulative) == (4, 1, 4)
    assert all("audio/" not in row[0] for row in records(directory))

def test_N02_N03_failure_reserves_id_and_resume_is_idempotent(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    summary, _ = run_selection(directory, page, [0], {url("B"): [Response(status=404)]})
    data = read_metadata(directory)
    assert data["next_id"] == 4 and data["items"][1]["status"] == "failed"
    assert [r[0] for r in records(directory)] == ["001.mp3", "003.mp3"]
    summary, session = run_selection(directory, page, [0])
    assert (summary.downloaded, summary.existing, summary.failed) == (1, 2, 0)
    assert [c[0] for c in session.calls] == [url("B")]
    assert [r[0] for r in records(directory)] == ["001.mp3", "002.mp3", "003.mp3"]
    assert len(read_metadata(directory)["items"]) == 3

def test_N04_reorder_insert_append_not_renumber(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0])
    updated = copy.deepcopy(page)
    updated.groups[0].candidates.reverse()
    for index, c in enumerate(updated.groups[0].candidates):
        c.dom_order = index + 1
    updated.groups[0].candidates.insert(0, VoiceCandidate(
        url("G"), "base", "原皮", "新台词", dom_order=0
    ))
    run_selection(directory, updated, [0])
    mapping = {item["source_key"]: item["id"] for item in read_metadata(directory)["items"]}
    assert mapping == {url("A"): 1, url("B"): 2, url("C"): 3, url("G"): 4}

def test_N05_only_selected_new_skins_allocate_and_cumulative_preserved(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0])
    run_selection(directory, page, [1])
    data = read_metadata(directory)
    assert len(data["items"]) == 4
    assert not {url("E"), url("F")}.intersection(i["source_key"] for i in data["items"])
    assert len(records(directory)) == 4
    assert data["items"][0]["group_name"] == "原皮"

def test_N06_transition_to_1000_sorted_numerically(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    directory.mkdir()
    with DirectoryLock(directory):
        store = Store.open_confirmed(directory, page.info, page.info.character_name, "biligame_blhx")
        store.data["next_id"] = 999  # Explicit test fixture for a reserved high starting number.
        store.save()
    run_selection(directory, page, [0])
    data = read_metadata(directory)
    assert [i["filename"] for i in data["items"]] == ["999.mp3", "1000.mp3", "1001.mp3"]
    assert [r[0] for r in records(directory)] == ["999.mp3", "1000.mp3", "1001.mp3"]
    assert data["next_id"] == 1002

def test_N07_different_pages_same_name_do_not_merge(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0])
    another = copy.deepcopy(page.info)
    another.page_identity = "biligame:blhx:url:wiki.biligame.com/blhx/Different"
    another.identity_aliases = [another.page_identity]
    with pytest.raises(StorageError):
        find_directory(tmp_path, another, page.info.character_name, "biligame_blhx")

def test_N07_nonempty_without_index_not_overwritten(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    directory.mkdir()
    (directory / "001.mp3").write_bytes(b"user-data")
    with pytest.raises(StorageError):
        find_directory(tmp_path, page.info, page.info.character_name, "biligame_blhx")
    assert (directory / "001.mp3").read_bytes() == b"user-data"

def test_N08_known_canonical_alias_reuses_directory(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0])
    alias = copy.deepcopy(page.info)
    alias.source_page_url = "https://wiki.biligame.com/blhx/AnotherAlias"
    alias.resolved_page_url = alias.source_page_url
    actual, _ = find_directory(tmp_path, alias, "另一个名称", "biligame_blhx")
    assert actual == directory

@pytest.mark.parametrize("value", ["../..", "CON", "con.txt", "PRN", "LPT1", "COM¹", "a:b/c\\d?*", "a. ", ""])
def test_windows_safe_component(value):
    name = safe_name(value)
    assert name and not any(c in name for c in '<>:"/\\|?*')
    assert not name.endswith((" ", "."))
    assert name.upper() not in {"CON", "PRN", "LPT1", "COM¹"}

def test_windows_case_conflict_not_separate_directory(tmp_path, page):
    modified = copy.deepcopy(page)
    modified.info.character_name = "Alice"
    run_selection(tmp_path / "Alice", modified, [0])
    actual, _ = find_directory(tmp_path, page.info, "alice", "biligame_blhx")
    assert actual.name == "Alice"

def test_unmanaged_numeric_audio_never_overwritten(tmp_path, page):
    directory = tmp_path / "离线测试角色"
    run_selection(directory, page, [0])
    unmanaged = directory / "audio" / "004.mp3"
    unmanaged.write_bytes(b"user-data")
    before = (directory / "metadata.json").read_bytes()
    with pytest.raises(StorageError):
        run_selection(directory, page, [1])
    assert unmanaged.read_bytes() == b"user-data"
    assert len(read_metadata(directory)["items"]) == 3
