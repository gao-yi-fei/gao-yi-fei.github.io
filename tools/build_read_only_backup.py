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
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA, WIKI = ROOT / "data", ROOT / "wiki"

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
    return re.sub(r"\[\[(?:module\s+CSS|css)\]\].*?\[\[/(?:module\s+CSS|css)\]\]", "[[backup-notice style]]", source, flags=re.S | re.I)

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
        if low in {"[[footnoteblock]]", "[[toc]]", "[[/toc]]"}:
            notices["widgets"] += 1; continue
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

def kind_label(kind: str) -> str:
    return {"original": "原创页面", "translation": "翻译页面", "fragment": "段落页面"}.get(kind, "其他页面")

def page_author(page: dict[str, Any]) -> str:
    return (((page.get("history_author") or {}).get("author") or {}).get("name") or page.get("history_author_name") or "未捕捉到")

def render_article(page: dict[str, Any], source: str, pages: dict[str, Any], generated: str) -> tuple[str, Counter[str]]:
    body, notices = render_wikitext(source, pages)
    title, name, voters = page.get("title") or page.get("page_name") or "未命名页面", page.get("page_name", ""), page.get("voters") or {}
    tags = "".join(f'<span class="tag">{html.escape(str(tag))}</span>' for tag in page.get("tags") or []) or '<span class="muted">无</span>'
    deleted = '<span class="status deleted">已删除存档</span>' if page.get("archived_deleted") else ""
    warning_labels = ([f"{notices['includes']} 个包含组件"] if notices["includes"] else []) + ([f"{notices['attachments']} 个附件"] if notices["attachments"] else []) + (["专用样式"] if notices["styles"] else [])
    warning = '<aside class="reader-warning">此页部分资源未归档：' + "、".join(warning_labels) + "。文本与源码仍已保留。</aside>" if warning_labels else ""
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)} | SCP基金会 Minecraft 分部只读备份</title><link rel="stylesheet" href="/assets/read-only.css"></head><body><header class="site-header"><a class="brand" href="/">SCP基金会 Minecraft 分部</a><span>只读备份</span><nav><a href="/pages.html?page={html.escape(name, quote=True)}">索引详情</a><a href="/pages.html">页面索引</a><a href="/forum.html">讨论区</a></nav></header><main class="reader-shell"><article class="reader-article"><header class="article-header"><p class="eyebrow">{kind_label(page.get('page_kind', 'other'))} {deleted}</p><h1>{html.escape(title)}</h1><p class="page-name">{html.escape(name)}</p><dl class="article-meta"><div><dt>创建者</dt><dd>{html.escape(page_author(page))}</dd></div><div><dt>评分</dt><dd class="rating">{page.get('rating_text') or 'n/a'} <small>+{voters.get('up', 0)} / -{voters.get('down', 0)}</small></dd></div><div><dt>发布时间</dt><dd>{html.escape(page.get('created_at_beijing') or 'n/a')}</dd></div><div><dt>最近编辑</dt><dd>{html.escape(page.get('last_edited_at_beijing') or 'n/a')}</dd></div></dl><div class="tags">{tags}</div></header>{warning}<section class="article-body">{body or '<p class="muted">该页未保存可渲染的源码。</p>'}</section><details class="source"><summary>查看原始 Wikidot 源码</summary><pre>{html.escape(source)}</pre></details></article></main><footer>只读快照：{html.escape(generated)} · created by piglin · 内容版权仍归原作者与 SCP 基金会 Minecraft 分部。</footer></body></html>''', notices

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--clean", action="store_true"); args = parser.parse_args()
    pages, sources = read_shards(DATA / "details", "pages"), read_shards(DATA / "sources", "sources")
    stats = json.load(gzip.open(DATA / "search-index.json.gz", "rt", encoding="utf-8")).get("stats", {})
    generated = stats.get("generated_at_beijing") or datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
    if args.clean and WIKI.exists(): shutil.rmtree(WIKI)
    WIKI.mkdir(parents=True, exist_ok=True)
    total_notices, missing_sources = Counter[str](), []
    for name, page in sorted(pages.items()):
        source = sources.get(name)
        if source is None: missing_sources.append(name); source = page.get("source_excerpt") or ""
        rendered, notices = render_article(page, source, pages, generated); total_notices.update(notices)
        (WIKI / f"{article_key(name)}.html").write_text(rendered, encoding="utf-8")
    manifest = {"generated_at_beijing": generated, "page_count": len(pages), "source_count": len(sources), "missing_full_sources": missing_sources, "parser_notices": dict(sorted(total_notices.items())), "format": "read-only-wikidot-fallback-v1"}
    (DATA / "read-only-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False)); return 0

if __name__ == "__main__": raise SystemExit(main())
