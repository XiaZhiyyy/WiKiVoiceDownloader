from __future__ import annotations
import copy
import logging
import re
from .errors import SelectionError
from .models import VoiceCandidate, VoiceGroup

log = logging.getLogger(__name__)

def parse_selection(value: str, count: int) -> list[int]:
    value = value.strip()
    if count < 1:
        raise SelectionError("没有可选语音分组。")
    if value.upper() == "A":
        return list(range(count))
    if not re.fullmatch(r"[0-9]+(?:\s*,\s*[0-9]+)*", value):
        raise SelectionError("请输入数字列表（如 1,3）或 A；空选择无效。")
    numbers = {int(v) for v in value.split(",")}
    if not numbers or min(numbers) < 1 or max(numbers) > count:
        raise SelectionError(f"选项必须在 1 至 {count} 之间。")
    return [n - 1 for n in sorted(numbers)]

def selected_candidates(groups: list[VoiceGroup], selection: list[int]
                        ) -> tuple[list[VoiceCandidate], int, int]:
    raw = sorted((c for index in sorted(set(selection)) for c in groups[index].candidates),
                 key=lambda c: c.dom_order)
    from .views import reference_from_candidate, candidate_references
    by_url = {}
    for candidate in raw:
        first = by_url.get(candidate.source_url)
        if first is None:
            by_url[candidate.source_url] = copy.deepcopy(candidate)
            by_url[candidate.source_url].references = candidate_references(candidate)
        else:
            if first.section_id != candidate.section_id:
                raise SelectionError('Select one server before media URL deduplication')
            first.references.extend(candidate_references(candidate))
            if first.text_language and not any(t.language == first.text_language and t.text for t in first.texts):
                target = [t for t in candidate.texts if t.language == first.text_language and t.text]
                if target and candidate.text_status not in {'ambiguous','ambiguous_pairing'}:
                    first.texts.extend(copy.deepcopy(target)); first.text_status = 'present'
            if not first.texts and candidate.texts:
                first.texts = copy.deepcopy(candidate.texts)
                first.text_status = "present"
                first.diagnostics.append("重复引用中的可靠正文补齐了缺失文本。")
            elif first.texts and candidate.texts and first.texts != candidate.texts:
                log.warning("同 URL 引用正文冲突，保留网页顺序中首次可靠正文。")
            if first.group_id != candidate.group_id or first.category != candidate.category:
                log.info("跨分组或台词类型重复引用，保留首次选中归属。")
    return list(by_url.values()), len(raw) - len(by_url), len(raw)
