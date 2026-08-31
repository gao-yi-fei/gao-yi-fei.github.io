#!/usr/bin/env python3
"""Build a safe, static reader from the captured SCPPER Wikidot snapshot.

This intentionally does not execute Wikidot CSS, includes, JavaScript, or
attachment URLs. Original source remains available for audit on every page.
"""
from __future__ import annotations

import argparse
import gzip
import html
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup, NavigableString

ROOT = Path(__file__).resolve().parents[1]
DATA, WIKI = ROOT / "data", ROOT / "wiki"
# Always render through Wikijump's FTML parser so every page gets the full
# Wikidot syntax treatment (colors, footnotes, collapsibles, tables, lists,
# quotes, code, ruby, alignment, ...). The sanitized fallback below is only
# used for pages FTML itself fails on.
FTML_MAX_SOURCE_CHARS = None

# These contest/campaign hub pages contain an achievement-table row that makes
# the Wikijump FTML tokenizer spin in a (non-deterministic) infinite loop. They
# are informational index pages, so render them through the Python fallback
# directly instead of letting a hang stall the whole parallel build.
HANG_PAGES = {
    "image-campaign-hub",
    "2024gamerules-contest-hub",
    "2025-reminisence-contest-hub",
    "ancient-city-contest-hub",
}

def read_shards(directory: Path, key: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for path in sorted(directory.glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            result.update(json.load(handle).get(key, {}))
    return result

def article_key(page_name: str) -> str:
    """Reversible URL/file-system-safe UTF-8 name on every platform."""
    output: list[str] = []
    for byte in page_name.encode("utf-8"):
        output.append(chr(byte) if ((48 <= byte <= 57) or (65 <= byte <= 90) or (97 <= byte <= 122) or byte in b"._-") else f"~{byte:02x}")
    return "".join(output) or "unnamed"

def article_href(page_name: str) -> str:
    return f"/wiki/{article_key(page_name)}.html"

def page_name_from_link(target: str) -> str | None:
    target = target.strip()
    if target.startswith(("http://", "https://")):
        parsed = urlparse(target)
        return parsed.path.strip("/").split("/")[-1] if parsed.netloc.lower().endswith("wikidot.com") else None
    if target.startswith(("/", "#")) or "://" in target:
        return None
    return target

def inline_wikitext(value: str, pages: dict[str, Any], notices: Counter[str]) -> str:
    text = html.escape(value.strip())
    text = re.sub(r"##[^#\n]*##", "", text)
    text = re.sub(r"\[\[backup-footnote\s+(.*?)\]\]", lambda m: f'<sup class="footnote">{html.escape(html.unescape(m.group(1)).strip())}</sup>', text, flags=re.I)
    def triple(match: re.Match[str]) -> str:
        raw = html.unescape(match.group(1)).strip()
        target, separator, label = raw.partition("|")
        label = label if separator else target
        local_name = page_name_from_link(target)
        if local_name and local_name in pages:
            return f'<a href="{article_href(local_name)}">{html.escape(label)}</a>'
        if target.startswith(("http://", "https://")):
            return f'<a href="{html.escape(target, quote=True)}" rel="noreferrer">{html.escape(label)}</a>'
        return html.escape(label)
    text = re.sub(r"\[\[\[([^\]]+)\]\]\]", triple, text)
    def external(match: re.Match[str]) -> str:
        url, label = html.unescape(match.group(1)), html.unescape(match.group(2) or match.group(1))
        return f'<a href="{html.escape(url, quote=True)}" rel="noreferrer">{html.escape(label)}</a>'
    text = re.sub(r"(?<!\[)\[(https?://[^\s\]]+)(?:\s+([^\]]+))?\]", external, text)
    text = re.sub(r"\[\[\*?user\s+([^\]]+)\]\]", lambda m: f'<span class="wiki-user">{html.escape(html.unescape(m.group(1)).strip())}</span>', text, flags=re.I)
    def image(match: re.Match[str]) -> str:
        notices["attachments"] += 1
        name = html.unescape(match.group(1)).strip().split()[0] if match.group(1).strip() else "未命名附件"
        return f'<span class="missing-resource">图片附件未归档：{html.escape(name)}</span>'
    text = re.sub(r"\[\[image\s+([^\]]*)\]\]", image, text, flags=re.I)
    def module(match: re.Match[str]) -> str:
        parts = html.unescape(match.group(1)).strip().split(maxsplit=1)
        kind = parts[1].lower() if len(parts) > 1 else ""
        if kind in {"rate", "rating"}:
            notices["rating_modules"] += 1
            return '<span class="module-note">评分组件已由页首快照替代</span>'
        notices["modules"] += 1
        return '<span class="module-note">动态模块在只读备份中已省略</span>'
    text = re.sub(r"\[\[(module\s+[^\]]+)\]\]", module, text, flags=re.I)
    text = re.sub(r"\[\[size(?:\s+[^\]]+)?\]\]|\[\[/size\]\]", "", text, flags=re.I)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!:)//(.+?)//", r"<em>\1</em>", text)
    return re.sub(r"--(.+?)--", r"<s>\1</s>", text)

def prepare_source(source: str, notices: Counter[str]) -> str:
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    source = re.sub(r"\[\[!--.*?--\]\]", "", source, flags=re.S)
    source = re.sub(r"\[\[footnote\]\](.*?)\[\[/footnote\]\]", lambda m: "[[backup-footnote " + re.sub(r"\s+", " ", m.group(1).strip()) + "]]", source, flags=re.S | re.I)
    def include(match: re.Match[str]) -> str:
        notices["includes"] += 1
        return "[[backup-notice include " + (re.sub(r"\s+.*", "", match.group(1).strip()) or "未命名组件") + "]]"
    source = re.sub(r"\[\[include\s+([^\]\n]+)\]\]", include, source, flags=re.I)
    # A component call may contain nested user/page links. Its terminator is a
    # standalone closing line. Compact calls are removed first so they cannot
    # be mistaken for the opening line of the following multiline component.
    source = re.sub(r"\[\[include\s+(.+?)\n\s*\]\]", include, source, flags=re.S | re.I)
    source = re.sub(r"\[\[module\s+(?:css|listpages)\b[^\]]*\]\].*?\[\[/module\]\]", "", source, flags=re.S | re.I)
    source = re.sub(r"\[\[module\s+(?:rate|listusers)\b[^\]]*\]\]|\[\[/module\]\]", "", source, flags=re.I)
    return source

def render_wikitext(source: str, pages: dict[str, Any]) -> tuple[str, Counter[str]]:
    notices: Counter[str] = Counter()
    source = prepare_source(source, notices)
    output: list[str] = []
    wrappers: list[str] = []
    open_table = False
    def close_table() -> None:
        nonlocal open_table
        if open_table:
            output.append("</tbody></table>")
            open_table = False
    for raw in source.split("\n"):
        line, low = raw.strip(), raw.strip().lower()
        if not line:
            close_table(); continue
        if low.startswith("[[backup-notice include "):
            close_table(); name = line[len("[[backup-notice include "):-2].strip()
            output.append(f'<aside class="backup-note">包含组件未归档：<code>{html.escape(name)}</code></aside>'); continue
        if low == "[[backup-notice style]]":
            close_table(); notices["styles"] += 1; output.append('<aside class="backup-note">原页面专用样式在只读备份中已省略。</aside>'); continue
        if low in {"[[=]]", "[[>]]", "[[<]]", "[[>>]]", "[[<<]]", "[[==]]"}:
            close_table(); output.append('<div class="wiki-aligned">'); wrappers.append("div"); continue
        if low in {"[[/=]]", "[[/>]]", "[[/<]]", "[[/==]]"}:
            close_table()
            if wrappers: output.append("</div>"); wrappers.pop()
            continue
        if low == "[[tabview]]":
            close_table(); output.append('<div class="tabview">'); wrappers.append("tabview"); continue
        if low.startswith("[[tab "):
            close_table(); output.append(f'<section class="wiki-tab"><h2>{inline_wikitext(line[6:-2].strip(), pages, notices)}</h2>'); wrappers.append("tab"); continue
        if low == "[[/tab]]":
            close_table()
            if wrappers and wrappers[-1] == "tab": output.append("</section>"); wrappers.pop()
            continue
        if low in {"[[/tabview]]", "[[/tabs]]"}:
            close_table()
            if wrappers and wrappers[-1] == "tabview": output.append("</div>"); wrappers.pop()
            continue
        if low.startswith("[[collapsible"):
            close_table(); title = re.search(r'(?:show|hide)="([^"]+)"', line, flags=re.I)
            output.append(f'<details class="wiki-collapsible"><summary>{html.escape(title.group(1) if title else "展开内容")}</summary>'); wrappers.append("details"); continue
        if low == "[[/collapsible]]":
            close_table()
            if wrappers and wrappers[-1] == "details": output.append("</details>"); wrappers.pop()
            continue
        if low.startswith("[[div"):
            close_table(); output.append('<div class="wiki-block">'); wrappers.append("div"); continue
        if low == "[[/div]]":
            close_table()
            if wrappers: output.append("</div>"); wrappers.pop()
            continue
        if low in {"[[footnoteblock]]", "[[/toc]]"}:
            notices["widgets"] += 1; continue
        if low == "[[toc]]":
            # The real TOC is assembled after headings have stable IDs.
            notices["widgets"] += 1; output.append('<div class="wiki-toc-placeholder"></div>'); continue
        if re.fullmatch(r"-{4,}", line):
            close_table(); output.append("<hr>"); continue
        heading = re.match(r"^(\+{1,6})\s+(.+)$", line)
        if heading:
            close_table(); level = min(6, len(heading.group(1)) + 1)
            output.append(f"<h{level}>{inline_wikitext(heading.group(2), pages, notices)}</h{level}>"); continue
        if line.startswith("||"):
            if not open_table: output.append('<table class="wiki-table"><tbody>'); open_table = True
            cells = [cell.strip() for cell in line.strip("|").split("||")]; tag = "th" if cells and cells[0].startswith("~") else "td"
            output.append("<tr>" + "".join(f"<{tag}>{inline_wikitext(cell.lstrip('~').strip(), pages, notices)}</{tag}>" for cell in cells) + "</tr>"); continue
        close_table()
        if line.startswith("> "): output.append(f"<blockquote>{inline_wikitext(line[2:], pages, notices)}</blockquote>")
        elif re.match(r"^[*#]\s+", line): output.append(f'<p class="wiki-list">{inline_wikitext(line[2:], pages, notices)}</p>')
        elif re.fullmatch(r"@+", line): notices["spacers"] += 1
        else: output.append(f"<p>{inline_wikitext(raw, pages, notices)}</p>")
    close_table()
    while wrappers:
        kind = wrappers.pop(); output.append("</details>" if kind == "details" else "</section>" if kind == "tab" else "</div>")
    return "\n".join(output), notices

def canonical_page_name(value: str, pages: dict[str, Any]) -> str | None:
    candidate = value.strip().strip("/")
    if candidate.lower() in {name.lower(): name for name in pages}:
        return {name.lower(): name for name in pages}[candidate.lower()]
    return None

def sanitize_ftml_html(fragment: str, pages: dict[str, Any]) -> str:
    """Keep Wikijump's rendered semantics but strip everything that is not
    safe or usable in a static offline reader (scripts, remote media, dynamic
    widgets, raw styling beyond text colors, and leftover wikitext tokens).
    """
    soup = BeautifulSoup(fragment, "html.parser")
    root = soup.find("wj-body") or soup
    aliases = {name.casefold(): name for name in pages}
    allowed = {"a", "blockquote", "br", "code", "details", "div", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "ol", "p", "pre", "s", "section", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "th", "thead", "tr", "ul"}
    style_ok = {"color", "font-size", "background-color", "text-align", "font-weight", "font-style", "text-decoration", "font-family", "line-height", "letter-spacing"}

    # FTML custom elements that need to become plain, static equivalents.
    for tag in list(root.find_all(["style", "script", "iframe", "img", "svg", "link", "meta", "template", "video", "audio", "object", "embed", "wj-code-copy", "wj-code-panel", "wj-code-language", "wj-footnote-ref-tooltip", "wj-title", "wj-collapsible-hide-text"])):
        tag.decompose()
    for tag in list(root.find_all(["wj-footnote-ref", "wj-footnote-ref-marker", "wj-footnote-list-item-marker"])):
        is_marker = tag.name in {"wj-footnote-ref-marker", "wj-footnote-list-item-marker"}
        tag.name = "sup" if is_marker else "span"
        tag["class"] = ["footnote-ref"] if not is_marker else ["footnote-marker"]
    for tag in list(root.find_all(["wj-footnote-list"])):
        tag.name = "div"; tag["class"] = ["footnote-list"]
    for tag in list(root.find_all(["wj-footnote-list-item"])):
        tag.name = "li"
    for tag in list(root.find_all(["wj-collapsible"])):
        tag.name = "details"; tag["class"] = ["wiki-collapsible"]
    for tag in list(root.find_all(["wj-collapsible-button"])):
        tag.name = "summary"
        tag["class"] = []
        for sub in list(tag.find_all(["wj-collapsible-show-text", "wj-collapsible-hide-text"])):
            sub.name = "span"; sub["class"] = ["show-text"]
    for tag in list(root.find_all(["wj-collapsible-content"])):
        tag.name = "div"
    for tag in list(root.find_all(["wj-code"])):
        tag.name = "div"; tag["class"] = ["wiki-code"]
    for tag in list(root.find_all(["wj-user-info"])):
        tag.name = "span"; tag["class"] = ["wiki-user"]
    for tag in list(root.find_all(["wj-math"])):
        tag.name = "code"; tag["class"] = ["wj-math"]
    for tag in list(root.find_all(True)):
        name = tag.name.lower()
        if name == "wj-body":
            continue
        if name not in allowed:
            tag.unwrap()
            continue
        classes = set(tag.get("class") or [])
        mapped: list[str] = []
        if "wj-align-center" in classes:
            mapped.append("wiki-aligned")
        if "wj-align-right" in classes:
            mapped.append("wiki-align-right")
        if "wj-collapsible" in classes:
            mapped.append("wiki-collapsible")
        if any(item.startswith("wj-tab") for item in classes):
            mapped.append("wiki-tab")
        if "wj-table" in classes:
            mapped.append("wiki-table")
        mapped.extend(item for item in classes if item in {"small", "ruby", "rt", "normal-link", "wiki-user", "missing-resource", "footnote-ref", "footnote-marker", "footnote-list", "show-text", "wiki-code", "wiki-table", "wiki-align-right"})
        if "wiki-user" in classes:
            tag.name = "a"
            tag["href"] = "/users.html?user=" + quote(tag.get_text(" ", strip=True), safe="")
            mapped = ["wiki-user"]
        if mapped:
            tag["class"] = mapped
        elif tag.has_attr("class"):
            del tag["class"]
        if tag.has_attr("style"):
            kept = []
            for part in (tag["style"] or "").split(";"):
                prop, sep, value = part.partition(":")
                if sep and prop.strip().lower() in style_ok:
                    kept.append(f"{prop.strip()}: {value.strip()}")
            if kept:
                tag["style"] = "; ".join(kept)
            else:
                del tag["style"]
        for attr in list(tag.attrs):
            if attr not in {"class", "style"} and not (tag.name == "a" and attr == "href"):
                del tag[attr]
        if tag.name != "a":
            continue
        href = str(tag.get("href") or "").strip()
        if href.startswith("/users.html?user="):
            tag["href"] = href
            continue
        candidate = None
        if href.startswith(("http://", "https://")):
            parsed = urlparse(href)
            if parsed.netloc.casefold().endswith("wikidot.com"):
                candidate = parsed.path.strip("/").split("/")[-1]
        elif href.startswith("/"):
            candidate = href.strip("/")
        if candidate and candidate.casefold() in aliases:
            tag["href"] = article_href(aliases[candidate.casefold()])
        elif href.startswith(("http://", "https://")):
            tag["href"] = href
            tag["rel"] = "noreferrer"
        else:
            tag.unwrap()
    for node in list(root.find_all(string=True)):
        cleaned = re.sub(r"\[\[[^\]\n]*\]\]", "", str(node))
        cleaned = re.sub(r"\[\[[^\]\n]*$", "", cleaned)
        cleaned = re.sub(r"\[\[include[^\n]*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"##[^#\n]*##", "", cleaned)
        cleaned = re.sub(r"%%[A-Za-z0-9_|%{}: .+-]+%%", "", cleaned)
        cleaned = re.sub(r"@{3,}", "", cleaned)
        if cleaned != node:
            node.replace_with(NavigableString(cleaned))
    for tag in reversed(root.find_all(True)):
        if tag.name not in {"br", "hr"} and not tag.get_text(strip=True) and not tag.find(["br", "hr"]):
            tag.decompose()
    return "".join(str(child) for child in root.contents).strip()

def enhance_article_body(body: str, source: str) -> str:
    """Add stable local anchors and a static TOC to sanitized FTML output."""
    soup = BeautifulSoup(body, "html.parser")
    headings = []
    used: set[str] = set()
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        label = heading.get_text(" ", strip=True) or "section"
        base = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", label, flags=re.UNICODE).strip("-").lower() or "section"
        anchor = base
        index = 2
        while anchor in used:
            anchor = f"{base}-{index}"; index += 1
        used.add(anchor); heading["id"] = anchor
        headings.append((int(heading.name[1:]), label, anchor))
    if "[[toc" in source.lower() and headings:
        nav = soup.new_tag("nav", attrs={"class": "wiki-toc", "aria-label": "目录"})
        title = soup.new_tag("strong"); title.string = "目录"; nav.append(title)
        ul = soup.new_tag("ul")
        for level, label, anchor in headings:
            li = soup.new_tag("li", attrs={"class": f"toc-level-{level}"})
            link = soup.new_tag("a", href=f"#{anchor}"); link.string = label
            li.append(link); ul.append(li)
        nav.append(ul)
        placeholder = soup.find(class_="wiki-toc-placeholder")
        if placeholder: placeholder.replace_with(nav)
        else: soup.insert(0, nav)
    else:
        for placeholder in soup.select(".wiki-toc-placeholder"): placeholder.decompose()
    return "".join(str(child) for child in soup.contents).strip()
    for tag in list(root.find_all(True)):
        name = tag.name.lower()
        if name not in allowed:
            tag.unwrap()
            continue
        classes = set(tag.get("class") or [])
        mapped: list[str] = []
        if "wj-collapsible-hide-text" in classes:
            tag.decompose()
            continue
        if "wj-align-center" in classes:
            mapped.append("wiki-aligned")
        if "wj-collapsible" in classes:
            mapped.append("wiki-collapsible")
        if any(item.startswith("wj-tabview") for item in classes):
            mapped.append("tabview")
        if any(item.startswith("wj-tab") for item in classes):
            mapped.append("wiki-tab")
        mapped.extend(item for item in classes if item in {"small", "ruby", "rt", "normal-link", "wiki-user", "missing-resource"})
        if mapped:
            tag["class"] = mapped
        elif tag.has_attr("class"):
            del tag["class"]
        for attr in list(tag.attrs):
            if attr != "class" and not (tag.name == "a" and attr == "href"):
                del tag[attr]
        if tag.name != "a":
            continue
        href = str(tag.get("href") or "").strip()
        candidate = None
        if href.startswith(("http://", "https://")):
            parsed = urlparse(href)
            if parsed.netloc.casefold().endswith("wikidot.com"):
                candidate = parsed.path.strip("/").split("/")[-1]
        elif href.startswith("/"):
            candidate = href.strip("/")
        if candidate and candidate.casefold() in aliases:
            tag["href"] = article_href(aliases[candidate.casefold()])
        elif href.startswith(("http://", "https://")):
            tag["href"] = href
            tag["rel"] = "noreferrer"
        else:
            tag.unwrap()
    for node in list(root.find_all(string=True)):
        cleaned = re.sub(r"\[\[[^\]\n]*\]\]", "", str(node))
        cleaned = re.sub(r"\[\[[^\]\n]*$", "", cleaned)
        cleaned = re.sub(r"\[\[include[^\n]*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"##[^#\n]*##", "", cleaned)
        cleaned = re.sub(r"%%[A-Za-z0-9_|%{}: .+-]+%%", "", cleaned)
        if cleaned != node:
            node.replace_with(NavigableString(cleaned))
    for tag in reversed(root.find_all(True)):
        if tag.name not in {"br", "hr"} and not tag.get_text(strip=True) and not tag.find(["br", "hr"]):
            tag.decompose()
    return "".join(str(child) for child in root.contents).strip()

def render_ftml_batch(records: list[dict[str, Any]], pages: dict[str, Any]) -> dict[str, tuple[str, Counter[str]]]:
    if not records:
        return {}
    records_by_name = {record["name"]: record for record in records}
    ftml_records = [record for record in records if record["name"] not in HANG_PAGES]
    if len(ftml_records) != len(records):
        print(f"Skipping FTML for {len(records) - len(ftml_records)} known-hang pages; using Python fallback", flush=True)
    command = ["node", str(ROOT / "tools" / "ftml_render_batch.mjs")]
    # FTML is CPU-bound. This is a one-time frozen-snapshot build, so use the
    # available CPU budget to parse independent pages concurrently.
    workers = max(1, min(os.cpu_count() or 4, len(ftml_records), 32))
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(workers)]
    for index, record in enumerate(sorted(ftml_records, key=lambda item: len(item["source"]), reverse=True)):
        buckets[index % len(buckets)].append(record)
    BUCKET_TIMEOUT = 120
    SINGLE_TIMEOUT = 20

    def run_bucket(bucket: list[dict[str, Any]], timeout: int) -> dict[str, Any]:
        response = subprocess.run(command, input=json.dumps(bucket, ensure_ascii=False), text=True, encoding="utf-8", capture_output=True, cwd=ROOT, check=True, timeout=timeout)
        return json.loads(response.stdout)

    def render_bucket(bucket: list[dict[str, Any]]) -> dict[str, Any]:
        """Render a bucket, recursively halving on timeout so a single page
        that hangs the FTML WASM cannot stall the whole build."""
        try:
            return run_bucket(bucket, BUCKET_TIMEOUT)
        except subprocess.TimeoutExpired:
            hang_names = [r["name"] for r in bucket if r["name"] in HANG_PAGES]
            if hang_names:
                # Known-hang pages stay in the bucket (timeout keeps them from
                # stalling siblings); they are recorded as failed below.
                pass
            if len(bucket) == 1:
                print(f"FTML hung on {bucket[0]['name']}; using Python fallback", flush=True)
                return {}
            mid = len(bucket) // 2
            half = render_bucket(bucket[:mid]) if mid else {}
            half.update(render_bucket(bucket[mid:]))
            return half
    with ThreadPoolExecutor(max_workers=len(buckets)) as executor:
        parts = list(executor.map(render_bucket, buckets))
    payload = {name: item for part in parts for name, item in part.items()}
    for name in records_by_name:
        if name in HANG_PAGES:
            payload[name] = {"html": "", "removed": {}, "errors": 1, "error": "FTML hang blacklist"}
    for name in records_by_name:
        if name not in payload:
            payload[name] = {"html": "", "removed": {}, "errors": 1, "error": "FTML timeout"}
    rendered: dict[str, tuple[str, Counter[str]]] = {}
    # Sanitizing the parsed HTML (BeautifulSoup, per page) is independent and
    # is itself the second biggest CPU cost after FTML, so run it concurrently
    # instead of serially in the caller thread.
    def sanitize_one(item: tuple[str, dict[str, Any]]) -> tuple[str, tuple[str, Counter[str]]]:
        name, item = item
        notices = Counter(item.get("removed") or {})
        if item.get("errors"):
            notices["parser_errors"] += int(item["errors"])
        html = item.get("html") or ""
        if item.get("error") or not html.strip():
            # FTML hard-failed for this page; use the sanitized fallback so it
            # still renders instead of coming out blank.
            fallback_body, fallback_notices = render_wikitext(records_by_name.get(name, {}).get("source") or "", pages)
            notices.update(fallback_notices)
            html = fallback_body
        clean = sanitize_ftml_html(html, pages)
        return name, (enhance_article_body(clean, records_by_name.get(name, {}).get("source") or ""), notices)

    with ThreadPoolExecutor(max_workers=min(32, len(payload))) as executor:
        for name, value in executor.map(sanitize_one, payload.items()):
            rendered[name] = value
    return rendered

def kind_label(kind: str) -> str:
    return {"original": "原创页面", "translation": "翻译页面", "fragment": "段落页面"}.get(kind, "其他页面")

def page_author(page: dict[str, Any]) -> str:
    return (((page.get("history_author") or {}).get("author") or {}).get("name") or page.get("history_author_name") or "未捕捉到")

def render_article(page: dict[str, Any], source: str, pages: dict[str, Any], generated: str, body: str, notices: Counter[str]) -> str:
    title, name, voters = page.get("title") or page.get("page_name") or "未命名页面", page.get("page_name", ""), page.get("voters") or {}
    tags = "".join(f'<span class="tag">{html.escape(str(tag))}</span>' for tag in page.get("tags") or []) or '<span class="muted">无</span>'
    deleted = '<span class="status deleted">已删除存档</span>' if page.get("archived_deleted") else ""
    warning_labels = ([f"{notices['includes']} 个包含组件"] if notices["includes"] else []) + ([f"{notices['modules']} 个动态模块"] if notices["modules"] else []) + (["嵌入 HTML"] if notices["html"] else [])
    warning = '<aside class="reader-warning">此页动态内容未在只读备份中运行：' + "、".join(warning_labels) + "。正文与原始源码仍已保留。</aside>" if warning_labels else ""
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)} | SCP基金会 Minecraft 分部只读备份</title><link rel="stylesheet" href="/assets/read-only.css"></head><body><header class="site-header"><a class="brand" href="/">SCP基金会 Minecraft 分部</a><span>只读备份</span><nav><a href="/pages.html?page={html.escape(name, quote=True)}">索引详情</a><a href="/pages.html">页面索引</a><a href="/forum.html">讨论区</a></nav></header><main class="reader-shell"><article class="reader-article"><header class="article-header"><p class="eyebrow">{kind_label(page.get('page_kind', 'other'))} {deleted}</p><h1>{html.escape(title)}</h1><p class="page-name">{html.escape(name)}</p><dl class="article-meta"><div><dt>创建者</dt><dd>{html.escape(page_author(page))}</dd></div><div><dt>评分</dt><dd class="rating">{page.get('rating_text') or 'n/a'} <small>+{voters.get('up', 0)} / -{voters.get('down', 0)}</small></dd></div><div><dt>发布时间</dt><dd>{html.escape(page.get('created_at_beijing') or 'n/a')}</dd></div><div><dt>最近编辑</dt><dd>{html.escape(page.get('last_edited_at_beijing') or 'n/a')}</dd></div></dl><div class="tags">{tags}</div></header>{warning}<section class="article-body">{body or '<p class="muted">该页未保存可渲染的源码。</p>'}</section><details class="source"><summary>查看原始 Wikidot 源码</summary><pre>{html.escape(source)}</pre></details></article></main><footer>只读快照：{html.escape(generated)} · created by piglin · 内容版权仍归原作者与 SCP 基金会 Minecraft 分部。</footer></body></html>'''

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--clean", action="store_true"); parser.add_argument("--only", action="append", default=[]); args = parser.parse_args()
    pages, sources = read_shards(DATA / "details", "pages"), read_shards(DATA / "sources", "sources")
    stats = json.load(gzip.open(DATA / "search-index.json.gz", "rt", encoding="utf-8")).get("stats", {})
    generated = stats.get("generated_at_beijing") or datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
    if args.clean and WIKI.exists(): shutil.rmtree(WIKI)
    WIKI.mkdir(parents=True, exist_ok=True)
    total_notices, missing_sources = Counter[str](), []
    selected = {name: page for name, page in pages.items() if not args.only or name in args.only}
    records: list[dict[str, Any]] = []
    for name, page in selected.items():
        source = sources.get(name)
        if source is None: missing_sources.append(name); source = page.get("source_excerpt") or ""
        records.append({"name": name, "title": page.get("title") or name, "tags": page.get("tags") or [], "rating": page.get("rating") or 0, "source": source})
    parsed = render_ftml_batch(records, pages)
    for name, page in sorted(selected.items()):
        source = sources.get(name) or page.get("source_excerpt") or ""
        body, notices = parsed[name]
        total_notices.update(notices)
        rendered = render_article(page, source, pages, generated, body, notices)
        (WIKI / f"{article_key(name)}.html").write_text(rendered, encoding="utf-8")
    manifest = {"generated_at_beijing": generated, "page_count": len(pages), "source_count": len(sources), "missing_full_sources": missing_sources, "parser_notices": dict(sorted(total_notices.items())), "format": "wikijump-ftml-read-only-v2"}
    (DATA / "read-only-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False)); return 0

if __name__ == "__main__": raise SystemExit(main())
