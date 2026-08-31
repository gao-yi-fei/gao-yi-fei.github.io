import re
import pathlib
import sys


BAD = re.compile(
    r"\[\[(?:include|span|module|a href)|%%title|<style|wj-user-info|<img"
)
ENTITY_TEXT = re.compile(r"&(?:lt|gt|amp|quot|#39);")
RAW_TAGS = re.compile(
    r"<(?:b|i|u|font|center|marquee|iframe|embed|object|video|audio|canvas|"
    r"form|input|button|select|option|textarea|label|style|script|link|meta|"
    r"img|svg)\b",
    re.I,
)


def article_body(text):
    start = text.find('article-body')
    if start < 0:
        return None
    body = text[start : start + 300000]
    # The intentional "view original source" block must not count as residue.
    src_start = body.find('<details class="source">')
    if src_start >= 0:
        src_end = body.find('</details>', src_start)
        if src_end >= 0:
            body = body[:src_start] + body[src_end + len("</details>") :]
    return body


def main():
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("wiki")
    hits = []
    for f in sorted(root.glob("*.html")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        body = article_body(text)
        if body is None:
            continue
        found = sorted({m.group(0)[:80] for m in BAD.finditer(body)})
        if found:
            hits.append((f.name, found[:5]))
    print("bad pages:", len(hits))
    for name, pats in hits:
        print(name, pats)


def fix_unrendered_includes(root):
    """Strip raw include paragraphs from rendered bodies (keeps source view)."""
    pattern = re.compile(r"<p>\[\[include[^\n]*?\]\]</p>\n?")
    fixed = 0
    for f in pathlib.Path(root).glob("*.html"):
        text = f.read_text(encoding="utf-8", errors="replace")
        i = text.find('<details class="source">')
        head, tail = (text[:i], text[i:]) if i >= 0 else (text, "")
        new_head = pattern.sub("", head)
        if new_head != head:
            f.write_text(new_head + tail, encoding="utf-8")
            fixed += 1
            print("fixed", f.name)
    print("fixed files:", fixed)


def scan_entities(root):
    hits = {}
    for f in pathlib.Path(root).glob("*.html"):
        text = f.read_text(encoding="utf-8", errors="replace")
        body = article_body(text)
        if body is None:
            continue
        found = ENTITY_TEXT.findall(body)
        if found:
            hits[f.name] = found[:4]
    print("pages with literal entities in body:", len(hits))
    for name, found in list(hits.items())[:20]:
        print(name, found)


def scan_raw_tags(root):
    hits = {}
    for f in pathlib.Path(root).glob("*.html"):
        text = f.read_text(encoding="utf-8", errors="replace")
        body = article_body(text)
        if body is None:
            continue
        found = sorted({m.lower() for m in RAW_TAGS.findall(body)})
        if found:
            hits[f.name] = found[:8]
    print("pages with suspicious raw tags in body:", len(hits))
    for name, found in list(hits.items())[:20]:
        print(name, found)


def show_context(root, name, pattern, limit=8):
    text = (pathlib.Path(root) / name).read_text(encoding="utf-8", errors="replace")
    body = article_body(text)
    for match in list(pattern.finditer(body))[:limit]:
        start = max(0, match.start() - 140)
        end = match.end() + 140
        print(repr(body[start:end].replace("\n", " ")))
        print("---")


if __name__ == "__main__":
    main()
