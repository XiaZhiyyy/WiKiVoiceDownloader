import random
import pytest
from bs4 import BeautifulSoup
from wiki_voice_downloader.text_format import extract_text, normalize_line, serialize_record, parse_record, render_text
from wiki_voice_downloader.models import TextVariant

def text(html):
    return extract_text(BeautifulSoup(html, "html.parser").find())

def test_T01_layout_entities_and_excluded_controls():
    assert text('<p>A<br>B\t C\r\n D &amp; &amp;lt; <button>播放</button><script>bad()</script></p>') == "A B C D & &lt;"

def test_T02_inline_chinese_and_english():
    assert text("<p>指<span>挥</span>官 Hello <em>Commander</em></p>") == "指挥官 Hello Commander"

def test_T03_unicode_punctuation_preserved():
    value = "（ため息）……—～ こんにちは！"
    assert text("<p>" + value + "</p>") == value

@pytest.mark.parametrize("fields", [
    ("001.mp3", "原皮", "类型", "A|B\\C"),
    ("001.mp3", "皮|肤\\", "类型\\\\|", "\\\\|\\\\\\"),
    ("001.mp3", "|", "\\", "tail\\"),
    ("1000.mp3", "原皮", "", ""),
])
def test_T04_T05_round_trip(fields):
    assert parse_record(serialize_record(fields)) == fields

def test_escape_property_many_backslashes():
    rng = random.Random(20260912)
    alphabet = "a|\\中あ—"
    for _ in range(400):
        fields = tuple("".join(rng.choice(alphabet) for _ in range(rng.randrange(30))) for _ in range(4))
        assert parse_record(serialize_record(fields)) == fields

@pytest.mark.parametrize("line", ["a|b|c", "a|b|c|d|e", "a|b|c|bad\\q", "a|b|c|bad\\", "a|b|c|x\ny", "a|b|c|x\ry"])
def test_broken_rows_rejected(line):
    with pytest.raises(ValueError):
        parse_record(line)

def test_multilingual_export_without_language_guess():
    assert render_text([]) == "[无台词]"
    assert render_text([TextVariant("zh", "你好")]) == "你好"
    assert render_text([TextVariant("zh", "你好"), TextVariant(None, "Hello")]) == "[zh] 你好 [unknown] Hello"

def test_abnormal_controls_log_diagnostic(caplog):
    assert normalize_line("a\x00b") == "a b"
    assert "U+0000" in caplog.text
