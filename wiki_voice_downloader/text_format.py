"""The four-field index format. JSON always stores unescaped semantic text."""
from __future__ import annotations
import logging
import re
import unicodedata
from bs4 import Tag, NavigableString, Comment
from .models import TextVariant

log = logging.getLogger(__name__)
BLOCKS = {"br", "p", "div", "li", "ul", "ol", "section", "blockquote",
          "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr"}
SKIP_CLASSES = {"mw-editsection", "sm-bar", "sm-audio-src",
                "reference", "ship_word_button"}
SKIP_TAGS = {"script", "style", "button", "audio", "source", "noscript"}

def normalize_line(text: str) -> str:
    out = []
    unusual = set()
    for char in text:
        # Zero-width joining characters are semantic; preserve them.
        if char.isspace():
            out.append(" ")
        elif unicodedata.category(char) == "Cc":
            unusual.add(f"U+{ord(char):04X}")
            out.append(" ")
        else:
            out.append(char)
    if unusual:
        log.warning("正文布局异常控制符已替换为空格: %s", ", ".join(sorted(unusual)))
    return re.sub(" +", " ", "".join(out)).strip()

def extract_text(node: Tag) -> str:
    def walk(value):
        if isinstance(value, Comment):
            return ""
        if isinstance(value, NavigableString):
            return str(value)
        if not isinstance(value, Tag):
            return ""
        if value.name in SKIP_TAGS or SKIP_CLASSES.intersection(value.get("class", [])):
            return ""
        inner = "".join(walk(child) for child in value.children)
        return f" {inner} " if value.name in BLOCKS else inner
    # BeautifulSoup already decoded HTML entities, so do not html.unescape again.
    return normalize_line(walk(node))

def escape_field(value: str) -> str:
    return "".join("\\" + c if c in "\\|" else c for c in normalize_line(value))

def serialize_record(fields: tuple[str, str, str, str] | list[str]) -> str:
    if len(fields) != 4 or not all(isinstance(v, str) for v in fields):
        raise ValueError("TXT 记录必须是四个字符串字段。")
    return "|".join(escape_field(v) for v in fields)

def parse_record(line: str) -> tuple[str, str, str, str]:
    # Accept one LF line terminator, but no embedded newline, CR or TAB.
    if line.endswith("\n"):
        line = line[:-1]
    if any(c.isspace() and c != " " for c in line) or any(
        unicodedata.category(c) == "Cc" for c in line
    ):
        raise ValueError("记录含非单行布局控制符。")
    parts, buf, escaped = [], [], False
    for char in line:
        if escaped:
            if char not in "\\|":
                raise ValueError("TXT 含不支持的转义。")
            buf.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    if escaped:
        raise ValueError("TXT 末尾存在未完成的反斜杠转义。")
    parts.append("".join(buf))
    if len(parts) != 4:
        raise ValueError(f"TXT 应为四字段，实际为 {len(parts)}。")
    return tuple(parts)

def render_text(texts: list[TextVariant] | list[dict]) -> str:
    values = [(v.language, v.text) if isinstance(v, TextVariant)
              else (v.get("language"), v["text"]) for v in texts]
    values = [(lang, normalize_line(text)) for lang, text in values if normalize_line(text)]
    if not values:
        return "[无台词]"
    if len(values) == 1:
        return values[0][1]
    return " ".join(f"[{lang or 'unknown'}] {text}" for lang, text in values)
