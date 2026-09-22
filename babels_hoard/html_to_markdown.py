"""A small, dependency-free HTML -> Markdown converter (stdlib
``html.parser`` only), good enough for indexed documentation pages: headings,
paragraphs, code blocks, lists, tables (as pipe rows) and links as text.
Not a general-purpose renderer — nav/aside/script/style are dropped.
"""
from __future__ import annotations

from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "nav", "aside", "svg", "button"}
_BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "li", "tr", "blockquote"}


class _MDConverter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip_depth = 0
        self.list_stack: list[str] = []  # 'ul' or 'ol'
        self.pre_depth = 0
        self.link_href: str | None = None

    def _write(self, text: str) -> None:
        if self.pre_depth:
            self.out.append(text)
        else:
            self.out.append(text)

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_d = dict(attrs)
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._write("\n\n" + "#" * level + " ")
        elif tag == "p":
            self._write("\n\n")
        elif tag == "br":
            self._write("  \n")
        elif tag in ("ul", "ol"):
            self.list_stack.append(tag)
            self._write("\n")
        elif tag == "li":
            marker = "- " if (not self.list_stack or self.list_stack[-1] == "ul") else "1. "
            self._write("\n" + marker)
        elif tag in ("pre",):
            self.pre_depth += 1
            self._write("\n\n```\n")
        elif tag == "code" and self.pre_depth == 0:
            self._write("`")
        elif tag in ("strong", "b"):
            self._write("**")
        elif tag in ("em", "i"):
            self._write("_")
        elif tag == "a":
            self.link_href = attrs_d.get("href")
            self._write("[")
        elif tag == "blockquote":
            self._write("\n> ")
        elif tag == "table":
            self._write("\n\n")
        elif tag == "tr":
            self._write("\n")
        elif tag in ("td", "th"):
            self._write(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag in ("ul", "ol") and self.list_stack:
            self.list_stack.pop()
            self._write("\n")
        elif tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
            self._write("\n```\n")
        elif tag == "code" and self.pre_depth == 0:
            self._write("`")
        elif tag in ("strong", "b"):
            self._write("**")
        elif tag in ("em", "i"):
            self._write("_")
        elif tag == "a":
            href = self.link_href
            self.link_href = None
            self._write(f"]({href})" if href else "]")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.pre_depth:
            self.out.append(data)
        else:
            self.out.append(data.replace("\n", " "))


def html_fragment_to_markdown(html: str) -> str:
    conv = _MDConverter()
    try:
        conv.feed(html)
    except Exception:
        pass
    # convert_charrefs=True already decoded entities once; decoding again
    # would turn documented entities (``&amp;lt;``) into markup (``<``).
    text = "".join(conv.out)
    lines = [ln.rstrip() for ln in text.splitlines()]
    # Collapse 3+ blank lines to 1.
    collapsed: list[str] = []
    blank_run = 0
    for ln in lines:
        if ln.strip() == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        collapsed.append(ln)
    return "\n".join(collapsed).strip()
