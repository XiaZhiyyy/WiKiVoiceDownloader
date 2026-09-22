"""Conservative, component-scoped BLHX parser.

Only voice tables are inspected. Unknown group boundaries are explicitly
reported; generic nearby paragraphs are never used as dialogue.
See docs/decisions.md for the distinction between supplied and live evidence.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from bs4 import BeautifulSoup, Tag
from ..errors import AppError, ParseError
from ..models import PageInfo, ParsedPage, VoiceCandidate, VoiceGroup, TextVariant
from ..text_format import extract_text, normalize_line
from ..urls import request_url, is_blhx_page, page_url_identity, check_resource_url

BASE_LABELS = {"原皮", "默认", "基础", "通常", "默认立绘", "原始皮肤"}
VOICE_HEADINGS = {"舰船台词", "角色台词"}
BLOCK_CLASSES = {"ship_word_block", "ship_word_media_wrap"}
CHALLENGE_MARKERS = (
    "cf-chl-", "challenge-platform", "g-recaptcha", "hcaptcha",
    "访问验证", "人机验证", "安全验证", "访问被拒绝", "访问受限",
)
IGNORE_IDS = {"comments", "comment", "mw-navigation", "footer", "toc"}

def is_block(tag) -> bool:
    return isinstance(tag, Tag) and bool(BLOCK_CLASSES.intersection(tag.get("class", [])))

def block_owner(tag: Tag, table: Tag) -> Tag | None:
    for parent in tag.parents:
        if parent is table:
            break
        if is_block(parent):
            return parent
    return None

def stable_group_id(label: str, explicit: str | None = None) -> str:
    if label in BASE_LABELS:
        return "base"
    # Explicit DOM IDs can change; labels are more stable than DOM positions.
    value = explicit or label
    return "skin-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

def heading_text(tag: Tag) -> str:
    return extract_text(tag).removesuffix("展开/折叠").strip()

class BiligameBlhxParser:
    parser_id = "biligame_blhx"
    display_name = "Biligame / BWIKI 碧蓝航线"

    def __init__(self, profile=None):
        from ..profile_loader import builtin_profile
        from ..site_policy import SitePolicy
        self.profile = profile or builtin_profile('biligame_blhx')
        self.policy = SitePolicy(self.profile)
        self.parser_id = self.profile.parser_id

    def _extract_profile_text(self, node):
        clone = copy.deepcopy(node)
        for selector in self.profile.data['selectors']['ignore_nodes']:
            for excluded in clone.select(selector):
                excluded.decompose()
        return extract_text(clone)

    def supports(self, url: str) -> bool:
        return self.policy.supports(url)

    def _page_info(self, soup: BeautifulSoup, page_url: str) -> PageInfo:
        titles = []
        reliable_names = []
        heading = soup.select_one("#firstHeading") or soup.find("h1")
        if heading:
            title = extract_text(heading)
            if title:
                titles.append(title)
        if not titles and soup.title:
            # Remove only an exact known site separator/suffix.
            title = extract_text(soup.title)
            title = re.sub(r"\s*[-–—]\s*碧蓝航线WIKI_BWIKI_哔哩哔哩$", "", title)
            if title:
                titles.append(title)
        # Explicit structured name fields only; never infer from a quote.
        for node in soup.select('[data-character-name]'):
            value = normalize_line(node.get("data-character-name", ""))
            if value:
                reliable_names.append(value)
        for th in soup.find_all("th"):
            if th.find_parent("table", class_="table-ShipWordsTable"):
                continue
            if extract_text(th) in {"角色名", "角色名称", "舰船名称", "名称", "本名"}:
                td = th.find_next_sibling("td")
                if td:
                    value = extract_text(td)
                    if value and len(value) <= 80:
                        reliable_names.append(value)
        for dt in soup.find_all("dt"):
            if extract_text(dt) in {"角色名", "角色名称", "舰船名称"}:
                dd = dt.find_next_sibling("dd")
                if dd and extract_text(dd):
                    reliable_names.append(extract_text(dd))

        canonical = None
        node = soup.find("link", rel=lambda value: value and "canonical" in value)
        if node and node.get("href"):
            try:
                proposed = request_url(node["href"], page_url)
                if self.supports(proposed):
                    canonical = proposed
            except AppError:
                pass

        article_id = None
        for script in soup.find_all("script"):
            # Only extract a numeric JSON field; scripts are never executed.
            found = re.search(r'["\']wgArticleId["\']\s*:\s*(\d+)', script.get_text())
            if found and int(found[1]) > 0:
                article_id = int(found[1])
                break
        identity = (f"biligame:blhx:pageid:{article_id}" if article_id
                    else f"biligame:blhx:url:{page_url_identity(canonical or page_url)}")
        aliases = [f"biligame:blhx:url:{page_url_identity(page_url)}"]
        if canonical:
            aliases.append(f"biligame:blhx:url:{page_url_identity(canonical)}")
        candidates = list(dict.fromkeys(reliable_names + titles))
        reliable_unique = list(dict.fromkeys(reliable_names))
        return PageInfo(
            character_name=candidates[0] if candidates else None,
            name_candidates=candidates,
            source_page_url=page_url, resolved_page_url=page_url,
            page_identity=identity, canonical_url=canonical,
            identity_aliases=list(dict.fromkeys([identity] + aliases)),
            name_reliable=len(reliable_unique) == 1,
        )

    def _group(self, table: Tag, soup: BeautifulSoup, index: int,
               warning: list[str]) -> tuple[str, str]:
        for node in [table, *table.parents]:
            if not isinstance(node, Tag) or node.name in {"html", "body", "[document]"}:
                break
            # These are semantic attributes in offline contract fixtures, not
            # a claim that every live BWIKI page has the same wrapper.
            for attr in ("data-skin-name", "data-group-name"):
                name = normalize_line(node.get(attr, ""))
                if name:
                    name = "原皮" if name in BASE_LABELS else name
                    return stable_group_id(name), name
            classes = set(node.get("class", []))
            if "tabbertab" in classes:
                name = normalize_line(node.get("title", "") or node.get("data-title", ""))
                if name:
                    name = "原皮" if name in BASE_LABELS else name
                    return stable_group_id(name), name
            panel = node.get("role") == "tabpanel" or "tab-pane" in classes
            if panel:
                label_ids = node.get("aria-labelledby", "").split()
                labels = [soup.find(id=value) for value in label_ids]
                names = [extract_text(label) for label in labels if label is not None]
                panel_id = node.get("id")
                if not names and panel_id:
                    for label in soup.find_all(["a", "button"]):
                        linked = (label.get("href") == f"#{panel_id}"
                                  or label.get("data-target") == f"#{panel_id}"
                                  or label.get("aria-controls") == panel_id)
                        is_tab = (label.get("role") == "tab" or label.get("data-toggle") == "tab"
                                  or label.find_parent(class_="nav-tabs") is not None)
                        if linked and is_tab:
                            names.append(extract_text(label))
                if len(set(filter(None, names))) == 1:
                    name = next(filter(None, names))
                    name = "原皮" if name in BASE_LABELS else name
                    return stable_group_id(name), name
                # Never fall back to base for an unlabelled tab.
                break

        previous = table.find_previous(["h2", "h3", "h4", "h5", "h6"])
        if previous:
            name = heading_text(previous)
            # Explicit subheadings under the voice section are valid grouping
            # evidence. Do not treat arbitrary headings as a skin.
            ancestors = [previous]
            current = previous
            while current and current.name != "h2":
                current = current.find_previous(["h2", "h3", "h4", "h5", "h6"])
                if current:
                    ancestors.append(current)
            in_voice = any(heading_text(h) in VOICE_HEADINGS for h in ancestors)
            if in_voice and name not in VOICE_HEADINGS:
                name = re.sub(r"^(?:换装|皮肤|语音)[：:]\s*", "", name)
                if name:
                    name = "原皮" if name in BASE_LABELS else name
                    return stable_group_id(name), name
            # Only the first unwrapped table immediately under a voice heading
            # is a documented conservative base-group inference.
            has_panel = any(isinstance(p, Tag) and (
                p.get("role") == "tabpanel" or
                {"tabbertab", "tab-pane"}.intersection(p.get("class", []))
            ) for p in table.parents)
            if name in VOICE_HEADINGS and index == 0 and not has_panel:
                return "base", "原皮"
        gid = "unknown-" + (str(table.get("id")) if table.get("id") else str(index + 1))
        warning.append(f"第 {index + 1} 个语音表无法可靠确定皮肤，放入未识别分组。")
        return gid, f"未识别分组 {index + 1}"

    @staticmethod
    def _categories(table: Tag) -> dict[int, str]:
        result = {}
        carry, remaining = "未分类", 0
        for row in table.find_all("tr"):
            if row.find_parent("table") is not table:
                continue
            th = next((v for v in row.find_all("th", recursive=False)), None)
            if th is not None:
                category = extract_text(th) or "未分类"
                try:
                    remaining = max(0, int(th.get("rowspan", "1")) - 1)
                except (TypeError, ValueError):
                    remaining = 0
                carry = category
            elif remaining > 0:
                category = carry
                remaining -= 1
            else:
                category = "未分类"
            result[id(row)] = category
        return result

    def parse(self, html: str, page_url: str) -> ParsedPage:
        if not self.supports(page_url):
            raise ParseError("该解析器只接受 Biligame 碧蓝航线角色页。", "unsupported")
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.select(self.profile.data["selectors"]["quote_table"])
        tables = [table for table in tables if not any(
            isinstance(p, Tag) and (p.get("id", "").lower() in IGNORE_IDS
                                    or "comment" in p.get("class", []))
            for p in table.parents)]
        if not tables:
            visible = soup.get_text(" ", strip=True)
            from ..access_detection import classify_access
            decision = classify_access(html)
            if decision.stop_current_job:
                raise ParseError(decision.diagnostic_message, decision.kind)
            if any(s in visible for s in ("暂无配音", "暂无语音", "没有配音")):
                raise ParseError("页面明确标注暂无配音。", "no_audio")
            if len(visible) < 80 or not soup.select_one("#mw-content-text, .mw-parser-output"):
                raise ParseError("页面内容不完整或不是角色页；未找到语音结构。", "incomplete")
            raise ParseError("未找到已支持的语音表；可能是布局变化或该页不支持。", "structure_changed")

        warnings, groups, group_by_id = [], [], {}
        skipped, order, seen_link_nodes, media_links_seen = 0, 0, set(), 0
        for table_index, table in enumerate(tables):
            gid, name = self._group(table, soup, table_index, warnings)
            group = group_by_id.get(gid)
            if group is None:
                group = VoiceGroup(gid, name, table_index)
                groups.append(group)
                group_by_id[gid] = group
            categories = self._categories(table)
            links = table.select(self.profile.data["selectors"]["audio_link"])
            # Own only links whose nearest voice table is this table.
            links = [a for a in links if a.find_parent("table") is table]
            media_links_seen += len(links)

            for block in table.find_all(is_block):
                if block.find_parent("table") is not table:
                    continue
                own_lines = [p for p in block.select(self.profile.data["selectors"]["language_node"])
                             if block_owner(p, table) is block]
                own_links = [a for a in links if block_owner(a, table) is block]
                if own_lines and not own_links:
                    skipped += 1
            # Text-only lines directly in rows still count, without making tasks.
            skipped += sum(1 for p in table.select(self.profile.data["selectors"]["language_node"])
                           if block_owner(p, table) is None and
                           not (p.find_parent("tr") and p.find_parent("tr").select(
                               ".sm-audio-src a[href], audio[src], audio source[src]")))

            for link in links:
                if id(link) in seen_link_nodes:
                    continue
                seen_link_nodes.add(id(link))
                try:
                    source = request_url(link.get("href") or link.get("src"), page_url)
                    self.policy.check(source, "audio")
                except AppError as exc:
                    warnings.append(f"拒绝不安全音频来源：{exc}")
                    continue
                owner = block_owner(link, table)
                row = link.find_parent("tr")
                category = categories.get(id(row), "未分类")
                diagnostics = []
                if category == "未分类":
                    diagnostics.append("台词类型缺失，未分类。")
                lines = []
                if owner is not None:
                    lines = [p for p in owner.select(self.profile.data["selectors"]["language_node"])
                             if block_owner(p, table) is owner]
                    # Find identifiers on the audio or its local wrappers,
                    # stopping at the minimal component boundary.
                    relations = {}
                    current = link
                    while current is not None:
                        for key in ("data-key", "data-key-i"):
                            if key not in relations and current.get(key) is not None:
                                relations[key] = str(current[key])
                        if current is owner:
                            break
                        current = current.parent if isinstance(current.parent, Tag) else None
                    for key, expected in relations.items():
                        explicit = [p for p in lines if p.get(key) is not None]
                        if explicit:
                            lines = [p for p in explicit if str(p[key]) == expected]
                texts = []
                for p in lines:
                    text = self._extract_profile_text(p)
                    if text:
                        language = normalize_line(p.get("data-lang", "")) or None
                        value = TextVariant(language, text)
                        if value not in texts:
                            texts.append(value)
                status = "present" if texts else "missing"
                # Two distinct lines of the same language without a unique
                # relation are competing quotes, not a multilingual pair.
                languages = [v.language for v in texts]
                if len(set(languages)) != len(languages):
                    texts, status = [], "ambiguous"
                    diagnostics.append("局部组件内存在同语言的不同正文，无法唯一关联。")
                if not texts:
                    diagnostics.append("无可靠对应正文；不从相邻组件猜测。")
                candidate = VoiceCandidate(source, gid, name, category, texts,
                                           status, order, diagnostics)
                group.candidates.append(candidate)
                warnings.extend(diagnostics)
                order += 1

        groups = [g for g in groups if g.candidates]
        if not groups:
            if media_links_seen:
                raise ParseError("发现音频控件，但所有来源均无效或被安全规则拒绝。", "unsupported_sources")
            if any(t.select(".ship_word_line") for t in tables):
                raise ParseError("语音表只有文字，没有可用音频；未创建下载任务。", "no_audio")
            raise ParseError("存在语音表但没有已支持的音频组件，可能是布局变化或动态内容缺失。",
                             "structure_changed")
        info = self._page_info(soup, page_url)
        return ParsedPage(info, groups, list(dict.fromkeys(warnings)), skipped,
                          profile_fingerprint=self.profile.fingerprint,
                          profile_id=self.profile.site_id)
