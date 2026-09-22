from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

@dataclass(frozen=True)
class TextVariant:
    language: str | None
    text: str

@dataclass
class VoiceCandidate:
    source_url: str
    group_id: str
    group_name: str
    category: str
    texts: list[TextVariant] = field(default_factory=list)
    text_status: str = "missing"
    dom_order: int = 0
    diagnostics: list[str] = field(default_factory=list)
    section_id: str = "legacy"
    text_language: str | None = None
    occurrence_locator: str = ""
    format_hint: str | None = None
    references: list[dict] = field(default_factory=list)

@dataclass
class VoiceGroup:
    group_id: str
    name: str
    order: int
    candidates: list[VoiceCandidate] = field(default_factory=list)

    @property
    def unique_count(self) -> int:
        return len({c.source_url for c in self.candidates})

@dataclass
class PageRequestContext:
    input_url: str
    request_url: str
    resolved_url: str | None = None
    fragment_hint: str = ""
    explicit_server: str | None = None

@dataclass
class VoiceSection:
    section_id: str
    display_name: str
    default_text_language: str | None
    groups: list[VoiceGroup] = field(default_factory=list)

@dataclass
class PageInfo:
    character_name: str | None
    name_candidates: list[str]
    source_page_url: str
    resolved_page_url: str
    page_identity: str
    canonical_url: str | None = None
    identity_aliases: list[str] = field(default_factory=list)
    name_reliable: bool = False
    effective_page_url: str | None = None
    resolved_url_observed: bool = True

@dataclass
class ParsedPage:
    info: PageInfo
    groups: list[VoiceGroup]
    warnings: list[str] = field(default_factory=list)
    skipped_text_only: int = 0
    sections: list[VoiceSection] = field(default_factory=list)
    selected_section: str = "legacy"
    text_language: str | None = None
    profile_fingerprint: str | None = None
    profile_id: str | None = None
    request_context: PageRequestContext | None = None
    acquisition: dict | None = None

@dataclass(frozen=True)
class DownloadResult:
    size_bytes: int
    sha256: str
    resolved_audio_url: str
    attempts: int
    frame_count: int | None = None
    format: str = "mp3"
    codec: str = "mpeg-layer-iii"
    extension: str = "mp3"
    validation_level: str = "frame-boundaries"
    page_count: int | None = None

@dataclass
class RunSummary:
    total: int = 0
    downloaded: int = 0
    existing: int = 0
    failed: int = 0
    unexecuted: int = 0
    duplicates: int = 0
    text_only: int = 0
    missing_text_saved: int = 0
    cumulative: int = 0
    audio_requests: int = 0
    references_added: int = 0
    languages_added: int = 0
    active_view: str = "legacy/*"
    blocked: int = 0  # Subset of failed; never add twice to totals.
    stop_reason: str | None = None
    access_decision: dict | None = None
    stopped: bool = False
    interrupted: bool = False
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.interrupted:
            return 130
        if self.stopped:
            return 1
        return 2 if self.failed else 0
