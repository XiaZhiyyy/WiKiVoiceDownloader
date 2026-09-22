import copy
from wiki_voice_downloader.models import VoiceCandidate, TextVariant
from wiki_voice_downloader.storage import read_metadata
from .helpers import run_selection, Response, url
from .test_numbering import records

def test_full_six_stage_acceptance_workflow(tmp_path, page):
    directory = tmp_path / "中文 路径" / "离线测试角色"
    first, _ = run_selection(directory, page, [0, 1])
    assert first.downloaded == 4 and first.duplicates == 1
    second, _ = run_selection(directory, page, [2], {url("F"): [Response(status=404)]})
    assert (second.downloaded, second.failed, second.cumulative) == (1, 1, 5)
    assert records(directory)[-1][3] == "[无台词]"
    third, requests = run_selection(directory, page, [2])
    assert third.downloaded == 1 and third.existing == 1
    assert [call[0] for call in requests.calls] == [url("F")]

    modified = copy.deepcopy(page)
    for group in modified.groups:
        for candidate in group.candidates:
            candidate.dom_order += 1
    modified.groups[0].candidates.insert(0, VoiceCandidate(
        url("G"), "base", "原皮", "新插入语音", [TextVariant("zh", "新正文")], "present", 0
    ))
    fourth, _ = run_selection(directory, modified, [0, 1, 2])
    assert fourth.downloaded == 1 and fourth.cumulative == 7
    only_a = copy.deepcopy(modified)
    only_a.groups[0].candidates = [next(c for c in modified.groups[0].candidates if c.source_url == url("A"))]
    fifth, _ = run_selection(directory, only_a, [0], {url("A"): [Response(status=404)]}, overwrite=True)
    assert fifth.failed == 1 and fifth.cumulative == 7

    data = read_metadata(directory)
    mapping = {item["source_url"]: item["id"] for item in data["items"]}
    assert mapping == {url(letter): index for index, letter in enumerate("ABCDEFG", 1)}
    rows = records(directory)
    assert len(rows) == 7 and len({r[0] for r in rows}) == 7
    assert rows[4][3] == "[无台词]" and rows[5][0] == "006.mp3"
    txt_bytes = (directory / data["txt_filename"]).read_bytes()
    assert not txt_bytes.startswith(b"\xef\xbb\xbf") and b"\r" not in txt_bytes
    assert not list((directory / "audio").glob("*.part"))
