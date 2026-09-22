"""Lightweight MPEG Layer III frame-boundary validation, not audio decoding.

Facts used: MPEG version/layer/bitrate/sample-rate fields and frame-length
formula (144 or 72 * bitrate / sample_rate + padding). See docs/sources.md.
No duration or minimum file-size filter is imposed.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import logging
import mmap
from pathlib import Path
from .errors import InvalidMedia, UnsupportedMedia

@dataclass(frozen=True)
class MediaInfo:
    size_bytes: int
    sha256: str
    frame_count: int | None
    format: str = "mp3"
    codec: str = "mpeg-layer-iii"
    extension: str = "mp3"
    validation_level: str = "frame-boundaries"
    page_count: int | None = None

def _frame_length(header: bytes) -> tuple[int, tuple[int, int]]:
    if len(header) != 4:
        raise InvalidMedia("MP3 帧头截断。")
    word = int.from_bytes(header, "big")
    if word >> 21 != 0x7FF:
        raise InvalidMedia("MP3 帧同步标志无效或存在不支持的附加数据。")
    version = (word >> 19) & 3
    layer = (word >> 17) & 3
    bitrate_index = (word >> 12) & 15
    rate_index = (word >> 10) & 3
    if version == 1 or layer != 1 or rate_index == 3 or bitrate_index in {0, 15}:
        raise InvalidMedia("不是受支持的 MPEG Layer III 帧（free-format 暂不支持）。")
    mpeg1 = version == 3
    rates = [44100, 48000, 32000]
    rate = rates[rate_index] // (1 if mpeg1 else 2 if version == 2 else 4)
    table = ([0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
             if mpeg1 else [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160])
    length = (144 if mpeg1 else 72) * table[bitrate_index] * 1000 // rate
    length += (word >> 9) & 1
    crc_size = 0 if (word >> 16) & 1 else 2
    mono = ((word >> 6) & 3) == 3
    side_size = (17 if mono else 32) if mpeg1 else (9 if mono else 17)
    if length < 4 + crc_size + side_size:
        raise InvalidMedia("MP3 帧长度不足。")
    return length, (version, rate)

def inspect_error_prefix(prefix: bytes) -> None:
    value = prefix.lstrip(b"\xef\xbb\xbf \r\n\t").lower()
    if value.startswith((b"<", b"{", b"[")):
        from .access_detection import classify_access, require_access
        require_access(classify_access(prefix, resource_kind='audio'))
        raise InvalidMedia("音频请求返回 HTML/JSON 文本，而非有效音频。")
    if value.startswith((b"riff", b"flac")):
        raise UnsupportedMedia("收到未支持的 WAV/FLAC 容器；不会强行修改扩展名。")

def validate_mp3(path: Path, content_type: str = "",
                 expected_size: int | None = None) -> MediaInfo:
    size = path.stat().st_size
    if size == 0:
        raise InvalidMedia("音频响应为空。")
    if expected_size is not None and size != expected_size:
        raise InvalidMedia(f"响应长度不符：收到 {size} 字节，应为 {expected_size}。",
                           retryable=True)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        prefix = stream.read(4096)
        inspect_error_prefix(prefix)
        stream.seek(0)
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            pos, end, frames = 0, size, 0
            # ID3v2 tag, including bounded synchsafe size and optional v2.4 footer.
            if data[:3] == b"ID3":
                if end < 10 or data[3] not in {2, 3, 4} or data[4] == 255:
                    raise InvalidMedia("ID3v2 头无效或截断。")
                flags = data[5]
                allowed_flags = {2: 0xC0, 3: 0xE0, 4: 0xF0}[data[3]]
                if flags & ~allowed_flags or any(v & 0x80 for v in data[6:10]):
                    raise InvalidMedia("ID3v2 标志或长度无效。")
                tag_size = sum(v << shift for v, shift in zip(data[6:10], (21, 14, 7, 0)))
                pos = 10 + tag_size
                if data[3] == 4 and flags & 0x10:
                    if pos + 10 > end or data[pos:pos + 3] != b"3DI":
                        raise InvalidMedia("ID3v2.4 footer 无效。")
                    pos += 10
                if pos > end:
                    raise InvalidMedia("ID3 标签长度超过文件大小。")
            if end - pos >= 128 and data[end - 128:end - 125] == b"TAG":
                end -= 128  # ID3v1, without rewriting bytes.
            # APEv2 tags are deliberately not guessed/stripped: reported as unsupported.
            stream_identity = None
            while pos < end:
                if end - pos < 4:
                    raise InvalidMedia("MP3 最后一帧不完整或含未知尾部数据。")
                length, identity = _frame_length(data[pos:pos + 4])
                if stream_identity is None:
                    stream_identity = identity
                elif identity != stream_identity:
                    raise InvalidMedia("MP3 帧的版本/采样率不一致。")
                if pos + length > end:
                    raise InvalidMedia("MP3 帧数据截断。", retryable=True)
                frames += 1
                pos += length
            if frames == 0:
                raise InvalidMedia("只有标签，没有任何完整 MP3 音频帧。")
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    # A generic MIME is fine. An unexpected MIME is diagnostic, not stronger
    # evidence than a complete frame stream. HTML/JSON byte prefixes fail above.
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime not in {"", "audio/mpeg", "audio/mp3", "audio/x-mp3",
                    "application/octet-stream", "binary/octet-stream"}:
        logging.getLogger(__name__).warning("MP3 帧结构有效，但服务器 MIME 非典型：%s", mime)
    return MediaInfo(size, digest.hexdigest(), frames)

def file_matches(path: Path, record: dict) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if path.stat().st_size != record.get("size_bytes"):
            return False
        from .media.registry import validate_media
        media = validate_media(path)
        expected_format = record.get("media_format") or (record.get("filename", "").rsplit(".", 1)[-1])
        return media.sha256 == record.get("sha256") and media.format == expected_format
    except (OSError, InvalidMedia, ValueError):
        return False
