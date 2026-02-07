"""Line-numbered comment annotation engine for cat --comments."""


def _format_author(author_dict: dict) -> str:
    """Format author for display: prefer email, fallback to name."""
    if not author_dict:
        return "unknown"
    return (author_dict.get("emailAddress") or
            author_dict.get("displayName") or "unknown")


def _format_annotation_block(
    comment: dict,
    anchor_text: str | None = None,
    fallback_note: str = "",
) -> list[str]:
    """Build annotation lines for a single comment.

    Returns list of un-numbered annotation lines (no line-number prefix).
    """
    prefix = "      \t"
    lines = []

    cid = comment.get("id", "")
    resolved = comment.get("resolved", False)
    status = "resolved" if resolved else "open"
    author = _format_author(comment.get("author", {}))
    content = comment.get("content", "")

    # Header line
    status_part = f"[#{cid} {status}]"
    if fallback_note:
        status_part += f" [{fallback_note}]"

    if anchor_text is not None:
        # Anchored: show truncated anchor text
        display_anchor = anchor_text
        if len(display_anchor) > 40:
            display_anchor = display_anchor[:37] + "..."
        lines.append(f'{prefix}  {status_part} {author} on "{display_anchor}":')
    else:
        lines.append(f'{prefix}  {status_part} {author}: "{content}"')
        # For unanchored, content is on the header line, no separate content line
        # Add replies
        for r in comment.get("replies", []):
            reply_content = r.get("content", "")
            if not reply_content:
                continue
            r_author = _format_author(r.get("author", {}))
            lines.append(f'{prefix}    > {r_author}: "{reply_content}"')
        return lines

    # Content line (for anchored comments)
    lines.append(f'{prefix}    "{content}"')

    # Reply lines
    for r in comment.get("replies", []):
        reply_content = r.get("content", "")
        if not reply_content:
            continue  # Skip action-only replies
        r_author = _format_author(r.get("author", {}))
        lines.append(f'{prefix}    > {r_author}: "{reply_content}"')

    return lines


def annotate_markdown(
    markdown: str,
    comments: list[dict],
    show_resolved: bool = False,
) -> str:
    """Produce line-numbered annotated output with inline comment annotations.

    Args:
        markdown: Raw markdown content from export_doc.
        comments: Comment dicts from list_comments(include_anchor=True).
        show_resolved: If True, include resolved comments. If False,
            filter them out (defensive — caller should pre-filter).

    Returns:
        Annotated string with numbered content lines and un-numbered
        annotation lines.
    """
    # Defensive resolved filtering
    if not show_resolved:
        comments = [c for c in comments if not c.get("resolved", False)]

    lines = markdown.split("\n")
    # Remove trailing empty line from split if markdown ends with \n
    if lines and lines[-1] == "" and markdown.endswith("\n"):
        lines = lines[:-1]

    # Classify comments: anchored vs unanchored
    # line_annotations: line_index (0-based) -> list of (comment, anchor_text, fallback_note)
    line_annotations: dict[int, list[tuple[dict, str, str]]] = {}
    unanchored: list[tuple[dict, str]] = []  # (comment, fallback_note)

    for c in comments:
        qfc = c.get("quotedFileContent")
        if not qfc or not qfc.get("value"):
            # Unanchored comment
            unanchored.append((c, ""))
            continue

        anchor_text = qfc["value"]

        # Short anchor check
        if len(anchor_text.strip()) < 4:
            unanchored.append((c, "anchor too short"))
            continue

        # Find anchor in full markdown string
        pos = markdown.find(anchor_text)
        if pos == -1:
            # Anchor text deleted
            unanchored.append((c, "anchor deleted"))
            continue

        # Check for multiple matches
        second_pos = markdown.find(anchor_text, pos + 1)
        if second_pos != -1:
            # Ambiguous
            unanchored.append((c, "anchor ambiguous"))
            continue

        # Single match — find line number
        # Count newlines up to end of match to find the last line of the span
        match_end = pos + len(anchor_text)
        line_idx = markdown[:match_end].count("\n")
        # Clamp to valid range
        if line_idx >= len(lines):
            line_idx = len(lines) - 1 if lines else 0

        if line_idx not in line_annotations:
            line_annotations[line_idx] = []
        line_annotations[line_idx].append((c, anchor_text, ""))

    # Build output
    output_lines: list[str] = []

    for i, line in enumerate(lines):
        line_num = i + 1
        output_lines.append(f"{line_num:>6}\t{line}")

        if i in line_annotations:
            for c, anchor_text, fallback_note in line_annotations[i]:
                annotation_lines = _format_annotation_block(
                    c, anchor_text=anchor_text, fallback_note=fallback_note,
                )
                output_lines.extend(annotation_lines)

    # Unanchored section
    if unanchored:
        output_lines.append("      \t[UNANCHORED]")
        for c, fallback_note in unanchored:
            annotation_lines = _format_annotation_block(
                c, anchor_text=None, fallback_note=fallback_note,
            )
            output_lines.extend(annotation_lines)

    # Final newline
    output_lines.append("")
    return "\n".join(output_lines)
