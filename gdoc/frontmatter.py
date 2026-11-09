"""Simple YAML frontmatter parser (no pyyaml dependency)."""

import re

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from content.

    Returns (metadata_dict, body_without_frontmatter).
    If no valid frontmatter, returns ({}, content).
    Only supports flat key: value pairs.

    A leading `---\\n...\\n---\\n` block is only treated as frontmatter
    when at least one `key: value` line parses out of it. Empty blocks
    or blocks containing only prose (for example, a thematic break
    followed by another `---`) are left in place to avoid silently
    eating content.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    raw = match.group(1)
    metadata: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        colon = line.find(":")
        if colon == -1:
            continue
        key = line[:colon].strip()
        value = line[colon + 1 :].strip()
        if key:
            metadata[key] = value

    if not metadata:
        return {}, content

    body = content[match.end() :]
    return metadata, body


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


def set_frontmatter_value(content: str, key: str, value: str) -> str:
    """Set one key in content's frontmatter, leaving everything else as is.

    Replaces every `key:` line in the leading frontmatter block, or
    appends one when the key is absent. The body and the other keys are
    kept byte-for-byte. Raises ValueError when content has no
    frontmatter block.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise ValueError("no frontmatter block")
    value = " ".join(str(value).splitlines())
    lines = match.group(1).split("\n")
    found = False
    for i, line in enumerate(lines):
        name, sep, _ = line.partition(":")
        if sep and name.strip() == key:
            lines[i] = f"{key}: {value}"
            found = True
    if not found:
        lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n" + content[match.end():]
