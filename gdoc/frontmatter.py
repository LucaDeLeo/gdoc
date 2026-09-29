"""Simple YAML frontmatter parser (no pyyaml dependency)."""

import re

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
# An empty metadata block. Markdown whose body starts with a `---` line is
# written after one, so the body's leading rule is never read as metadata.
_EMPTY_FRONTMATTER_RE = re.compile(r"^---\r?\n---\r?\n")
_LEADING_RULE_RE = re.compile(r"^---\r?\n")
# A metadata key line: a key, then a colon followed by a space or the line
# end. A plain identifier key may also be followed directly by a value
# (`key:value`) unless that value starts a path or URL (`/`, `\\`). Keys
# hold no Markdown link, code, table or escape characters (`[ ] ( ) < > ` |
# \\`), no strikethrough (`~`), do not start or end with emphasis marks
# (`*`, `_`), and are not list items or quotes.
_PLAIN_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_KEY_LINE_RE = re.compile(
    r"(?![-+*>]\s|\d+[.)]\s)"
    r"([^\s:#\[\]()<>`|\\~*_][^:\[\]()<>`|\\~]*?(?<![*_\s]))[ \t]*"
    r"(?::(?=\s|$)|(?<=[A-Za-z0-9_.-]):(?![/\\])(?=\S))"
)


def protect_body(body: str) -> str:
    """Return *body* as Markdown input whose metadata block cannot absorb it."""
    return "---\n---\n" + body if _LEADING_RULE_RE.match(body) else body


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from content.

    Returns (metadata_dict, body_without_frontmatter).
    If no valid frontmatter, returns ({}, content).
    Only supports flat key: value pairs. A leading UTF-8 byte-order mark,
    which some editors add on save, is ignored.

    Markdown input carries at most one leading metadata block: either an
    empty block (`---` on two consecutive lines, which `protect_body` adds
    before a body that starts with a rule) or a YAML-style block. A block
    is metadata when its first line after any `#` comments is a key line
    and so is every unindented line with a colon (see `_KEY_LINE_RE`):
    Markdown-shaped lines (links, code, tables, list items, quotes,
    emphasis-wrapped keys, paths and URLs such as `https://…`) are not key
    lines. Comments, nested YAML and colon-free lines are skipped. Any other
    block, including one opening with a blank line, stays in the body as
    rules and paragraphs. A prose line shaped like `Note: keep me` is a key
    line; a body meant to start `---`, `Note: keep me`, `---` is written
    after the empty block, as `cat` prints it, or escapes the colon
    (`Note\\:`).
    """
    content = content.removeprefix("\ufeff")
    empty = _EMPTY_FRONTMATTER_RE.match(content)
    if empty:
        return {}, content[empty.end():]
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    raw = match.group(1)
    lines = raw.splitlines()
    first = next((line for line in lines if not line.startswith("#")), "")
    if not _KEY_LINE_RE.match(first):
        return {}, content
    metadata: dict[str, str] = {}
    for line in lines:
        # Nested YAML (indented lines, list items) and comments are skipped.
        if line[:1] in (" ", "\t") or line.startswith(("#", "-")):
            continue
        if ":" not in line:
            continue
        key_line = _KEY_LINE_RE.match(line)
        if not key_line or (
            not line[key_line.end():][:1].isspace() and line[key_line.end():]
            and not _PLAIN_KEY_RE.fullmatch(key_line[1])
        ):
            return {}, content
        key = key_line[1].strip()
        if len(key) > 1 and key[0] == key[-1] and key[0] in "\"'":
            key = key[1:-1]
        metadata[key] = line[key_line.end():].strip()

    if not metadata:
        return {}, content

    body = content[match.end() :]
    return metadata, body


# Letters that render as blank space.
_BLANK_LETTERS = "\u115f\u1160\u3164\uffa0"
# A dash run, counting Unicode dashes, or a `...` closer.
_BLOCK_CLOSE_RE = re.compile(
    r"(?:[-\u2010-\u2015\u2212\ufe58\ufe63\uff0d]{3,}|\.\.\.)[ \t]*")
# A gdoc key (`gdoc`, `gdoc-*`) in any case: starting a line, indented,
# quoted, commented out or as a list item, or inside a flow mapping.
_GDOC_KEY_RE = re.compile(
    r"(?:^[\s#-]*|[{,]\s*)[\"']?gdoc(?:-[^\s:\uff1a\"',}]*)?[\"']?\s*[:\uff1a]",
    re.IGNORECASE)


def _shown(line: str) -> str:
    """*line* as it reads: control, format and combining characters and
    blank letters removed, and surrounding whitespace stripped."""
    import unicodedata

    return "".join(
        char for char in line
        if char.isspace() or (unicodedata.category(char)[0] not in "CM"
                              and char not in _BLANK_LETTERS)
    ).strip()


def _header_shaped(line: str) -> bool:
    """Whether *line* could belong to a metadata block: it has no letters or
    digits, or it is a `key: value` line, a comment or an indented line."""
    import unicodedata

    shown = _shown(line)
    return (not any(unicodedata.category(char)[0] in "LN" for char in shown)
            or shown.startswith("#") or line[:1] in (" ", "\t")
            or bool(_KEY_LINE_RE.match(shown) or _GDOC_KEY_RE.search(shown)))


def provenance_header_problem(content: str) -> str | None:
    """Why *content*'s pulled-file header can't be trusted, or None.

    A pulled-file header is the file's leading run of header-shaped lines
    (see `_header_shaped`) and `---` blocks, ignoring invisible characters
    and HTML comments, when it holds a `gdoc` or `gdoc-*` key. It must be read
    exactly: it starts the file (after at most one byte-order mark), opens
    and closes with `---` lines, holds only `key: value` lines, and names
    the document (`gdoc: ID`). Otherwise its stale-file checks could not run
    and the header would be written as text. Other front matter, and a body
    that opens with a rule or after an empty `---`/`---` block, are
    unaffected.
    """
    content = content.removeprefix("\ufeff")
    if _EMPTY_FRONTMATTER_RE.match(content):
        return None  # `cat`'s spelling of a body that opens with a rule
    visible = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    inside = False  # between a dash line and its closer, any line counts
    for line in visible.splitlines():
        if _GDOC_KEY_RE.search(_shown(line)):
            break
        if _BLOCK_CLOSE_RE.fullmatch(_shown(line)):
            inside = not inside
        elif not (inside or _header_shaped(line)):
            return None
    else:
        return None
    lines = re.split(r"\r\n|\r|\n", content)
    if lines[0] != "---":
        return ("its first line must be exactly `---`, with nothing before or "
                "after the dashes")
    close = next((k for k in range(1, len(lines))
                  if _BLOCK_CLOSE_RE.fullmatch(_shown(lines[k]))), None)
    if close is None:
        return "its opening `---` block never closes"
    if lines[close] != "---" or "\n" not in content:
        return "its opening block must open and close with `---` lines"
    block = lines[1:close]
    first = next((line for line in block if not line.startswith("#")), "")
    if not first.strip():
        return "its opening block starts with a blank line, so it is read as text"
    for line in block:
        if line.strip() and not _KEY_LINE_RE.match(line):
            return ("its opening block has a line that isn't `key: value`: "
                    f"{line.rstrip()!r}")
    if not parse_frontmatter(content)[0].get("gdoc"):
        return "its opening block doesn't name the document with a `gdoc: ID` line"
    return None


def add_frontmatter(body: str, metadata: dict) -> str:
    """Prepend YAML frontmatter to body.

    Args:
        body: The document body.
        metadata: Flat dict of key-value pairs.

    Returns:
        Content with frontmatter prepended.

    Values are flattened to a single line: a line break in a value
    (e.g. a doc title) would otherwise inject arbitrary frontmatter
    keys. splitlines() covers the same separator set parse_frontmatter
    splits on (\\n, \\r, \\x0b, \\u2028, ...), unlike a [\\r\\n] regex.
    """
    lines = ["---"]
    for key, value in metadata.items():
        value = " ".join(str(value).splitlines())
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body


def update_frontmatter_value(content: str, key: str, value: str) -> str:
    """Update one flat field without reserializing unrelated frontmatter.

    A leading UTF-8 byte-order mark is kept, as parse_frontmatter ignores it.
    """
    if content.startswith("\ufeff"):
        return "\ufeff" + update_frontmatter_value(content[1:], key, value)
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return content
    raw = match[1]
    replacement = f"{key}: {' '.join(str(value).splitlines())}"
    pattern = re.compile(r"^" + re.escape(key) + r":[^\r\n]*(?=\r?$)", re.MULTILINE)
    raw = (
        pattern.sub(lambda _: replacement, raw)
        if pattern.search(raw)
        else raw + ("\r\n" if "\r\n" in content else "\n") + replacement
    )
    return content[: match.start(1)] + raw + content[match.end(1) :]


def preserve_and_replace(
    path: str, content: str, *, expected: str | None = None
) -> bool:
    """Publish a file without discarding any concurrently edited inode.

    Move the previous inode to a retained recovery file before publishing with
    exclusive hard-link creation. Editors with an already-open descriptor keep
    writing to that recovery file; editors saving a new inode at the original
    path win the race. This is recovery-backed publication, not filesystem CAS.
    """
    import os
    import sys
    import tempfile
    from pathlib import Path
    from uuid import uuid4

    target = Path(path)
    if target.is_symlink():
        # Replace the linked file and keep the link itself.
        target = Path(os.path.realpath(target))
    if expected is not None and read_local_text(target) != expected:
        print(f"WARN: local file changed; left {path} untouched", file=sys.stderr)
        return False
    try:
        if target.read_bytes() == content.encode("utf-8"):
            return True  # Already published: no move, no recovery copy.
    except FileNotFoundError:
        pass
    fd, staging = tempfile.mkstemp(prefix=".gdoc-publish-", dir=target.parent)
    backup = target.with_name(target.name + ".gdoc-backup-" + uuid4().hex)
    moved = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Publication needs hard links; check before the original moves.
        probe = staging + ".link"
        try:
            os.link(staging, probe)
        except OSError as error:
            raise OSError(
                f"{target.parent} does not support hard links; {path} left "
                f"unchanged ({error})"
            ) from error
        os.unlink(probe)
        if target.exists():
            os.chmod(staging, target.stat().st_mode & 0o777)
            os.rename(target, backup)
            moved = True
            print(f"LOCAL: previous file retained at {backup}", file=sys.stderr)
            if expected is not None and read_local_text(backup) != expected:
                try:
                    os.link(backup, target)
                except FileExistsError:
                    pass
                print("WARN: local file changed; replacement skipped", file=sys.stderr)
                return False
        if not moved:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(staging, 0o666 & ~umask)
        try:
            os.link(staging, target)
        except FileExistsError:
            print(
                f"WARN: concurrent local save at {path}; replacement skipped",
                file=sys.stderr,
            )
            return False
        return True
    except Exception:
        if moved and not target.exists():
            try:
                os.link(backup, target)
            except FileExistsError:
                pass
        raise
    finally:
        os.unlink(staging)


def body_fingerprint(body: str) -> str:
    """Identify exactly the local body last pulled or acknowledged."""
    import hashlib

    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def read_local_text(path) -> str:
    """Read source bytes as UTF-8 without normalizing frontmatter newlines."""
    with open(path, encoding="utf-8", newline="") as stream:
        return stream.read()
