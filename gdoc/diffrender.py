"""Terminal (color/plain) and HTML renderers for the revision-diff model.

Richer artifacts (docx, PDF, ...) are deliberately out of scope:
external scripts build them from the `gdoc diff --json` model.
"""

import html as html_mod
from collections.abc import Iterator

from gdoc.revdiff import DEFAULT_CONTEXT, hunk_changed

_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_GREEN = "\x1b[32m"
_RED_STRIKE = "\x1b[31;9m"


def select_visible(
    hunks: list[dict],
    context: int = DEFAULT_CONTEXT,
    comment_hunks: frozenset | set = frozenset(),
) -> list[bool]:
    """Which hunks to render: changed, commented, headings, ±context."""
    keep = [
        hunk_changed(h)
        or h["block_type"] == "heading"
        or i in comment_hunks
        for i, h in enumerate(hunks)
    ]
    for i, h in enumerate(hunks):
        if hunk_changed(h):
            for d in range(-context, context + 1):
                if 0 <= i + d < len(hunks):
                    keep[i + d] = True
    return keep


def iter_visible(
    hunks: list[dict],
    context: int = DEFAULT_CONTEXT,
    comment_hunks: frozenset | set = frozenset(),
) -> Iterator[tuple[str, int]]:
    """Yield ("gap", count) / ("hunk", index) events, collapsing
    consecutive hidden hunks into one gap."""
    keep = select_visible(hunks, context, comment_hunks=comment_hunks)
    gap = 0
    for i in range(len(hunks)):
        if not keep[i]:
            gap += 1
            continue
        if gap:
            yield "gap", gap
            gap = 0
        yield "hunk", i
    if gap:
        yield "gap", gap


def split_comments(comments: list[dict]) -> tuple[dict, list[dict]]:
    """Group model comments by anchored hunk index; rest go to appendix."""
    by_hunk: dict[int, list[dict]] = {}
    appendix: list[dict] = []
    for c in comments:
        if c.get("hunk") is None:
            appendix.append(c)
        else:
            by_hunk.setdefault(c["hunk"], []).append(c)
    return by_hunk, appendix


def short_time(iso: str) -> str:
    """Trim an RFC3339 UTC timestamp to 'YYYY-MM-DD HH:MMZ'."""
    return iso[:16].replace("T", " ") + "Z" if iso else "?"


_QUOTED_MAX = 90


def clip_quoted(text: str) -> str:
    """Truncate a comment's quoted snippet for display."""
    return text[:_QUOTED_MAX] + "…" if len(text) > _QUOTED_MAX else text


# ---------------------------------------------------------------- terminal

def _block_prefix(hunk: dict) -> str:
    if hunk["block_type"] == "heading":
        return "#" * hunk.get("level", 2) + " "
    if hunk["block_type"] == "listitem":
        return hunk.get("marker", "•") + " "
    return ""


def _term_run(run: dict, color: bool) -> str:
    if run["op"] == "ins":
        if color:
            return f"{_GREEN}{run['text']}{_RESET}"
        return "{+" + run["text"] + "+}"
    if run["op"] == "del":
        if color:
            return f"{_RED_STRIKE}{run['text']}{_RESET}"
        return "[-" + run["text"] + "-]"
    return run["text"]


def render_terminal(
    model: dict, color: bool, context: int = DEFAULT_CONTEXT,
) -> str:
    """Render the diff model as terminal text (ANSI word-diff or plain)."""
    old, new = model["old"], model["new"]
    header = (
        f"{model['doc']['name']}: "
        f"rev {old['id']} ({short_time(old['modifiedTime'])}) -> "
        f"rev {new['id']} ({short_time(new['modifiedTime'])})"
    )
    lines = [f"{_DIM}{header}{_RESET}" if color else header, ""]

    hunks = model["hunks"]
    for event, value in iter_visible(hunks, context):
        if event == "gap":
            label = f"⋯ {value} unchanged ⋯"
            lines.append(f"{_DIM}{label}{_RESET}" if color else label)
            continue
        hunk = hunks[value]
        body = "".join(_term_run(r, color) for r in hunk["runs"])
        line = _block_prefix(hunk) + body
        if color and hunk["kind"] == "equal":
            line = f"{_DIM}{line}{_RESET}"
        lines.append(line)

    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------- html

_AUTHOR_BARS = [
    "#d4a72c", "#54aeff", "#8250df", "#fb8500", "#2da44e", "#cf222e",
]

_HTML_STYLE = """\
body { font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI",
       Helvetica, Arial, sans-serif; color: #24292f; max-width: 760px;
       margin: 2rem auto; padding: 0 1rem; }
h1.title { margin-bottom: 0.2rem; color: #0b2b52; }
.meta { color: #57606a; margin-bottom: 0.3rem; }
.meta .old { color: #8b141b; } .meta .new { color: #0a5a24; }
.legend { color: #57606a; font-size: 13px; border-bottom: 1px solid #d0d7de;
          padding-bottom: 0.8rem; margin-bottom: 1rem; }
ins { background: #ccffd8; color: #0a5a24; text-decoration: none; }
del { background: #ffd7d5; color: #8b141b; text-decoration: line-through; }
h2.h, h3.h, h4.h { color: #0b2b52; margin: 1.1em 0 0.3em; }
.h.ins-blk { color: #0a5a24; } .h.del-blk { color: #8b141b; }
.blk { margin: 6px 0; }
.blk.ctx { color: #57606a; }
.blk.ins-blk { border-left: 3px solid #2da44e; padding-left: 9px; }
.blk.del-blk { border-left: 3px solid #cf222e; padding-left: 9px; }
.blk.rep { border-left: 3px solid #0969da; padding-left: 9px; }
.collapse { text-align: center; color: #8c959d; font-style: italic;
            font-size: 13px; margin: 10px 0; }
.cmt { background: #fff8c5; border-left: 4px solid #d4a72c; border-radius: 3px;
       padding: 8px 12px; margin: 8px 0 8px 16px; font-size: 13.5px; }
.cmt.resolved { opacity: 0.65; }
.cmt .head { font-weight: 600; }
.cmt .head .when, .cmt .head .tag { color: #57606a; font-weight: 400;
                                    font-size: 12.5px; }
.cmt .on { color: #57606a; font-style: italic; font-size: 12.5px; }
.cmt .reply { margin-left: 1.2em; margin-top: 2px; }
.appendix { border-top: 1px solid #d0d7de; margin-top: 1.5rem;
            padding-top: 0.5rem; }
"""


def _html_runs(runs: list[dict]) -> str:
    parts = []
    for r in runs:
        text = html_mod.escape(r["text"])
        if r["op"] == "ins":
            parts.append(f"<ins>{text}</ins>")
        elif r["op"] == "del":
            parts.append(f"<del>{text}</del>")
        else:
            parts.append(text)
    return "".join(parts)


_KIND_CLASS = {
    "equal": "ctx", "insert": "ins-blk", "delete": "del-blk",
    "replace": "rep",
}


def _html_hunk(hunk: dict) -> str:
    body = _html_runs(hunk["runs"])
    kind_class = _KIND_CLASS[hunk["kind"]]
    if hunk["block_type"] == "heading":
        tag = f"h{min(hunk.get('level', 2) + 1, 4)}"
        return f'<{tag} class="h {kind_class}">{body}</{tag}>'
    if hunk["block_type"] == "listitem":
        marker = html_mod.escape(hunk.get("marker", "•"))
        body = f"{marker}&nbsp;&nbsp;" + body
    return f'<div class="blk {kind_class}">{body}</div>'


def _html_comment(c: dict, bar: str) -> str:
    classes = "cmt resolved" if c.get("resolved") else "cmt"
    head = (
        f'💬 {html_mod.escape(c["author"])} '
        f'<span class="when">· {html_mod.escape(short_time(c["createdTime"]))}'
        "</span>"
    )
    if c.get("resolved"):
        head += ' <span class="tag">(resolved)</span>'
    parts = [
        f'<div class="{classes}" style="border-left-color:{bar}">',
        f'<div class="head">{head}</div>',
    ]
    if c.get("quoted"):
        parts.append(
            '<div class="on">on: '
            f"“{html_mod.escape(clip_quoted(c['quoted']))}”</div>"
        )
    parts.append(f"<div>{html_mod.escape(c['content'])}</div>")
    for r in c.get("replies", []):
        parts.append(
            f'<div class="reply">↳ <b>{html_mod.escape(r["author"])}</b>: '
            f'{html_mod.escape(r["content"])}</div>'
        )
    parts.append("</div>")
    return "".join(parts)


def render_html(model: dict, context: int = DEFAULT_CONTEXT) -> str:
    """Render the diff model as a self-contained styled HTML document."""
    hunks = model["hunks"]
    comments = model.get("comments", [])
    by_hunk, appendix = split_comments(comments)

    author_bars: dict[str, str] = {}

    def bar(author: str) -> str:
        if author not in author_bars:
            author_bars[author] = _AUTHOR_BARS[
                len(author_bars) % len(_AUTHOR_BARS)
            ]
        return author_bars[author]

    name = html_mod.escape(model["doc"]["name"])
    old, new = model["old"], model["new"]
    legend = (
        "<ins>added</ins> &nbsp; <del>removed</del> &nbsp;·&nbsp; "
        "blue bar = reworded"
    )
    if comments:
        legend += " &nbsp;·&nbsp; 💬 comment threads (color-coded by author)"

    body: list[str] = [
        f'<h1 class="title">{name} — revision diff</h1>',
        '<div class="meta">'
        f'<span class="old">rev {html_mod.escape(str(old["id"]))} '
        f"({html_mod.escape(short_time(old['modifiedTime']))})</span>"
        " → "
        f'<span class="new">rev {html_mod.escape(str(new["id"]))} '
        f"({html_mod.escape(short_time(new['modifiedTime']))})</span>"
        "</div>",
        f'<div class="legend">{legend}</div>',
    ]

    for event, value in iter_visible(
        hunks, context, comment_hunks=set(by_hunk),
    ):
        if event == "gap":
            body.append(
                f'<div class="collapse">⋯ {value} unchanged ⋯</div>'
            )
            continue
        body.append(_html_hunk(hunks[value]))
        for c in by_hunk.get(value, []):
            body.append(_html_comment(c, bar(c["author"])))

    if appendix:
        body.append('<div class="appendix">')
        body.append("<h2>Other comment threads</h2>")
        for c in appendix:
            body.append(_html_comment(c, bar(c["author"])))
        body.append("</div>")

    return (
        "<!doctype html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{name} — revision diff</title>\n"
        f"<style>\n{_HTML_STYLE}</style>\n</head>\n<body>\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )
