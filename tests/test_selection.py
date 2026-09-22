import pytest
from wiki_voice_downloader.selection import parse_selection, selected_candidates
from wiki_voice_downloader.errors import SelectionError

@pytest.mark.parametrize("value,expected", [
    ("1", [0]), ("1,3", [0, 2]), ("3,1", [0, 2]), ("1,1,3", [0, 2]),
    ("A", [0, 1, 2]), ("a", [0, 1, 2]), (" 3 , 1 ", [0, 2]),
])
def test_P09_selection(value, expected):
    assert parse_selection(value, 3) == expected

@pytest.mark.parametrize("value", ["", " ", "0", "-1", "4", "1,a", "1,,2", "A,1", "1;2", "1.0", "1 2"])
def test_P10_invalid_selection(value):
    with pytest.raises(SelectionError):
        parse_selection(value, 3)

def test_selection_keeps_dom_order(page):
    a = selected_candidates(page.groups, parse_selection("3,1", 3))[0]
    b = selected_candidates(page.groups, parse_selection("1,1,3", 3))[0]
    assert [c.source_url for c in a] == [c.source_url for c in b]
    assert [c.dom_order for c in a] == sorted(c.dom_order for c in a)
