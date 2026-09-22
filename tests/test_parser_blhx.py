import pytest
from bs4 import BeautifulSoup
from wiki_voice_downloader.errors import ParseError
from wiki_voice_downloader.parsers.biligame_blhx import BiligameBlhxParser
from wiki_voice_downloader.selection import selected_candidates
from wiki_voice_downloader.urls import request_url
from .conftest import FIXTURES
from .helpers import PAGE_URL, block, wrap, url

def parse(html):
    return BiligameBlhxParser().parse(html, PAGE_URL)

def test_P01_supplied_profile_regression():
    page = parse((FIXTURES / "supplied_profile.html").read_text(encoding="utf-8"))
    c = page.groups[0].candidates[0]
    assert c.source_url.endswith("/2/2a/jte9xlrs9q097upuwbyakwocnt5q6un.mp3")
    assert (c.group_name, c.category, c.texts[0].language) == ("原皮", "自我介绍", "zh")
    assert c.texts[0].text == "妾身乃是重樱航母、信浓……"

def test_P02_P03_independent_same_row_blocks(page):
    values = page.groups[0].candidates
    assert len(values) == 3
    assert values[1].category == values[2].category == "登录台词"
    assert values[1].texts[0].text == "第一句，指挥官。"
    assert values[2].texts[0].text == "二番目の台詞です。"

def test_P04_P05_P06_text_only_and_missing(page):
    assert page.skipped_text_only == 1
    missing = page.groups[2].candidates[0]
    assert missing.text_status == "missing" and missing.texts == []
    assert all("只有文字" not in v.text for g in page.groups for c in g.candidates for v in c.texts)

def test_P07_P08_hidden_groups(page):
    assert [g.name for g in page.groups] == ["原皮", "测试皮肤一", "测试皮肤二"]
    assert [g.unique_count for g in page.groups] == [3, 2, 2]
    assert sum(len(g.candidates) for g in page.groups) == 7

def test_P11_selection_before_cross_group_deduplication(page):
    one, dup_one, _ = selected_candidates(page.groups, [1])
    assert one[1].source_url == url("A") and one[1].group_name == "测试皮肤一"
    all_values, duplicates, raw = selected_candidates(page.groups, [2, 1, 0])
    assert (len(all_values), duplicates, raw) == (6, 1, 7)
    assert all_values[0].group_name == "原皮"

def test_P12_same_text_different_urls_not_merged():
    p = parse(wrap("<tr><th>同类型</th><td>" + block("A") + block("B") + "</td></tr>"))
    values, duplicates, _ = selected_candidates(p.groups, [0])
    assert len(values) == 2 and duplicates == 0

@pytest.mark.parametrize("value,expected", [
    ("./A.mp3?Token=X&z=2&a=1#p", "https://wiki.biligame.com/blhx/A.mp3?Token=X&z=2&a=1"),
    ("//patchwiki.biligame.com/images/blhx/Mixed.MP3?q=1", "https://patchwiki.biligame.com/images/blhx/Mixed.MP3?q=1"),
])
def test_P13_urls_preserve_semantics(value, expected):
    assert request_url(value, PAGE_URL) == expected

def test_P14_alias_name_and_identity():
    html = wrap("<tr><th>问候</th><td>" + block() + "</td></tr>")
    html = html.replace("<h1", '<script>mw.config.set({"wgArticleId":12345});</script><div data-character-name="信浓"></div><h1')
    html = html.replace("合成角色", "鵗")
    p = parse(html)
    assert p.info.name_candidates == ["信浓", "鵗"]
    assert p.info.character_name == "信浓" and p.info.name_reliable
    assert p.info.page_identity == "biligame:blhx:pageid:12345"

@pytest.mark.parametrize("html,reason", [
    ("<html><title>Just a moment</title><div id='cf-chl-test'></div></html>", "needs_human_verification"),
    ("<html><body>短页面</body></html>", "incomplete"),
    ("<div>暂无配音</div>", "no_audio"),
    ("<div id='mw-content-text'>" + "其他正文" * 40 + "</div>", "structure_changed"),
])
def test_P15_zero_result_classification(html, reason):
    with pytest.raises(ParseError) as caught:
        parse(html)
    assert caught.value.reason == reason

@pytest.mark.parametrize("address", [
    "https://wiki.biligame.com.evil.test/blhx/Test",
    "https://evilbiligame.com/blhx/Test", "file:///blhx/Test",
    "https://wiki.biligame.com/other/Test",
    "https://patchwiki.biligame.com/images/blhx/a.mp3",
    "https://wiki.biligame.com:8443/blhx/Test",
])
def test_P15_unsupported_source_hosts(address):
    assert not BiligameBlhxParser().supports(address)

def test_P16_local_multilingual_texts():
    content = block("A", "你好").replace(
        '<div class="sm-audio-src">',
        '<p class="ship_word_line" data-lang="ja">こんにちは。</p><div class="sm-audio-src">'
    )
    c = parse(wrap("<tr><th>问候</th><td>" + content + "</td></tr>")).groups[0].candidates[0]
    assert [(v.language, v.text) for v in c.texts] == [("zh", "你好"), ("ja", "こんにちは。")]

def test_ambiguous_text_is_not_guessed():
    content = block().replace('<div class="sm-audio-src">',
                             '<p class="ship_word_line" data-lang="zh">另一句</p><div class="sm-audio-src">')
    c = parse(wrap("<tr><td>" + content + "</td></tr>")).groups[0].candidates[0]
    assert c.text_status == "ambiguous" and not c.texts and c.category == "未分类"

def test_rowspan_type_is_local_to_table():
    p = parse(wrap('<tr><th rowspan="2">问候</th><td>' + block("A") +
                   '</td></tr><tr><td>' + block("B") + '</td></tr><tr><td>' + block("C") + '</td></tr>'))
    assert [c.category for c in p.groups[0].candidates] == ["问候", "问候", "未分类"]

def test_no_cross_block_or_whole_td_text_guess():
    p = parse(wrap('<tr><th>问候</th><td><p class="ship_word_line">不可靠邻近文字</p>'
                   '<div class="sm-audio-src"><a href="' + url("A") + '">音频</a></div></td></tr>'))
    assert p.groups[0].candidates[0].texts == []

def test_unknown_group_not_silently_base():
    html = wrap("<tr><td>" + block() + "</td></tr>")
    html = html.replace('data-skin-name="原皮"', 'role="tabpanel" id="unlabelled"')
    p = parse(html)
    assert p.groups[0].name.startswith("未识别分组")
    assert p.warnings

def test_semantic_tab_label_and_title_wrappers():
    html = wrap("<tr><td>" + block() + "</td></tr>")
    html = html.replace('data-skin-name="原皮"', 'role="tabpanel" id="panel-a" aria-labelledby="tab-a"')
    html = html.replace("<h2>", '<a role="tab" id="tab-a" href="#panel-a">实际标签名</a><h2>')
    assert parse(html).groups[0].name == "实际标签名"
    html = html.replace('role="tabpanel" id="panel-a" aria-labelledby="tab-a"',
                        'class="tabbertab" title="另一个标签"')
    assert parse(html).groups[0].name == "另一个标签"

def test_invalid_audio_source_cannot_become_success():
    html = wrap("<tr><td>" + block().replace(url("A"), "javascript:alert(1)") + "</td></tr>")
    with pytest.raises(ParseError) as caught:
        parse(html)
    assert caught.value.reason == "unsupported_sources"

def test_duplicate_missing_text_can_be_filled_but_group_stable():
    p = parse(wrap("<tr><td>" + block("A", None) + block("A", "补充正文") + "</td></tr>"))
    values, duplicates, _ = selected_candidates(p.groups, [0])
    assert len(values) == 1 and duplicates == 1
    assert values[0].texts[0].text == "补充正文"

def test_identifier_mismatch_does_not_take_unrelated_line():
    html = wrap('<tr><td><div class="ship_word_block" data-key="x" data-key-i="2">'
                '<p class="ship_word_line" data-key="x" data-key-i="1">错误的句子</p>'
                '<div class="sm-audio-src"><a href="' + url("A") + '">音频</a></div></div></td></tr>')
    c = parse(html).groups[0].candidates[0]
    assert not c.texts
