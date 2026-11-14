"""Spans -> Markdown inline text and code fences, parameterised by an InlineWriter."""
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from .config import NBSP, ZWSP, md_url
from .model import Span

Resolver = Callable[[str, str], str | None]          # (href, plain text) -> GFM link target or None
_UNDERSCORE_RE = re.compile(r"(?<!\w)_|_(?!\w)")
_LT_RE = re.compile(r"<(?=[A-Za-z/!?])")
_BRACKET_RE = re.compile(r"\](?=\()")
_ENTITY_RE = re.compile(r"&(?=#?[A-Za-z0-9]+;)")
_SPACES_RE = re.compile(r" {2,}")
_ORDERED_RE = re.compile(r"^(\d+)([.)]) ")


def plain_text(spans: list[Span]) -> str:
    """Return the normalised plain text of spans."""
    text = "".join(s.text for s in spans).replace(ZWSP, "")
    text = text.replace(NBSP, " ").replace("\n", " ")
    return _SPACES_RE.sub(" ", text).strip()


def _key(span: Span) -> tuple:
    return (span.bold, span.italic, span.code, span.href)


def _with_text(span: Span, text: str) -> Span:
    return Span(text=text, bold=span.bold, italic=span.italic, code=span.code, href=span.href)


def _normalise(spans: list[Span]) -> list[Span]:
    """Steps 1-3: clean characters, merge equal neighbours, collapse spaces."""
    cleaned = []
    for s in spans:
        text = s.text.replace(ZWSP, "").replace(NBSP, " ").replace("\n", " ").replace("\t", " ")
        if text:
            cleaned.append(_with_text(s, text))
    merged: list[Span] = []
    for s in cleaned:
        if merged and _key(merged[-1]) == _key(s):
            merged[-1] = _with_text(s, merged[-1].text + s.text)
        else:
            merged.append(s)
    out: list[Span] = []
    for s in merged:
        text = _SPACES_RE.sub(" ", s.text)
        if out and out[-1].text.endswith(" "):
            text = text.lstrip(" ")
        if text:
            out.append(_with_text(s, text))
    while out and not out[0].text.strip(" "):
        out.pop(0)
    while out and not out[-1].text.strip(" "):
        out.pop()
    if out:
        out[0] = _with_text(out[0], out[0].text.lstrip(" "))
        out[-1] = _with_text(out[-1], out[-1].text.rstrip(" "))
    return out


def escape_text(text: str, *, all_brackets: bool = False) -> str:
    """Escape Markdown-significant characters in non-code text.

    all_brackets escapes every [ and ] (Obsidian) instead of only "](".
    """
    text = text.replace("\\", "\\\\").replace("*", "\\*").replace("`", "\\`")
    text = _UNDERSCORE_RE.sub(r"\\_", text)
    if all_brackets:
        text = text.replace("[", "\\[").replace("]", "\\]")
    else:
        text = _BRACKET_RE.sub(r"\\]", text)
    text = _ENTITY_RE.sub(r"\\&", text)
    return _LT_RE.sub(r"\\<", text)


def code_span(text: str) -> str:
    """Wrap text in enough backticks, padding when it starts or ends with one."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


@dataclass(frozen=True)
class InlineWriter:
    """How one flavour writes inline text: escaping of non-code text and links."""
    escape: Callable[[str], str]
    link: Callable[[str, str, str], str]               # (href, rendered inner, plain text) -> output
    merge_emphasis: bool = False                       # Obsidian: one ** pair over neighbours differing in code
    code_link: Callable[[str], str | None] | None = None   # code text -> link destination


def md_link_writer(resolve: Resolver, escape: Callable[[str], str] = escape_text) -> InlineWriter:
    """Writer producing [inner](target) links from a string resolver; unresolved links stay plain."""
    def link(href: str, inner: str, plain: str) -> str:
        target = resolve(href, plain)
        return f"[{inner}]({md_url(target)})" if target is not None else inner
    return InlineWriter(escape, link)


NO_LINKS = md_link_writer(lambda href, text: None)


def _marks(span: Span) -> str:
    return "***" if span.bold and span.italic else "**" if span.bold else "*" if span.italic else ""


def _wrap(text: str, marks: str) -> str:
    """Put marks around text, keeping its leading and trailing spaces outside them."""
    core = text.strip(" ")
    if not core:
        return text
    lead = text[: len(text) - len(text.lstrip(" "))]
    trail = text[len(text.rstrip(" ")):]
    return f"{lead}{marks}{core}{marks}{trail}"


CodeLink = Callable[[str], str | None] | None


def _code(span: Span, core: str, code_link: CodeLink) -> str:
    """A code span, written as [`core`](dest) when code_link gives a destination (never for bold spans)."""
    dest = code_link(core) if code_link is not None and not span.bold else None
    return code_span(core) if dest is None else f"[{code_span(core)}]({dest})"


def _body(span: Span, escape: Callable[[str], str], code_link: CodeLink = None) -> str:
    core = span.text.strip(" ")
    if not core:
        return span.text
    lead = span.text[: len(span.text) - len(span.text.lstrip(" "))]
    trail = span.text[len(span.text.rstrip(" ")):]
    return lead + (_code(span, core, code_link) if span.code else escape(core)) + trail


def _render_one(span: Span, escape: Callable[[str], str], code_link: CodeLink = None) -> str:
    return _wrap(_body(span, escape, code_link), _marks(span))


def _punctuation_only(run: list[Span]) -> bool:
    """A run without code whose text, spaces removed, is only Unicode punctuation."""
    text = "".join(s.text for s in run).replace(" ", "")
    return bool(text) and not any(s.code for s in run) and all(unicodedata.category(c)[0] == "P" for c in text)


def _render_merged(group: list[Span], escape: Callable[[str], str], code_link: CodeLink = None) -> str:
    """Neighbours with the same emphasis share one pair of marks: **`x`y** instead of **`x`****y**.

    A run of punctuation only is written without marks: **.** would show literal asterisks.
    """
    out, i = [], 0
    while i < len(group):
        j = i + 1
        while j < len(group) and _marks(group[j]) == _marks(group[i]):
            j += 1
        body = "".join(_body(s, escape, code_link) for s in group[i:j])
        out.append(body if _punctuation_only(group[i:j]) else _wrap(body, _marks(group[i])))
        i = j
    return "".join(out)


def _line_start(text: str) -> str:
    if text[:1] in ("#", ">", "-", "+"):
        return "\\" + text
    m = _ORDERED_RE.match(text)
    if m:
        return f"{m.group(1)}\\{text[len(m.group(1)):]}"
    return text


def _groups(norm: list[Span], *, join_plain: bool = False) -> Iterator[list[Span]]:
    """Split normalised spans into runs; neighbours with the same href form one link.

    join_plain also joins neighbouring non-link spans into one run (for emphasis merging).
    """
    i = 0
    while i < len(norm):
        j = i + 1
        if norm[i].href is not None or join_plain:
            while j < len(norm) and norm[j].href == norm[i].href:
                j += 1
        yield norm[i:j]
        i = j


def link_groups(spans: list[Span]) -> list[tuple[str, str]]:
    """(href, plain link text) of every link that render_spans would resolve, in order."""
    return [(g[0].href, plain_text(g)) for g in _groups(_normalise(spans)) if g[0].href is not None]


def render_spans(spans: list[Span], writer: InlineWriter, *, at_line_start: bool = True) -> str:
    """Render spans as one line of Markdown, escaping and writing links with `writer`.

    Code spans outside hyperlinks may become links of their own through `writer.code_link`.
    """
    parts: list[str] = []
    for group in _groups(_normalise(spans), join_plain=writer.merge_emphasis):
        href = group[0].href
        code_link = writer.code_link if href is None else None
        inner = (_render_merged(group, writer.escape, code_link) if writer.merge_emphasis
                 else "".join(_render_one(s, writer.escape, code_link) for s in group))
        parts.append(writer.link(href, inner, plain_text(group)) if href is not None else inner)
    result = "".join(parts)
    return _line_start(result) if at_line_start else result


def code_fence(lines: list[str]) -> list[str]:
    """Return a fenced code block (fence lines included), tagged json when it looks like JSON."""
    fence = "~~~~" if any("```" in line for line in lines) else "```"
    joined = "\n".join(lines).strip()
    lang = "json" if joined.startswith("{") or re.match(r"^\[\s*\{", joined) else ""
    return [fence + lang, *lines, fence]
