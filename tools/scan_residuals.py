import re
import pathlib
import sys


BAD = re.compile(
    r"\[\[(?:include|span|module|a href)|%%title|<style|wj-user-info|<img"
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


if __name__ == "__main__":
    main()
