"""CLI parser, subcommand dispatch, and exception handler."""

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Literal

from gdoc import __version__
from gdoc.revdiff import DEFAULT_CONTEXT, DEFAULT_MIN_COMMON
from gdoc.util import SPREADSHEET_MIME, AuthError, GdocError


class GdocArgumentParser(argparse.ArgumentParser):
    """Custom parser that exits with code 3 on usage errors (not 2)."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"ERR: {message}", file=sys.stderr)
        sys.exit(3)


def _truncate_bytes(text: str, max_bytes: int) -> str:
    """Truncate text to at most max_bytes UTF-8 bytes.

    Handles multi-byte characters safely by decoding with errors='ignore'.
    Returns the original text if max_bytes is 0 (unlimited).
    """
    if max_bytes <= 0:
        return text
    encoded = text.encode("utf-8")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore")


def _resolve_doc_id(raw: str) -> str:
    """Extract doc ID, wrapping ValueError as GdocError(exit_code=3)."""
    from gdoc.util import extract_doc_id

    try:
        return extract_doc_id(raw)
    except ValueError as e:
        raise GdocError(str(e), exit_code=3)



def _file_mime(doc_id: str, change_info) -> str:
    """Get the file's mimeType, reusing the pre-flight metadata when available."""
    if change_info is not None and change_info.mime_type:
        return change_info.mime_type
    from gdoc.api.drive import get_file_version

    return get_file_version(doc_id).get("mimeType", "")


def _require_doc(doc_id: str, change_info) -> None:
    """Reject spreadsheets early on doc-only commands.

    Only fires when pre-flight already fetched the mime — quiet mode falls
    through to the API's own error rather than paying an extra lookup.
    """
    if change_info is not None and change_info.mime_type == SPREADSHEET_MIME:
        raise GdocError(
            f"not a Google Doc: {doc_id} "
            "(spreadsheets support cat/tabs/info/cells only)",
            exit_code=3,
        )


def _quote_sheet_title(title: str) -> str:
    """Quote a sheet title for use in an A1 range reference."""
    return "'" + title.replace("'", "''") + "'"


def _pad_rows(values: list[list]) -> list[list[str]]:
    """Pad rows to equal width (the API omits trailing empty cells)."""
    width = max((len(r) for r in values), default=0)
    return [[str(c) for c in r] + [""] * (width - len(r)) for r in values]


def _format_sheet_tsv(values: list[list]) -> str:
    """Format cell values as TSV. Tabs/newlines inside cells become spaces."""
    rows = _pad_rows(values)
    clean = [
        "\t".join(c.replace("\t", " ").replace("\n", " ") for c in r)
        for r in rows
    ]
    return "\n".join(clean) + ("\n" if clean else "")


def _format_sheet_table(values: list[list]) -> str:
    """Format cell values as a markdown table (first row as header)."""
    rows = _pad_rows(values)
    if not rows:
        return "(no values)\n"
    rows = [[c.replace("|", "\\|").replace("\n", " ") for c in r] for r in rows]
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    widths = [max(w, 3) for w in widths]

    def fmt(row):
        return "| " + " | ".join(c.ljust(w) for c, w in zip(row, widths)) + " |"

    lines = [fmt(rows[0]), "| " + " | ".join("-" * w for w in widths) + " |"]
    lines.extend(fmt(r) for r in rows[1:])
    return "\n".join(lines) + "\n"


def _cat_sheet(args, doc_id: str, change_info) -> int:
    """Spreadsheet branch of `gdoc cat`: print cell values."""
    if getattr(args, "comments", False):
        raise GdocError("--comments is not supported for spreadsheets", exit_code=3)
    if getattr(args, "revision", None):
        raise GdocError(
            "--revision is not supported for spreadsheets", exit_code=3,
        )

    quiet = getattr(args, "quiet", False)
    tab = getattr(args, "tab", None)
    all_tabs = getattr(args, "all_tabs", False)
    range_ = getattr(args, "range", None)
    max_bytes = getattr(args, "max_bytes", 0)

    from gdoc.api.docs import resolve_tab
    from gdoc.api.sheets import batch_get_values, get_spreadsheet_meta, get_values
    from gdoc.format import format_json, get_output_mode

    meta = get_spreadsheet_meta(doc_id)
    sheets = sorted(meta["sheets"], key=lambda s: s["index"])
    if not sheets:
        raise GdocError("spreadsheet has no worksheets")

    mode = get_output_mode(args)
    formatter = _format_sheet_tsv if mode == "plain" else _format_sheet_table

    if all_tabs:
        if range_:
            raise GdocError(
                "--range and --all-tabs are mutually exclusive", exit_code=3
            )
        ranges = [_quote_sheet_title(s["title"]) for s in sheets]
        results = list(zip(sheets, batch_get_values(doc_id, ranges)))
        if mode == "json":
            print(
                format_json(
                    tabs=[
                        {
                            "title": s["title"],
                            "range": d["range"],
                            "values": d["values"],
                        }
                        for s, d in results
                    ]
                )
            )
        else:
            parts = []
            for s, d in results:
                parts.append(f"=== Tab: {s['title']} ===\n")
                parts.append(formatter(d["values"]))
            print(_truncate_bytes("".join(parts), max_bytes), end="")
    else:
        if tab:
            target = resolve_tab(sheets, tab)
        else:
            target = sheets[0]
            if len(sheets) > 1 and not quiet:
                print(
                    f"--- {len(sheets)} tabs; showing \"{target['title']}\" "
                    "(use --tab or --all-tabs) ---",
                    file=sys.stderr,
                )
        a1 = _quote_sheet_title(target["title"])
        if range_:
            a1 += f"!{range_}"
        data = get_values(doc_id, a1)
        if mode == "json":
            print(format_json(range=data["range"], values=data["values"]))
        else:
            print(_truncate_bytes(formatter(data["values"]), max_bytes), end="")

    from gdoc.state import update_state_after_command

    update_state_after_command(doc_id, change_info, command="cat", quiet=quiet)
    return 0


def _format_local_time(iso: str) -> str:
    """Format an RFC3339 UTC timestamp as local 'YYYY-MM-DD HH:MM'."""
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def _resolve_revision(doc_id: str, selector: str) -> dict:
    """Resolve a REV selector to a revision dict (one revisions.list call)."""
    from gdoc.api.revisions import list_revisions
    from gdoc.revdiff import resolve_selector

    return resolve_selector(list_revisions(doc_id), selector)


def cmd_revisions(args) -> int:
    """Handler for `gdoc revisions`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    limit = getattr(args, "limit", 0)

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.revisions import list_revisions
    revisions = list_revisions(doc_id)
    if limit and limit > 0:
        revisions = revisions[-limit:]

    from gdoc.format import format_json, get_output_mode
    mode = get_output_mode(args)
    if mode == "json":
        items = [
            {
                "id": r.get("id"),
                "modifiedTime": r.get("modifiedTime", ""),
                "lastModifyingUser": r.get("lastModifyingUser", {}),
                "keepForever": r.get("keepForever", False),
                "exportLinks": r.get("exportLinks", {}),
            }
            for r in revisions
        ]
        print(format_json(revisions=items))
    elif mode == "plain":
        for r in revisions:
            author = (r.get("lastModifyingUser") or {}).get("displayName", "")
            keep = "true" if r.get("keepForever") else "false"
            print(f"{r.get('id')}\t{r.get('modifiedTime', '')}\t{author}\t{keep}")
    elif not revisions:
        print("No revisions retained.")
    else:
        for r in revisions:
            author = (r.get("lastModifyingUser") or {}).get("displayName", "?")
            if mode == "verbose":
                when = r.get("modifiedTime", "")
            else:
                when = _format_local_time(r.get("modifiedTime", ""))
            keep = "  [keep]" if r.get("keepForever") else ""
            print(f"{r.get('id', '?'):>6}  {when}  {author}{keep}")
        if mode == "verbose":
            print(f"\n({len(revisions)} revisions, oldest first; "
                  "non-pinned revisions are pruned by Google over time)")

    from gdoc.state import update_state_after_command
    update_state_after_command(
        doc_id, change_info, command="revisions", quiet=quiet,
    )

    return 0


def cmd_cat(args) -> int:
    """Handler for `gdoc cat`."""
    doc_id = _resolve_doc_id(args.doc)

    quiet = getattr(args, "quiet", False)
    tab = getattr(args, "tab", None)
    all_tabs = getattr(args, "all_tabs", False)

    if getattr(args, "comments", False) and getattr(args, "plain", False):
        raise GdocError("--comments and --plain are mutually exclusive", exit_code=3)

    if all_tabs and getattr(args, "comments", False):
        raise GdocError(
            "--all-tabs and --comments are mutually exclusive; annotate one "
            "tab at a time with --tab",
            exit_code=3,
        )

    max_bytes = getattr(args, "max_bytes", 0)
    no_images = getattr(args, "no_images", False)

    revision = getattr(args, "revision", None)
    if revision and (tab or all_tabs or getattr(args, "comments", False)):
        raise GdocError(
            "--revision cannot be combined with "
            "--tab/--all-tabs/--comments",
            exit_code=3,
        )

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    if _file_mime(doc_id, change_info) == SPREADSHEET_MIME:
        return _cat_sheet(args, doc_id, change_info)

    if getattr(args, "range", None):
        raise GdocError("--range is only supported for spreadsheets", exit_code=3)

    if revision:
        from gdoc.api.revisions import export_revision

        rev = _resolve_revision(doc_id, revision)
        mime = (
            "text/plain" if getattr(args, "plain", False)
            else "text/markdown"
        )
        content = export_revision(
            doc_id, rev["id"], mime_type=mime,
            export_links=rev.get("exportLinks"),
        )
        if no_images:
            from gdoc.mdimport import strip_images
            content = strip_images(content)
        total_bytes = len(content.encode("utf-8"))
        truncated = max_bytes > 0 and total_bytes > max_bytes
        content = _truncate_bytes(content, max_bytes)
        if truncated:
            print(
                "NOTE: revision output is truncated; "
                "use --max-bytes 0 for complete content", file=sys.stderr,
            )

        from gdoc.format import format_json, get_output_mode
        if get_output_mode(args) == "json":
            print(format_json(
                revision=rev["id"], content=content,
                scope={"complete": not (truncated or no_images),
                       "truncated": truncated, "total_bytes": total_bytes},
            ))
        else:
            print(content, end="")

        # A past revision is not the current content: record the
        # interaction without advancing the read baseline that the
        # write-conflict check relies on.
        from gdoc.state import update_state_after_command
        update_state_after_command(
            doc_id, change_info, command="cat-revision", quiet=quiet,
        )
        return 0

    from gdoc.api.docs import (
        flatten_tabs,
        get_document_with_tabs,
        get_tab_text,
        resolve_tab,
    )
    from gdoc.format import format_json, get_output_mode
    from gdoc.state import update_state_after_command

    document = get_document_with_tabs(doc_id)
    tabs = flatten_tabs(document.get("tabs", []))
    if not tabs:
        raise GdocError("document has no readable tabs", exit_code=3)
    selected = tabs if all_tabs else [resolve_tab(tabs, tab) if tab else tabs[0]]
    want_markdown = not getattr(args, "plain", False)
    parts = []
    for selected_tab in selected:
        if all_tabs:
            parts.append(f"=== Tab: {selected_tab['title']} ===\n")
        if no_images:
            # Drop the native image elements, not text that looks like an
            # image reference: code keeps its characters and paragraphs stay.
            selected_tab = _without_inline_objects(selected_tab)
        parts.append(get_tab_text(selected_tab, markdown=want_markdown))
    content = "".join(parts)
    annotated = getattr(args, "comments", False)
    if want_markdown and not all_tabs and not annotated:
        from gdoc.frontmatter import protect_body
        content = protect_body(content)
    if annotated:
        from gdoc.annotate import annotate_markdown
        from gdoc.api.comments import list_comments
        include_resolved = getattr(args, "all", False)
        comments = list_comments(
            doc_id, include_resolved=include_resolved, include_anchor=True,
        )
        others = [get_tab_text(other, markdown=True) for other in tabs
                  if other["id"] != selected[0]["id"]]
        content = annotate_markdown(content, comments, show_resolved=include_resolved,
                                    other_tabs=others)

    total_bytes = len(content.encode("utf-8"))
    displayed = _truncate_bytes(content, max_bytes)
    truncated = displayed != content
    # Plain text omits supported formatting, and no-images omits objects.
    baseline = not (truncated or no_images or not want_markdown or annotated) \
        and _preview_complete(selected)
    omitted = _read_omissions(selected) if want_markdown else []
    scope = {
        "tab_ids": [selected_tab["id"] for selected_tab in selected],
        "complete": baseline and not omitted, "truncated": truncated,
        "revision_id": document.get("revisionId", ""),
        "total_bytes": total_bytes, "tab_count": len(tabs),
    }
    if omitted:
        scope["omitted"] = omitted
    if want_markdown:
        from gdoc.api.docs import pending_suggestion_ids
        pending = sum(len(pending_suggestion_ids(t.get("body", {})))
                      for t in selected)
        if pending:
            scope["pending_suggestions"] = pending
        from gdoc.api.docs import suggestion_preview_gaps
        gaps = [gap for t in selected for gap in suggestion_preview_gaps(t)]
        if gaps:
            scope["suggestion_preview_gaps"] = gaps
    if get_output_mode(args) == "json":
        extra = {"tab": selected[0]["title"]} if len(selected) == 1 else {}
        print(format_json(content=displayed, scope=scope, **extra))
    else:
        print(displayed, end="")
        if not all_tabs:
            _note_tab_scope(document, selected[0],
                            "--tab" if annotated else "--all-tabs or --tab")
        elif want_markdown:
            _note_pending_suggestions(selected)
        _note_omissions(omitted)
        if truncated:
            print(
                f"NOTE: partial output ({len(displayed.encode('utf-8'))} of "
                f"{total_bytes} bytes). Use --max-bytes 0 for complete content.",
                file=sys.stderr,
            )
    update_state_after_command(
        doc_id, change_info, command="cat-content", quiet=quiet,
    )
    # A read that reports its omissions still pins the revision it saw: a
    # rewrite then needs --allow-lossy to discard them (the loss guard), and
    # stays revision-protected.
    if baseline:
        _record_read(doc_id, selected, scope["revision_id"])
    return 0


def _known_image_aliases(known, tab_id: str, revision: str) -> dict:
    """References to images gdoc's own acknowledged writes re-created, usable
    while this tab's recorded coverage (complete or limited) is exactly the
    revision being written; the Markdown itself is sent as written."""
    if known is None or not revision or revision not in (
            known.read_revision_ids.get(tab_id),
            known.limited_read_revision_ids.get(tab_id)):
        return {}
    return known.image_reference_ids


def _record_read(doc_id: str, tabs: list[dict], revision: str) -> None:
    """Record a Markdown read; a tab whose read named omissions gets limited
    coverage, which a replacement accepts only with --allow-lossy."""
    from gdoc.api.docs import markdown_read_omissions
    from gdoc.state import record_content_read

    record_content_read(
        doc_id, [tab["id"] for tab in tabs], revision,
        limited_tab_ids=[tab["id"] for tab in tabs if markdown_read_omissions(tab)],
    )


def _read_omissions(tabs: list[dict]) -> list[str]:
    """Native content the Markdown reads of *tabs* leave out or flatten."""
    from gdoc.api.docs import markdown_read_omissions

    return sorted({item for tab in tabs for item in markdown_read_omissions(tab)})


def _note_omissions(omitted: list[str]) -> None:
    if omitted:
        print(
            "NOTE: this Markdown leaves out native content: "
            + ", ".join(omitted) + ". `gdoc structure` shows it; targeted "
            "edits keep it, and a rewrite of the tab needs --allow-lossy and "
            "discards it.", file=sys.stderr,
        )


def _tabs_sheet(args, doc_id: str, change_info) -> int:
    """Spreadsheet branch of `gdoc tabs`: list worksheets."""
    quiet = getattr(args, "quiet", False)

    from gdoc.api.sheets import get_spreadsheet_meta
    from gdoc.format import format_json, get_output_mode

    sheets = sorted(get_spreadsheet_meta(doc_id)["sheets"], key=lambda s: s["index"])

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(tabs=sheets))
    elif mode == "plain":
        for s in sheets:
            print(f"{s['id']}\t{s['title']}")
    elif not sheets:
        print("No tabs.")
    else:
        for s in sheets:
            print(f"{s['id']}\t{s['title']}\t{s['rows']}x{s['cols']}")

    from gdoc.state import update_state_after_command

    update_state_after_command(doc_id, change_info, command="tabs", quiet=quiet)
    return 0


def cmd_tabs(args) -> int:
    """Handler for `gdoc tabs`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    if _file_mime(doc_id, change_info) == SPREADSHEET_MIME:
        return _tabs_sheet(args, doc_id, change_info)

    from gdoc.api.docs import get_document_tabs

    tabs = get_document_tabs(doc_id)

    from gdoc.format import get_output_mode, format_json

    mode = get_output_mode(args)
    if mode == "json":
        json_tabs = [
            {"id": t["id"], "title": t["title"], "index": t["index"],
             "nesting_level": t["nesting_level"]}
            for t in tabs
        ]
        print(format_json(tabs=json_tabs))
    elif mode == "plain":
        for t in tabs:
            print(f"{t['id']}\t{t['title']}")
    elif mode == "verbose":
        for t in tabs:
            print(f"{t['id']}\t{t['title']}\tindex={t['index']}\tlevel={t['nesting_level']}")
    elif not tabs:
        print("No tabs.")
    else:
        for t in tabs:
            indent = "  " * t["nesting_level"]
            print(f"{indent}{t['id']}\t{t['title']}")

    from gdoc.state import update_state_after_command
    update_state_after_command(doc_id, change_info, command="tabs", quiet=quiet)

    return 0


def cmd_toc(args) -> int:
    """Handler for `gdoc toc`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    tab = getattr(args, "tab", None)
    max_depth = getattr(args, "max_depth", 0)
    no_links = getattr(args, "no_links", False)

    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)

    from gdoc.api.docs import get_document_headings

    body = None
    tab_id = None
    if tab:
        from gdoc.api.docs import get_document_tabs, resolve_tab

        tabs = get_document_tabs(doc_id)
        tab_match = resolve_tab(tabs, tab)
        body = tab_match["body"]
        tab_id = tab_match["id"]

    headings = get_document_headings(doc_id, body=body)

    if max_depth > 0:
        headings = [h for h in headings if h["level"] <= max_depth]

    from gdoc.util import build_doc_url

    # build_doc_url emits `?tab=<tab_id>` as a query parameter before the
    # fragment, and tab_id already carries Google's `t.` prefix. The heading
    # anchor is the URL fragment, so it must come last.
    base_url = build_doc_url(doc_id, tab_id=tab_id)

    def _link(heading_id: str) -> str:
        return f"{base_url}#heading={heading_id}"

    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    if mode == "json":
        items = []
        for h in headings:
            items.append({
                "level": h["level"],
                "heading_id": h["heading_id"],
                "text": h["text"],
                "link": _link(h["heading_id"]),
            })
        print(format_json(headings=items))
    elif mode == "plain":
        for h in headings:
            print(f"{h['level']}\t{h['heading_id']}\t{h['text']}\t{_link(h['heading_id'])}")
    else:
        for h in headings:
            indent = "  " * (h["level"] - 1)
            if no_links:
                print(f"{indent}- {h['text']}")
            else:
                print(f"{indent}- [{h['text']}]({_link(h['heading_id'])})")
        if mode == "verbose":
            print(f"\n({len(headings)} headings)")

    from gdoc.state import update_state_after_command

    update_state_after_command(doc_id, change_info, command="toc", quiet=quiet)

    return 0


def cmd_add_tab(args) -> int:
    """Handler for `gdoc add-tab`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    title = args.title

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)

    from gdoc.api.docs import add_tab
    result = add_tab(doc_id, title)
    tab_id = result["tabId"]

    from gdoc.api.drive import version_after_write
    command_version = version_after_write(doc_id)

    from gdoc.util import build_doc_url
    url = build_doc_url(doc_id, tab_id=tab_id)

    from gdoc.format import format_json, get_output_mode
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(
            id=tab_id, title=result["title"],
            index=result["index"], doc_id=doc_id, url=url,
        ))
    elif mode == "verbose":
        print(f"Added tab: {result['title']}")
        print(f"ID: {tab_id}")
        print(f"Index: {result['index']}")
        print(f"URL: {url}")
    elif mode == "plain":
        print(f"id\t{tab_id}")
        print(f"title\t{result['title']}")
        print(f"index\t{result['index']}")
        print(f"url\t{url}")
    else:
        print(f"{tab_id}\t{result['title']}\t{url}")

    from gdoc.state import update_state_after_command
    update_state_after_command(
        doc_id, change_info, command="add-tab", quiet=quiet,
        command_version=command_version,
    )

    return 0


def _print_tab_write_result(
    mode: str, doc_id: str, result: dict, version, verb: str,
) -> None:
    """Print the output for a successful per-tab write (insert or write --tab).

    `verb` is one of "inserted" / "wrote"; it controls the terse line, the
    verbose phrasing, the `status` column for plain mode, and the JSON
    boolean key (`inserted` vs `written`).
    """
    from gdoc.format import format_json

    json_key = "inserted" if verb == "inserted" else "written"
    status = "inserted" if verb == "inserted" else "updated"
    title = result["tab_title"]

    if mode == "json":
        print(format_json(**{
            json_key: True,
            "tab_id": result["tab_id"],
            "tab_title": title,
            "version": version,
            **({"rebased": True} if result.get("rebased") else {}),
        }))
    elif mode == "plain":
        print(f"id\t{doc_id}")
        print(f"tab_id\t{result['tab_id']}")
        print(f"status\t{status}")
    elif mode == "verbose":
        label = "Inserted into tab" if verb == "inserted" else "Wrote tab"
        print(f'{label}: "{title}"')
        print(f"Tab ID: {result['tab_id']}")
    else:
        terse = (
            f'OK inserted into "{title}"' if verb == "inserted"
            else f'OK wrote "{title}"'
        )
        print(terse)


def cmd_insert(args) -> int:
    """Handler for `gdoc insert`."""
    import os

    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    force = getattr(args, "force", False)
    tab_name = args.tab
    position = getattr(args, "position", "start")
    file_path = args.file

    if not os.path.isfile(file_path):
        raise GdocError(f"file not found: {file_path}", exit_code=3)
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise GdocError(f"cannot read file: {e}", exit_code=3) from e

    from gdoc.frontmatter import parse_frontmatter
    _, content = parse_frontmatter(content)

    if not content.strip():
        raise GdocError("input file has no content to insert", exit_code=3)

    from gdoc.api.docs import (
        flatten_tabs,
        get_document_with_tabs,
        insert_markdown_into_tab,
        resolve_tab,
    )
    from gdoc.notify import pre_flight
    from gdoc.state import require_content_baseline

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)
    document = get_document_with_tabs(doc_id)
    selected = resolve_tab(flatten_tabs(document.get("tabs", [])), tab_name)
    # An insertion keeps any content the last read had to leave out.
    require_content_baseline(
        doc_id, [selected["id"]], document.get("revisionId", ""), force=force,
        accept_limited=True,
    )

    from gdoc.state import load_state

    # Old image references resolve like a write's. An insertion only adds
    # copies (the originals stay), so its new image IDs are not recorded.
    result = insert_markdown_into_tab(
        doc_id, selected["id"], content, position=position, replace=False,
        document=document, image_aliases=_known_image_aliases(
            load_state(doc_id), selected["id"], document.get("revisionId", "")),
    )

    # Record the acknowledged write before any optional follow-up lookup.
    _record_acknowledged_write(
        doc_id,
        input_revision_id=result.get(
            "input_revision_id", document.get("revisionId", ""),
        ),
        acknowledged_revision_id=result.get("acknowledged_revision_id", ""),
        rebased=result.get("rebased", False),
    )
    from gdoc.api.drive import version_after_write
    command_version = version_after_write(doc_id)

    from gdoc.format import get_output_mode
    _print_tab_write_result(
        get_output_mode(args), doc_id, result, command_version,
        verb="inserted",
    )

    _update_state_after_write(
        doc_id, change_info, command="insert",
        quiet=quiet, command_version=command_version,
    )
    return 0


def _read_cell_rows(args) -> list[list[str]]:
    """Collect cell values for `gdoc cells` from -v/--file/--stdin."""
    values = getattr(args, "value", None)
    file_path = getattr(args, "file", None)
    use_stdin = getattr(args, "stdin", False)

    sources = sum(1 for s in (values, file_path, use_stdin) if s)
    if sources != 1:
        raise GdocError(
            "provide values via exactly one of -v/--value, --file, or --stdin",
            exit_code=3,
        )

    if values:
        return [list(values)]

    if file_path:
        try:
            with open(file_path, encoding="utf-8", newline="") as f:
                if file_path.lower().endswith(".csv"):
                    import csv

                    return list(csv.reader(f))
                return [line.rstrip("\n").split("\t") for line in f]
        except (OSError, UnicodeDecodeError) as e:
            raise GdocError(f"cannot read {file_path}: {e}", exit_code=3) from e

    rows = [line.rstrip("\n").split("\t") for line in sys.stdin]
    if not rows:
        raise GdocError("no values on stdin", exit_code=3)
    return rows


def cmd_cells(args) -> int:
    """Handler for `gdoc cells`: write values into a spreadsheet range."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)

    rows = _read_cell_rows(args)

    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)

    # Only checked when pre-flight already fetched the mime — the Sheets API
    # rejects non-spreadsheets anyway, so --quiet skips the extra lookup.
    if change_info is not None and change_info.mime_type not in (
        "",
        SPREADSHEET_MIME,
    ):
        raise GdocError(f"not a spreadsheet: {doc_id}", exit_code=3)

    # Conflict warning (warn but don't block, matching `edit` semantics for
    # surgical writes; only full-document overwrites hard-block).
    if change_info and change_info.has_conflict:
        print("WARN: doc changed since last read", file=sys.stderr)

    from gdoc.api.sheets import write_values

    append = getattr(args, "append", False)
    result = write_values(
        doc_id,
        args.range,
        rows,
        user_entered=getattr(args, "user_entered", False),
        append=append,
    )
    verb = "Appended" if append else "Updated"

    # Record the post-write version so the next pre-flight doesn't report
    # this command's own write as an external edit.
    from gdoc.api.drive import version_after_write

    command_version = version_after_write(doc_id)

    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(**result))
    elif mode == "plain":
        print(f"range\t{result['range']}")
        print(f"cells\t{result['cells']}")
    else:
        print(f"{verb} {result['range']} ({result['cells']} cells)")

    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info, command="cells",
        quiet=quiet, command_version=command_version,
    )
    return 0


def cmd_info(args) -> int:
    """Handler for `gdoc info`."""
    doc_id = _resolve_doc_id(args.doc)

    # Pre-flight awareness check
    quiet = getattr(args, "quiet", False)
    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.drive import get_file_info, export_doc
    from gdoc.format import get_output_mode, format_json

    metadata = get_file_info(doc_id)

    sheet_tabs = None
    if metadata.get("mimeType") == SPREADSHEET_MIME:
        from gdoc.api.sheets import get_spreadsheet_meta

        sheet_tabs = get_spreadsheet_meta(doc_id)["sheets"]
        word_count = None
    else:
        try:
            text = export_doc(doc_id, mime_type="text/plain")
            word_count = len(text.split())
        except GdocError as e:
            if "file is not a Google Docs editor document" in str(e):
                word_count = None
            else:
                raise

    title = metadata.get("name", "")
    owners = metadata.get("owners", [])
    owner_info = owners[0] if owners else {}
    owner = owner_info.get("displayName") or owner_info.get("emailAddress", "Unknown")
    modified = metadata.get("modifiedTime", "")
    created = metadata.get("createdTime", "")
    last_editor_info = metadata.get("lastModifyingUser", {})
    last_editor = last_editor_info.get("displayName") or last_editor_info.get(
        "emailAddress", ""
    )
    mime_type = metadata.get("mimeType", "")
    size = metadata.get("size")

    mode = get_output_mode(args)

    if sheet_tabs is not None:
        label, json_extra = "Tabs", {"tabs": sheet_tabs}
        value = ", ".join(
            f"{s['title']} ({s['rows']}x{s['cols']})" for s in sheet_tabs
        )
    else:
        label = "Words"
        value = word_count if word_count is not None else "N/A"
        json_extra = {"words": value}

    if mode == "json":
        print(
            format_json(
                id=doc_id,
                title=title,
                owner=owner,
                modified=modified,
                **json_extra,
            )
        )
    elif mode == "plain":
        print(f"title\t{title}")
        print(f"owner\t{owner}")
        print(f"modified\t{modified}")
        print(f"{label.lower()}\t{value}")
    elif mode == "verbose":
        print(f"Title: {title}")
        print(f"Owner: {owner}")
        print(f"Modified: {modified}")
        print(f"Created: {created}")
        print(f"Last editor: {last_editor}")
        print(f"Type: {mime_type}")
        print(f"Size: {size or 'N/A'}")
        print(f"{label}: {value}")
    else:
        print(f"Title: {title}")
        print(f"Owner: {owner}")
        print(f"Modified: {modified[:10]}")
        print(f"{label}: {value}")

    # Update state after success (version from get_file_info, Decision #14)
    command_version = metadata.get("version")
    if command_version is not None:
        command_version = int(command_version)
    from gdoc.state import update_state_after_command
    update_state_after_command(
        doc_id, change_info, command="info",
        quiet=quiet, command_version=command_version,
    )

    return 0


def _format_file_list(files: list[dict], mode: str) -> str:
    """Format a list of file dicts for output."""
    if mode == "json":
        from gdoc.format import format_json

        return format_json(files=files)

    if not files:
        return ""

    lines = []
    for f in files:
        fid = f.get("id", "")
        name = f.get("name", "")
        modified = f.get("modifiedTime", "")
        if mode == "verbose":
            mime = f.get("mimeType", "")
            lines.append(f"{fid}\t{name}\t{modified}\t{mime}")
        elif mode == "plain":
            mime = f.get("mimeType", "")
            lines.append(f"{fid}\t{name}\t{mime}")
        else:
            lines.append(f"{fid}\t{name}\t{modified[:10]}")
    return "\n".join(lines)


def cmd_ls(args) -> int:
    """Handler for `gdoc ls`."""
    from gdoc.api.drive import list_files
    from gdoc.format import get_output_mode

    query_parts = []

    if getattr(args, "folder_id", None):
        folder_id = _resolve_doc_id(args.folder_id)
        query_parts.append(f"'{folder_id}' in parents")
    else:
        query_parts.append("'root' in parents")

    query_parts.append("trashed=false")

    type_filter = getattr(args, "type", "all")
    if type_filter == "docs":
        query_parts.append("mimeType='application/vnd.google-apps.document'")
    elif type_filter == "sheets":
        query_parts.append("mimeType='application/vnd.google-apps.spreadsheet'")

    query = " and ".join(query_parts)
    files = list_files(query)

    mode = get_output_mode(args)
    output = _format_file_list(files, mode)
    if output:
        print(output)
    elif mode not in ("json", "plain"):
        print("No files.")

    return 0


def cmd_find(args) -> int:
    """Handler for `gdoc find`."""
    from gdoc.format import get_output_mode

    title_only = getattr(args, "title", False)
    if getattr(args, "raw", False):
        if title_only:
            raise GdocError(
                "--raw and --title are mutually exclusive "
                "(put name conditions in the raw query)",
                exit_code=3,
            )
        from gdoc.api.drive import list_files

        # Raw queries are the broad-search path: cover shared drives the
        # user belongs to, not just the default user corpus.
        files = list_files(args.query, all_drives=True)
    else:
        from gdoc.api.drive import search_files

        files = search_files(args.query, title_only=title_only)

    mode = get_output_mode(args)
    output = _format_file_list(files, mode)
    if output:
        print(output)
    elif mode not in ("json", "plain"):
        print("No files.")

    return 0


def _read_file(path: str) -> str:
    """Read replacement source; normalize its terminator after resolving inputs."""
    import os

    if not os.path.isfile(path):
        raise GdocError(f"file not found: {path}", exit_code=3)
    try:
        with open(path) as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise GdocError(f"cannot read file: {e}", exit_code=3)
    return content


@dataclass
class _ReplacementPlan:
    """Front half of a find/replace shared by `edit` and `suggest`."""

    quiet: bool
    change_info: object  # ChangeInfo | None
    matches: list
    revision_id: str
    tab_id: str | None
    search_body: dict
    search_scope: dict

    @property
    def replacement_source(self) -> dict:
        if any(m.get("tabId") or m.get("segmentId") for m in self.matches):
            return self.search_scope
        return self.search_body


def _resolve_replacement_text(args, cell) -> tuple[str | None, str | None]:
    """Resolve old/new text from positionals, `-` (stdin), or --old/--new-file.

    Fails fast (exit 3) before any API call when the combination is invalid.
    """
    old_text = args.old_text
    new_text = args.new_text
    old_file = getattr(args, "old_file", None)
    new_file = getattr(args, "new_file", None)

    # Read stdin lazily, once, and only for an argument that is actually used
    # (`-` on a positional that cell-mode ignores must not block on stdin).
    _stdin_data = None

    def read_stdin() -> str:
        nonlocal _stdin_data
        if _stdin_data is None:
            _stdin_data = sys.stdin.read()
        return _stdin_data

    if cell is not None:
        # Cell mode: the cell address is the anchor, so the single positional
        # (or --new-file) carries the replacement — no separate old_text.
        if new_file:
            new_text = _read_file(new_file)
        else:
            replacement = new_text if new_text is not None else old_text
            if replacement == "-":
                replacement = read_stdin()
            new_text = replacement
        if new_text is None:
            raise GdocError(
                "cell edit needs replacement text "
                "(NEW_TEXT positional or --new-file)",
                exit_code=3,
            )
    elif old_file or new_file:
        if new_file and not old_file:
            raise GdocError(
                "--new-file requires --old-file (needs an anchor). "
                "To add content without an anchor, use `gdoc insert`.",
                exit_code=3,
            )
        old_text = _read_file(old_file)
        if new_file:
            new_text = _read_file(new_file)
        else:
            # --old-file alone → delete the matched range.
            new_text = ""
    else:
        # `-` reads that positional from stdin (one stream → at most one `-`).
        if old_text == "-" and new_text == "-":
            raise GdocError(
                "only one argument can read from stdin ('-')", exit_code=3,
            )
        if old_text == "-" or new_text == "-":
            stdin_data = read_stdin()
            if old_text == "-":
                old_text = stdin_data
            if new_text == "-":
                new_text = stdin_data
        if old_text is None or new_text is None:
            raise GdocError(
                "old_text and new_text required "
                "(or use --old-file/--new-file)",
                exit_code=3,
            )
    # Input transport must not decide whether a paragraph mark is edited.
    def normalize(text):
        if text is None:
            return None
        return text.replace("\r\n", "\n").replace("\r", "\n").removesuffix("\n")

    return normalize(old_text), normalize(new_text)



def _prepare_text_replacement(
    args, doc_id: str, old_text: str | None,
    *, suggest: bool = False,
) -> _ReplacementPlan:
    """Pre-flight, read the document, and locate the ranges to replace.

    Text search covers every tab unless --tab restricts it. Multiple matches
    require --all; cell addressing retains its existing default-tab behavior.
    Suggestions use SUGGESTIONS_INLINE so read and write indexes agree.
    """
    quiet = getattr(args, "quiet", False)
    replace_all = getattr(args, "all", False)
    case_sensitive = getattr(args, "case_sensitive", False)
    normalize = getattr(args, "normalize", False)
    cell = getattr(args, "cell", None)
    col = getattr(args, "col", None)
    table_index = getattr(args, "table", None)

    # Pre-flight awareness check
    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)

    # Conflict warning (warn but don't block, per spec)
    if change_info and change_info.doc_edited:
        print(
            "WARN: doc changed since last interaction; checking the current target",
            file=sys.stderr,
        )

    # Get document structure + revision ID
    from gdoc.api.docs import (
        find_text_in_document,
        flatten_tabs,
        get_document,
        get_document_with_tabs,
        resolve_tab,
    )

    tab_name = getattr(args, "tab", None)
    tab_id = None

    if suggest:
        from gdoc.api.docs import (
            SUGGESTIONS_INLINE,
            flatten_tabs,
            get_document_structure,
            resolve_tab,
        )
        try:
            doc = get_document_structure(
                doc_id, suggestions_view_mode=SUGGESTIONS_INLINE,
            )
        except GdocError as e:
            # Live: a reader gets 403 "You do not have permission to access
            # the document suggestions." on this view; commenters and
            # editors read it fine.
            if str(e).startswith("Permission denied"):
                raise GdocError(
                    f"{e} (reading suggestions inline needs comment or "
                    "edit access on the document)", exit_code=e.exit_code,
                )
            raise
        revision_id = doc.get("revisionId", "")
        tabs = flatten_tabs(doc.get("tabs", []))
        if not tabs:
            raise GdocError(f"document has no tabs: {doc_id}")
    elif cell is not None and not tab_name:
        doc = get_document(doc_id)
        revision_id = doc.get("revisionId", "")
        tabs = []
    else:
        doc = get_document_with_tabs(doc_id)
        revision_id = doc.get("revisionId", "")
        tabs = flatten_tabs(doc.get("tabs", []))

    if tabs:
        selected = resolve_tab(tabs, tab_name) if tab_name else tabs[0]
        search_body = selected["body"]
        if tab_name or cell is not None:
            search_scope = selected
            tab_id = selected["id"]
        else:
            search_scope = doc
    else:
        if tab_name or "tabs" in doc:
            raise GdocError(f"document has no tabs: {doc_id}")
        search_scope = doc
        search_body = doc.get("body", {})

    if cell is not None:
        from gdoc.api.docs import resolve_cell_range
        cell_range = resolve_cell_range(
            search_body, cell, col=col, table_index=table_index,
            normalize=normalize,
        )
        if cell_range is None:
            raise GdocError(f"cell not found: {cell!r}", exit_code=3)
        matches = [cell_range]
    else:
        matches = find_text_in_document(
            search_scope, old_text, match_case=case_sensitive, normalize=normalize,
        )
        if not matches:
            from gdoc.api.docs import diagnose_no_match
            reason = diagnose_no_match(
                None, old_text, match_case=case_sensitive,
                body=search_body, already_normalized=normalize,
            )
            msg = "no match found" + (f"; {reason}" if reason else "")
            raise GdocError(msg, exit_code=3)
        if not replace_all and len(matches) > 1:
            match_tab_ids = {m.get("tabId") for m in matches}
            matching_tabs = [t for t in tabs if t["id"] in match_tab_ids]
            guidance = "Use --all or more specific text"
            if len(matching_tabs) > 1:
                labels = ", ".join(f"{t['title']} ({t['id']})" for t in matching_tabs)
                guidance = (
                    f"Matching tabs: {labels}. "
                    "Use --all or --tab to narrow scope"
                )
            raise GdocError(
                f"multiple matches ({len(matches)} found). {guidance}",
                exit_code=3,
            )

    match_tabs = {m.get("tabId") for m in matches}
    if cell is None and len(match_tabs) == 1 and None not in match_tabs:
        tab_id = next(iter(match_tabs))

    return _ReplacementPlan(
        quiet=quiet, change_info=change_info, matches=matches,
        revision_id=revision_id, tab_id=tab_id, search_body=search_body,
        search_scope=search_scope,
    )


def _refuse_suggestion_overlaps(plan) -> None:
    """Refuse matches that intersect pending suggestions.

    A direct edit there would silently settle someone's suggestion, and a
    suggested edit could merge into it. Matches elsewhere are unaffected.
    """
    from gdoc.api.docs import _search_containers, find_suggestions_in_range

    containers = {
        (coordinates.get("tabId"), coordinates.get("segmentId")): content
        for content, coordinates in _search_containers(plan.search_scope)
    }
    for m in plan.matches:
        overlapping = find_suggestions_in_range(
            # Cell matches carry no coordinates: they are in the searched body.
            containers.get((m.get("tabId"), m.get("segmentId")), plan.search_body),
            m["startIndex"], m["endIndex"],
        )
        if overlapping:
            ids = ", ".join(sorted(overlapping))
            raise GdocError(
                f"match at index {m['startIndex']} overlaps existing "
                f"suggestion(s) {ids}; accept or reject them in Docs first, or "
                "choose wording outside the suggested text. Nothing was "
                "changed.", exit_code=3,
            )


def cmd_edit(args) -> int:
    """Handler for `gdoc edit`."""
    doc_id = _resolve_doc_id(args.doc)
    cell = getattr(args, "cell", None)

    # Resolve text from args or files (fail fast before API calls)
    old_text, new_text = _resolve_replacement_text(args, cell)

    plan = _prepare_text_replacement(args, doc_id, old_text)
    matches = plan.matches

    from gdoc.api.docs import check_segment_replacement
    from gdoc.mdparse import parse_markdown

    check_segment_replacement(parse_markdown(new_text), new_text, matches)
    _refuse_suggestion_overlaps(plan)

    # Perform formatted replacement via Docs API batchUpdate. A table in the
    # replacement is rejected there when more than one match takes the block
    # path; partial-paragraph matches insert the table source literally.
    from gdoc.api.docs import replace_formatted

    result_details = {}
    occurrences = replace_formatted(
        doc_id, matches, new_text, plan.revision_id, tab_id=plan.tab_id,
        body=plan.replacement_source, result_details=result_details,
        **({"replace_paragraphs": True} if cell is not None else {}),
    )

    # Record the acknowledged write before any optional follow-up lookup.
    _record_acknowledged_write(
        doc_id, input_revision_id=result_details.get("input_revision_id", ""),
        acknowledged_revision_id=result_details.get("acknowledged_revision_id", ""),
        rebased=result_details.get("rebased", False),
    )

    # Get post-edit version for state tracking (Decision #12)
    from gdoc.api.drive import version_after_write

    command_version = version_after_write(doc_id)

    # Output
    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    label = "occurrence" if occurrences == 1 else "occurrences"
    counts = {}
    from gdoc.api.docs import flatten_tabs

    # Name the tabs changed whenever the search covered several tabs, so a
    # unique match outside the tab the agent read is visible.
    searched_tabs = len(flatten_tabs(plan.search_scope.get("tabs", [])))
    if cell is None and (getattr(args, "all", False) or searched_tabs > 1):
        for match in matches:
            if match.get("tabId"):
                key = match["tabId"]
                counts[key] = counts.get(key, 0) + 1
    if mode == "json":
        print(format_json(replaced=occurrences, **({"tabs": counts} if counts else {}),
                          **({"rebased": True} if result_details.get("rebased")
                             else {})))
    elif mode == "plain":
        print(f"id\t{doc_id}")
        print("status\tupdated")
        for key, count in counts.items():
            print(f"tab\t{key}\t{count}")
    else:
        print(f"OK replaced {occurrences} {label}")
        for key, count in counts.items():
            print(f"  tab {key}: {count}")

    _update_state_after_write(
        doc_id, plan.change_info, command="edit",
        quiet=plan.quiet, command_version=command_version,
    )

    return 0


def cmd_suggest(args) -> int:
    """Handler for `gdoc suggest`: a find/replace made as a suggested edit.

    Same matching as `edit`, but the batchUpdate runs with
    writeControl.writeMode=SUGGEST (Docs API Developer Preview) so the
    change lands as a reviewable suggestion — the original text stays in
    place until someone accepts it. There is no fallback: if suggest mode
    is unavailable or unverifiable the command fails and nothing is
    edited directly.
    """
    doc_id = _resolve_doc_id(args.doc)

    old_text, new_text = _resolve_replacement_text(args, None)

    # Structural Markdown needs the multi-batch cleanup/table phases that
    # suggest mode does not run. suggest_replacement rejects it before the
    # write, once the match context shows whether a block marker is
    # structural or literal text inside a paragraph.

    # Capture the token identity before the document read: the write
    # verifies against this baseline, so the grant that read the ranges is
    # the grant that authors the suggestion (see suggest_replacement).
    from gdoc.api import account_cache_key
    from gdoc.api.docs import _token_identity

    read_identity = _token_identity(account_cache_key()[0])

    plan = _prepare_text_replacement(
        args, doc_id, old_text, suggest=True,
    )

    # Never touch an existing review thread by accident: Google may merge a
    # change into an overlapping open suggestion, so refuse any match that
    # intersects one (v1; an explicit opt-in can come later).
    from gdoc.api.docs import check_segment_replacement
    from gdoc.mdparse import parse_markdown

    check_segment_replacement(parse_markdown(new_text), new_text, plan.matches)
    _refuse_suggestion_overlaps(plan)

    from gdoc.api.docs import suggest_replacement

    result = suggest_replacement(
        doc_id, plan.matches, new_text, plan.revision_id, tab_id=plan.tab_id,
        expected_token_identity=read_identity, body=plan.replacement_source,
    )

    _record_targeted_write(doc_id, plan.revision_id, result.acknowledged_revision_id)

    # The suggestion is saved and verified at this point. A failure of the
    # follow-up version lookup must not turn that into an ordinary error
    # that hides the IDs — report success, then warn that state was not
    # refreshed.
    from gdoc.api.drive import get_file_version

    version_error = None
    command_version = None
    try:
        command_version = get_file_version(doc_id).get("version")
    except Exception as e:  # noqa: BLE001 — post-mutation; any failure here
        # (HttpError already translated to GdocError, but also transport
        # ConnectionError/timeout) must not hide the saved suggestion IDs.
        version_error = e

    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    ids = result.suggestion_ids
    if mode == "json":
        print(format_json(
            suggested=result.occurrences,
            suggestionIds=ids,
            createdSuggestionIds=result.created_suggestion_ids,
            updatedSuggestionIds=result.updated_suggestion_ids,
        ))
    elif mode == "plain":
        print(f"id\t{doc_id}")
        print("status\tsuggested")
        print(f"suggested\t{result.occurrences}")
        print("suggestion_ids\t" + ",".join(ids))
    else:
        label = "occurrence" if result.occurrences == 1 else "occurrences"
        tags = ", ".join(f"#{i}" for i in ids)
        print(f"OK suggested {result.occurrences} {label} ({tags})")

    if version_error is not None:
        print(
            "WARN: suggestion saved ("
            + ", ".join(f"#{i}" for i in ids)
            + f") but the document version could not be refreshed: "
            f"{version_error}; awareness state not updated",
            file=sys.stderr,
        )
        return 0

    # A suggestion is a partial write like `edit`: record the new version
    # but do not advance the read baseline (update_state_after_command
    # only does that for full-document writes).
    from gdoc.state import update_state_after_command

    try:
        update_state_after_command(
            doc_id, plan.change_info, command="suggest",
            quiet=plan.quiet, command_version=command_version,
        )
    except Exception as e:  # noqa: BLE001 — post-mutation; the local state
        # file failing to write (read-only dir, full disk) must not turn
        # the saved suggestion into a reported failure that an automated
        # caller would blindly retry.
        print(
            "WARN: suggestion saved ("
            + ", ".join(f"#{i}" for i in ids)
            + f") but awareness state was not persisted: {e}",
            file=sys.stderr,
        )
    return 0


def _comparable_markdown(text: str) -> str:
    """Ignore CRLF and one terminal paragraph mark, not meaningful whitespace."""
    return text.replace("\r\n", "\n").removesuffix("\n")


def cmd_write(args) -> int:
    """Handler for `gdoc write`."""
    import os

    doc_id = _resolve_doc_id(args.doc)
    tab_name = getattr(args, "tab", None)
    file_path = args.file

    # Read local file first (fail fast on missing file)
    if not os.path.isfile(file_path):
        raise GdocError(f"file not found: {file_path}", exit_code=3)
    try:
        with open(file_path, encoding="utf-8", newline="") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise GdocError(f"cannot read file: {e}", exit_code=3) from e

    # Strip frontmatter — pull prepends it, and leaving it in the upload
    # dumps visible YAML into the doc body. A pulled file names its source
    # document and tab; writing it elsewhere must be explicit.
    from gdoc.frontmatter import parse_frontmatter
    metadata, content = parse_frontmatter(content)
    pulled_doc = metadata.get("gdoc")
    if pulled_doc and _resolve_doc_id(pulled_doc) != doc_id:
        raise GdocError(
            f"the file was pulled from document {pulled_doc}, not {doc_id}. "
            "Use `gdoc push` to update its own document, or remove its "
            "frontmatter to copy the text into this one.", 3,
        )

    if (not pulled_doc and "revision" in metadata and "source" in metadata
            and not getattr(args, "force", False)):
        raise GdocError(
            f"this file was pulled from past revision {metadata['revision']}; "
            "writing it would replace the live tab with that older text. Use "
            "--force to restore it intentionally, or remove its frontmatter "
            "to copy the text.", 3,
        )

    # `tab`, `gdoc-revision` and `gdoc-tab-sha256` are provenance only in a
    # file `pull` wrote for this document; they are checked as `push` checks
    # them, so reading a newer copy cannot authorize an older file.
    return _write_native_markdown(
        args, doc_id, content, command="write", tab_name=tab_name,
        file_tab=(metadata.get("tab") or None) if pulled_doc else None,
        file_revision=metadata.get("gdoc-revision", "") if pulled_doc else None,
        file_tab_fingerprint=(metadata.get("gdoc-tab-sha256")
                              if pulled_doc else None),
    )


def _write_native_markdown(
    args, doc_id, content, *, command, tab_name=None, result_details=None,
    file_revision=None, file_tab_fingerprint=None, file_tab=None,
):
    """Share full-content write semantics across CLI, MCP, push and hooks."""
    import re

    from gdoc.api.docs import (
        flatten_tabs,
        get_document_with_tabs,
        get_tab_text,
        insert_markdown_into_tab,
        resolve_tab,
    )
    from gdoc.api.drive import get_file_version, update_doc_content
    from gdoc.format import format_json, get_output_mode
    from gdoc.mdparse import _FENCE_CLOSE_RE, _fence_open
    from gdoc.notify import pre_flight
    from gdoc.state import require_content_baseline

    inspection_header = False
    fence = None
    # Split Markdown lines as the parser does: a soft break (\v) or another
    # Unicode separator stays inside its paragraph's line.
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if fence is not None:
            close = _FENCE_CLOSE_RE.match(line.lstrip())
            if close and close[1][0] == fence[0] and len(close[1]) >= len(fence):
                fence = None
        elif opener := _fence_open(line.lstrip()):
            fence = opener[1]
        elif re.fullmatch(r"=== Tab: .+ ===", line):
            inspection_header = True
    if inspection_header:
        raise GdocError(
            "combined --all-tabs output is an inspection view; read and write "
            "each tab separately with --tab", 3,
        )
    quiet = getattr(args, "quiet", False)
    collapse = getattr(args, "force_collapse_tabs", False)
    if collapse and tab_name:
        raise GdocError("--tab cannot be combined with --force-collapse-tabs", 3)
    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)
    expected_version = (change_info.current_version if change_info else None)
    if expected_version is None:
        expected_version = get_file_version(doc_id).get("version")
    document = get_document_with_tabs(doc_id)
    tabs = flatten_tabs(document.get("tabs", []))
    if not tabs:
        raise GdocError("document has no writable tabs", 3)
    selected = resolve_tab(tabs, tab_name) if tab_name else tabs[0]
    if file_tab:
        # A pulled file's `tab` is where its text came from: it selects the
        # tab when --tab is absent and must agree with an explicit --tab.
        from gdoc.api.docs import _match_tab
        pulled = _match_tab(tabs, file_tab)
        if pulled is None and not tab_name:
            raise GdocError(
                f"the file was pulled from tab {file_tab}, which this document "
                "no longer has. "
                + ("Remove the file's frontmatter to collapse the document "
                   "into this text." if collapse else
                   "Pass --tab to `write` to choose the tab to replace, or "
                   "pull a fresh copy."), 3,
            )
        if pulled is not None and tab_name and pulled["id"] != selected["id"]:
            raise GdocError(
                f"the file was pulled from tab {pulled['title']!r} "
                f"({pulled['id']}), but --tab selects {selected['title']!r} "
                f"({selected['id']}). Remove the file's frontmatter to copy "
                "its text into another tab.", 3,
            )
        if pulled is not None and collapse and pulled["id"] != tabs[0]["id"]:
            raise GdocError(
                f"the file was pulled from tab {pulled['title']!r} "
                f"({pulled['id']}); --force-collapse-tabs replaces the first "
                "tab. Remove the file's frontmatter to collapse the document "
                "into this text.", 3,
            )
        if pulled is not None and not tab_name and not collapse:
            selected, tab_name = pulled, pulled["id"]
    revision = document.get("revisionId", "")
    from gdoc.state import load_state

    aliases = _known_image_aliases(load_state(doc_id), selected["id"], revision)
    from gdoc.mdparse import rename_image_references
    unchanged = (
        not (collapse and len(tabs) > 1)
        and _comparable_markdown(get_tab_text(selected, markdown=True))
        == _comparable_markdown(rename_image_references(content, aliases))
    )
    mode = get_output_mode(args)
    from gdoc.api.docs import native_tab_fingerprint

    # A file pulled at another revision still shows the selected tab when the
    # tab's native content (including styles and suggestions Markdown does not
    # show) is identical now; edits to other tabs changed the revision.
    tab_unchanged = (
        file_revision is not None and not collapse
        and bool(file_tab_fingerprint)
        and native_tab_fingerprint(selected) == file_tab_fingerprint
    )
    file_current = bool(file_revision) and (
        file_revision == revision or tab_unchanged)
    if unchanged:
        if result_details is not None and file_current:
            # The file's provenance advances only when it already covers the
            # selected tab; an unseen native change keeps the file stale.
            result_details.update(
                acknowledged_revision_id=revision, rebased=False,
                tab_fingerprint=native_tab_fingerprint(selected),
            )
        # Matching Markdown is not a read: invisible native changes may differ.
        # Only a file whose recorded native fingerprint matches this snapshot
        # is one; a revision string alone is not.
        if tab_unchanged and _preview_complete([selected]):
            _record_read(doc_id, [selected], revision)
        if mode == "json":
            print(format_json(
                in_sync=True, tab_id=selected["id"], revision_id=revision,
            ))
        elif mode == "plain":
            print(f"id\t{doc_id}\ntab_id\t{selected['id']}\nstatus\tin_sync")
        else:
            print("OK already in sync (selected tab matches; nothing to write)")
        return 0
    # A file that already matches the live tab is in sync whatever its
    # provenance says; only a differing body needs the file's own baseline.
    if file_revision is not None and not getattr(args, "force", False):
        if not file_revision:
            raise GdocError(
                "file has no gdoc-revision, so its baseline is unknown. Pull a "
                "fresh copy and reapply the edits, or use --force to "
                "intentionally overwrite.", 3,
            )
        # The revision is document-wide. A change elsewhere (another tab)
        # leaves the file current when the selected tab's native content is
        # exactly what it was at the file's revision.
        if not file_current:
            raise GdocError(
                "file gdoc-revision is stale; the selected tab changed since "
                "this file was pulled. Reconcile the local and remote edits, or "
                "use --force to intentionally overwrite.", 3,
            )
        if tab_unchanged and _preview_complete([selected]):
            # The native tab is identical to the one the file recorded, so the
            # file is a complete read of that tab at this revision, whether
            # or not the revision moved and even without local state. A
            # revision string alone is not such evidence.
            _record_read(doc_id, [selected], revision)
    require_content_baseline(
        doc_id, [t["id"] for t in tabs] if collapse else [selected["id"]],
        revision, force=getattr(args, "force", False),
        accept_limited=getattr(args, "allow_lossy", False),
        omitted=", ".join(_read_omissions([selected])),
    )
    details = {}
    if tab_name:
        details = insert_markdown_into_tab(
            doc_id, selected["id"], content, replace=True,
            allow_lossy=getattr(args, "allow_lossy", False), document=document,
            image_aliases=aliases,
        )
        from gdoc.api.drive import version_after_write
        version = version_after_write(doc_id)
    else:
        version = update_doc_content(
            doc_id, content, expected_version=expected_version, document=document,
            allow_lossy=getattr(args, "allow_lossy", False),
            collapse_tabs=collapse, result_details=details, image_aliases=aliases,
        )
    acknowledged = details.get("acknowledged_revision_id", "")
    if result_details is not None:
        result_details.update(details)
    _update_state_after_write(
        doc_id, change_info, command=command, quiet=quiet, command_version=version,
    )
    _record_acknowledged_write(
        doc_id, input_revision_id=details.get("input_revision_id", revision),
        acknowledged_revision_id=acknowledged,
        replaced_tab_ids=[selected["id"]], rebased=details.get("rebased", False),
        image_reference_ids=details.get("image_reference_ids"),
    )
    if (file_revision is not None and result_details is not None and acknowledged
            and not details.get("rebased", False)):
        # A file keeps its tab's native fingerprint so that a later edit to
        # another tab leaves it pushable. Only a snapshot at exactly the
        # acknowledged revision shows the written tab without foreign edits.
        try:
            after = get_document_with_tabs(doc_id)
        except Exception as error:  # noqa: BLE001 — the write is already saved
            print("WARN: write saved; its tab could not be fingerprinted "
                  f"({error}), so this file needs a fresh pull after another "
                  "edit to the document", file=sys.stderr)
            after = {}
        if after.get("revisionId") == acknowledged:
            written = next((t for t in flatten_tabs(after.get("tabs", []))
                            if t["id"] == selected["id"]), None)
            if written is not None:
                result_details["tab_fingerprint"] = native_tab_fingerprint(written)
    if mode == "json":
        result = {
            "pushed" if command == "push" else "written": True,
            "tab_id": selected["id"], "tab_title": selected["title"],
            "revision_id": acknowledged,
        }
        if version is not None:
            result["version"] = version
        if details.get("rebased"):
            result["rebased"] = True
        if command == "push":
            result["file"] = args.file
        print(format_json(**result))
    elif mode == "plain":
        print(f"id\t{doc_id}\ntab_id\t{selected['id']}\nstatus\tupdated")
    else:
        verb = "pushed" if command == "push" else "written"
        print(f"OK {verb} tab {selected['title']!r} ({selected['id']})")
    return 0


def _without_inline_objects(tab: dict) -> dict:
    """A copy of *tab* whose paragraphs, including table cells, hold no
    inline images or other inline objects."""
    import copy

    def strip(value):
        if isinstance(value, list):
            for child in value:
                strip(child)
        elif isinstance(value, dict):
            elements = value.get("paragraph", {}).get("elements")
            if isinstance(elements, list):
                elements[:] = [e for e in elements if "inlineObjectElement" not in e]
            for child in value.values():
                strip(child)

    tab = copy.deepcopy(tab)
    strip(tab.get("body", {}))
    return tab


def _note_tab_scope(document: dict, selected: dict, others: str = "--tab") -> None:
    """Say on stderr when a Markdown read covered one of several tabs."""
    from gdoc.api.docs import flatten_tabs

    count = len(flatten_tabs(document.get("tabs", [])))
    if count > 1:
        print(
            f"NOTE: read tab {selected['title']!r} ({selected['id']}); "
            f"{count} tabs exist. Use {others} to read others.",
            file=sys.stderr,
        )
    _note_pending_suggestions([selected])


def _note_pending_suggestions(tabs: list[dict]) -> None:
    """Say on stderr that Markdown shows the text without pending suggestions."""
    from gdoc.api.docs import pending_suggestion_ids, suggestion_preview_gaps

    for tab in tabs:
        count = len(pending_suggestion_ids(tab.get("body", {})))
        if not count:
            continue
        print(
            f"NOTE: tab {tab['title']!r} has {count} pending "
            f"suggestion{'s' if count != 1 else ''}; the Markdown shows its "
            "text without them. Resolve them in Docs (accept or reject) before "
            "rewriting the tab; a rewrite with --allow-lossy instead discards "
            "them and keeps the text as shown.",
            file=sys.stderr,
        )
        gaps = suggestion_preview_gaps(tab)
        if gaps:
            print(
                f"NOTE: gdoc cannot reliably render tab {tab['title']!r} without "
                f"its suggestions ({'; '.join(gaps)}). This read is incomplete "
                "and cannot authorize a rewrite; resolve the suggestions first.",
                file=sys.stderr,
            )


def _preview_complete(tabs: list[dict]) -> bool:
    """Whether a Markdown read shows every tab without its suggestions exactly."""
    from gdoc.api.docs import suggestion_preview_gaps

    return not any(suggestion_preview_gaps(tab) for tab in tabs)


def _read_native_tab(doc_id: str, tab_name: str | None = None, *, document=None):
    """Return editable Markdown and its exact native snapshot provenance."""
    from gdoc.api.docs import (
        flatten_tabs,
        get_document_with_tabs,
        get_tab_text,
        resolve_tab,
    )

    document = document if document is not None else get_document_with_tabs(doc_id)
    tabs = flatten_tabs(document.get("tabs", []))
    if not tabs:
        raise GdocError("document has no readable tabs", exit_code=3)
    selected = resolve_tab(tabs, tab_name) if tab_name else tabs[0]
    return get_tab_text(selected, markdown=True), document, selected


def cmd_pull(args) -> int:
    """Handler for `gdoc pull`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    file_path = args.file
    revision = getattr(args, "revision", None)

    if revision and getattr(args, "tab", None):
        raise GdocError("--revision cannot be combined with --tab", 3)

    # Pre-flight awareness check
    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)

    # Export doc (or one past revision) as markdown
    from gdoc.api.drive import get_file_info

    rev = None
    document = selected = None
    if revision:
        from gdoc.api.revisions import export_revision

        rev = _resolve_revision(doc_id, revision)
        markdown = export_revision(
            doc_id, rev["id"], mime_type="text/markdown",
            export_links=rev.get("exportLinks"),
        )
    else:
        markdown, document, selected = _read_native_tab(
            doc_id, getattr(args, "tab", None),
            document=getattr(args, "_document_snapshot", None),
        )
    metadata = get_file_info(doc_id)
    title = metadata.get("name", "")

    # Add frontmatter and write to local file. Revision pulls
    # deliberately omit the `gdoc:` key — push and the sync hooks key
    # off it, and silently pushing a stale revision over the live doc
    # is a footgun.
    from gdoc.api.docs import native_tab_fingerprint
    from gdoc.frontmatter import add_frontmatter, body_fingerprint

    if rev is not None:
        front = {"source": doc_id, "revision": rev["id"], "title": title}
    else:
        front = {"gdoc": doc_id, "title": title, "tab": selected["id"],
                 "gdoc-revision": document.get("revisionId", ""),
                 "gdoc-body-sha256": body_fingerprint(markdown),
                 "gdoc-tab-sha256": native_tab_fingerprint(selected)}
    content = add_frontmatter(markdown, front)

    from gdoc.frontmatter import preserve_and_replace

    try:
        if not preserve_and_replace(
            file_path, content, expected=getattr(args, "_expected_local_content", None),
        ):
            raise GdocError("local file changed during pull; replacement skipped", 3)
    except OSError as e:
        raise GdocError(f"cannot write file: {e}", exit_code=3)

    # Output
    from gdoc.format import get_output_mode, format_json

    rev_label = f" @ rev {rev['id']}" if rev is not None else ""
    mode = get_output_mode(args)
    omitted = _read_omissions([selected]) if document is not None else []
    if document is not None:
        _note_tab_scope(document, selected)
        _note_omissions(omitted)
    if mode == "json":
        if rev is not None:
            print(format_json(
                pulled=True, title=title, file=file_path,
                revision=rev["id"],
            ))
        else:
            extra = {"omitted": omitted} if omitted else {}
            print(format_json(pulled=True, title=title, file=file_path,
                              tab=selected["title"], tab_id=selected["id"],
                              complete=_preview_complete([selected]) and not omitted,
                              **extra))
    elif mode == "plain":
        print(f"path\t{file_path}")
        if rev is not None:
            print(f"revision\t{rev['id']}")
    elif mode == "verbose":
        print(f'Pulled: "{title}"{rev_label}')
        print(f"File: {file_path}")
        print(f"Doc ID: {doc_id}")
    else:
        print(f'OK pulled "{title}"{rev_label} -> {file_path}')

    # Update state (pull is a read command; a revision pull is not a
    # read of the current content, so it must not advance the read
    # baseline used by write-conflict checks)
    command_version = metadata.get("version")
    if command_version is not None:
        command_version = int(command_version)
    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info,
        command="pull-content" if rev is None else "pull-revision",
        quiet=quiet,
        command_version=command_version if rev is None else None,
    )

    if document is not None and _preview_complete([selected]):
        _record_read(doc_id, [selected], document.get("revisionId", ""))
    return 0


def cmd_push(args) -> int:
    """Handler for `gdoc push`."""
    import os

    file_path = args.file

    # Read local file (fail fast)
    if not os.path.isfile(file_path):
        raise GdocError(f"file not found: {file_path}", exit_code=3)
    try:
        with open(file_path, encoding="utf-8", newline="") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise GdocError(f"cannot read file: {e}", exit_code=3)

    # Parse frontmatter
    from gdoc.frontmatter import parse_frontmatter

    metadata, body = parse_frontmatter(content)
    if "gdoc" not in metadata:
        # pull --revision writes both keys; requiring both avoids
        # false positives on unrelated files with a `source:` key
        if "revision" in metadata and "source" in metadata:
            raise GdocError(
                "this file was pulled from a past revision and is not "
                "pushable (it would overwrite the live doc with stale "
                "content). Use 'gdoc pull' for an editable copy.",
                exit_code=3,
            )
        raise GdocError(
            "no gdoc frontmatter found. Use 'gdoc pull' first.",
            exit_code=3,
        )

    doc_id = _resolve_doc_id(metadata["gdoc"])

    details = {}
    result = _write_native_markdown(
        args, doc_id, body, command="push",
        tab_name=(None if getattr(args, "force_collapse_tabs", False)
                  else metadata.get("tab")),
        # As for `write`, a collapse keeps only the first tab, so it refuses
        # a file pulled from a later one.
        file_tab=metadata.get("tab") or None,
        file_revision=metadata.get("gdoc-revision", ""), result_details=details,
        file_tab_fingerprint=metadata.get("gdoc-tab-sha256"),
    )
    _refresh_file_revision(file_path, content, details)
    return result


def _refresh_file_revision(file_path, content, details):
    """Advance file provenance only for known, acknowledged uploaded content."""
    acknowledged = details.get("acknowledged_revision_id")
    if not acknowledged or details.get("rebased", False):
        return
    from gdoc.frontmatter import (
        body_fingerprint,
        parse_frontmatter,
        preserve_and_replace,
        update_frontmatter_value,
    )

    _, body = parse_frontmatter(content)
    updated = update_frontmatter_value(content, "gdoc-revision", acknowledged)
    updated = update_frontmatter_value(
        updated, "gdoc-body-sha256", body_fingerprint(body),
    )
    # Without a snapshot at the acknowledged revision the tab's native state is
    # unknown; an empty fingerprint makes any later revision change stale.
    updated = update_frontmatter_value(
        updated, "gdoc-tab-sha256", details.get("tab_fingerprint", ""),
    )
    if updated == content:
        return
    try:
        preserve_and_replace(file_path, updated, expected=content)
    except OSError as error:
        print("WARN: upload acknowledged, but file provenance was not updated: "
              f"{error}",
              file=sys.stderr)


def _hook_notice(data: dict, message: str) -> None:
    """Report a skipped or failed sync to stderr and to the agent's context.

    Claude Code does not show a hook's stderr to the model on exit 0, so a
    hook event also gets the message as ``additionalContext`` on stdout.
    """
    import json

    print(message, file=sys.stderr)
    event = data.get("hook_event_name") if isinstance(data, dict) else None
    if event:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event, "additionalContext": "gdoc " + message,
        }}))


def cmd_sync_hook(args) -> int:
    """Handler for `gdoc _sync-hook` (called by PostToolUse hook)."""
    import json
    import os

    data = {}
    try:
        raw = sys.stdin.read()
        if not raw:
            return 0

        data = json.loads(raw)
        tool_input = data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")

        if not file_path or not file_path.endswith(".md"):
            return 0
        if not os.path.isfile(file_path):
            return 0

        with open(file_path, encoding="utf-8", newline="") as f:
            content = f.read()

        from gdoc.frontmatter import parse_frontmatter

        metadata, body = parse_frontmatter(content)
        if "gdoc" not in metadata:
            return 0

        doc_id = _resolve_doc_id(metadata["gdoc"])

        from contextlib import redirect_stdout
        from types import SimpleNamespace

        hook_args = SimpleNamespace(
            file=file_path, quiet=True, force=False, force_collapse_tabs=False,
            allow_lossy=False, json=False, verbose=False, plain=False,
        )
        write_result = {}
        try:
            with redirect_stdout(sys.stderr):
                _write_native_markdown(
                    hook_args, doc_id, body, command="push",
                    tab_name=metadata.get("tab"), result_details=write_result,
                    file_revision=metadata.get("gdoc-revision", ""),
                    file_tab_fingerprint=metadata.get("gdoc-tab-sha256"),
                )
        except GdocError as error:
            if error.exit_code != 3:
                raise
            _hook_notice(data, f"SYNC: skipped {file_path} (replacement safety "
                               f"check: {error})")
            return 0
        _refresh_file_revision(file_path, content, write_result)
        print(f"SYNC: pushed to {metadata.get('title', doc_id)!r}", file=sys.stderr)

    except Exception as e:
        # Keep the hook non-blocking, but never hide a failed read or upload.
        _hook_notice(data, f"ERR: SYNC: failed: {e}")

    return 0


def cmd_pull_hook(args) -> int:
    """Handler for `gdoc _pull-hook` (called by PreToolUse hook)."""
    import json
    import os

    data = {}
    try:
        raw = sys.stdin.read()
        if not raw:
            return 0

        data = json.loads(raw)
        tool_input = data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")

        if not file_path or not file_path.endswith(".md"):
            return 0
        if not os.path.isfile(file_path):
            return 0

        with open(file_path, encoding="utf-8", newline="") as f:
            content = f.read()

        from gdoc.frontmatter import parse_frontmatter

        metadata, body = parse_frontmatter(content)
        if "gdoc" not in metadata:
            return 0

        from gdoc.frontmatter import body_fingerprint

        if metadata.get("gdoc-body-sha256") != body_fingerprint(body):
            _hook_notice(data, f"SYNC: pull skipped for {file_path} (local edits "
                         "not pushed, or no verified local baseline; reconcile "
                         "with a separate pull)")
            return 0
        doc_id = _resolve_doc_id(metadata["gdoc"])

        from gdoc.api.docs import get_document_with_tabs

        snapshot = get_document_with_tabs(doc_id)
        revision = snapshot.get("revisionId", "")
        if revision and metadata.get("gdoc-revision") == revision:
            return 0

        from contextlib import redirect_stdout
        from types import SimpleNamespace

        with redirect_stdout(sys.stderr):
            cmd_pull(SimpleNamespace(
                doc=doc_id, file=file_path, tab=metadata.get("tab"), quiet=True,
                revision=None, json=False, verbose=False, plain=False,
                _document_snapshot=snapshot, _expected_local_content=content,
            ))
        print(f"SYNC: pulled {metadata.get('title', doc_id)!r}", file=sys.stderr)

    except Exception as error:
        _hook_notice(data, f"ERR: SYNC: pull failed: {error}")

    return 0


def _resolve_diff_format(args) -> str:
    """Resolve the effective renderer for a revision diff."""
    import sys

    from gdoc.format import get_output_mode

    fmt = getattr(args, "format", "auto")
    out = getattr(args, "out", None)
    mode = get_output_mode(args)

    if fmt == "auto" and out:
        if out.endswith(".html"):
            fmt = "html"
        else:
            raise GdocError(
                f"cannot infer format from {out!r} (expected .html); "
                "pass --format",
                exit_code=3,
            )
    # --json composes with the html artifact (JSON write confirmation
    # on stdout) but not with the terminal formats
    if mode == "json" and fmt in ("color", "plain"):
        raise GdocError(
            f"--json and --format {fmt} are mutually exclusive",
            exit_code=3,
        )
    if mode == "plain" and fmt == "color":
        raise GdocError(
            "--plain and --format color are mutually exclusive",
            exit_code=3,
        )
    if out and fmt != "html":
        raise GdocError(
            "--out requires --format html "
            "(redirect stdout for text formats)",
            exit_code=3,
        )
    if fmt == "auto":
        if mode == "json":
            return "json"
        if mode == "plain":
            return "plain"
        return "color" if sys.stdout.isatty() else "plain"
    return fmt


def _diff_revisions(args, doc_id: str) -> int:
    """Revision-vs-revision diff (`gdoc diff --rev` / `--since`)."""
    quiet = getattr(args, "quiet", False)
    since = getattr(args, "since", None)
    min_common = getattr(args, "min_common", DEFAULT_MIN_COMMON)
    context = getattr(args, "context", DEFAULT_CONTEXT)
    with_comments = getattr(args, "with_comments", False)

    fmt = _resolve_diff_format(args)
    if with_comments and fmt in ("color", "plain"):
        raise GdocError(
            "--with-comments requires --format html or json "
            "(the terminal renderer does not show comments)",
            exit_code=3,
        )

    from gdoc.revdiff import (
        build_diff_model,
        hunk_changed,
        parse_rev_range,
        parse_timestamp,
        resolve_at_timestamp,
        resolve_selector,
    )

    # Validate selector syntax before any API call
    if since:
        parse_timestamp(since)
        old_sel = new_sel = None
    else:
        old_sel, new_sel = parse_rev_range(args.rev)

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)

    from gdoc.api.revisions import export_revision, list_revisions

    revisions = list_revisions(doc_id)
    if since:
        old_rev = resolve_at_timestamp(revisions, since)
        new_rev = resolve_selector(revisions, "latest")
    else:
        old_rev = resolve_selector(revisions, old_sel)
        new_rev = resolve_selector(revisions, new_sel)

    from gdoc.api.drive import get_file_info
    metadata = get_file_info(doc_id)
    doc_name = metadata.get("name", doc_id)

    old_md = export_revision(
        doc_id, old_rev["id"], export_links=old_rev.get("exportLinks"),
    )
    new_md = export_revision(
        doc_id, new_rev["id"], export_links=new_rev.get("exportLinks"),
    )

    model = build_diff_model(
        doc_id, doc_name, old_rev, new_rev, old_md, new_md,
        min_common=min_common,
    )

    if with_comments:
        from gdoc.api.comments import list_comments
        from gdoc.revdiff import attach_comments

        comments = list_comments(doc_id, include_anchor=True)
        model["comments"] = attach_comments(model["hunks"], comments)

    changed = sum(1 for h in model["hunks"] if hunk_changed(h))

    from gdoc.format import format_json, get_output_mode
    mode = get_output_mode(args)

    if fmt == "json":
        print(format_json(identical=changed == 0, **model))
    elif fmt == "html":
        out_path = getattr(args, "out", None) or "gdoc-diff.html"
        from gdoc.diffrender import render_html
        try:
            with open(out_path, "w") as f:
                f.write(render_html(model, context=context))
        except OSError as e:
            raise GdocError(f"cannot write file: {e}", exit_code=3) from e

        inline = None
        anchored = ""
        if "comments" in model:
            inline = sum(
                1 for c in model["comments"] if c["hunk"] is not None
            )
            anchored = f", {inline}/{len(model['comments'])} comments anchored"
        if mode == "json":
            confirmation = {
                "path": out_path,
                "format": fmt,
                "changed": changed,
                "identical": changed == 0,
            }
            if inline is not None:
                confirmation["comments"] = len(model["comments"])
                confirmation["comments_anchored"] = inline
            print(format_json(**confirmation))
        elif mode == "plain":
            print(f"path\t{out_path}")
            print(f"changed\t{changed}")
        elif mode == "verbose":
            print(f"Wrote: {out_path}")
            print(f"Revisions: {old_rev['id']} -> {new_rev['id']}")
            print(f"Changed hunks: {changed}{anchored}")
        else:
            print(f"OK wrote {out_path} ({changed} changed hunks{anchored})")
    elif changed == 0:
        print(f"OK identical (rev {old_rev['id']} -> rev {new_rev['id']})")
    else:
        from gdoc.diffrender import render_terminal
        print(
            render_terminal(model, color=fmt == "color", context=context),
            end="",
        )

    # Update state (version already fetched via get_file_info above)
    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info, command="diff", quiet=quiet,
        command_version=metadata.get("version"),
    )

    return 1 if changed else 0


def cmd_diff(args) -> int:
    """Handler for `gdoc diff`."""
    import difflib
    import os

    doc_id = _resolve_doc_id(args.doc)
    file_path = getattr(args, "file", None)
    rev = getattr(args, "rev", None)
    since = getattr(args, "since", None)

    if rev and since:
        raise GdocError(
            "--rev and --since are mutually exclusive", exit_code=3,
        )
    if file_path and (rev or since):
        raise GdocError(
            "FILE and --rev/--since are mutually exclusive (a file diff "
            "always compares against the current document)",
            exit_code=3,
        )
    if (rev or since) and getattr(args, "tab", None):
        raise GdocError("--tab applies to file diffs, not revision diffs", 3)
    if rev or since:
        return _diff_revisions(args, doc_id)
    if not file_path:
        raise GdocError(
            "nothing to compare: pass a local FILE, or --rev/--since "
            "to diff revisions",
            exit_code=3,
        )
    if (
        getattr(args, "format", "auto") != "auto"
        or getattr(args, "out", None)
        or getattr(args, "with_comments", False)
    ):
        raise GdocError(
            "--format/--out/--with-comments apply to revision diffs "
            "(--rev/--since), not file diffs",
            exit_code=3,
        )

    quiet = getattr(args, "quiet", False)
    use_plain = getattr(args, "plain", False)

    # Pre-flight
    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)

    # Read local file
    if not os.path.isfile(file_path):
        raise GdocError(f"file not found: {file_path}", exit_code=3)
    try:
        with open(file_path, encoding="utf-8", newline="") as f:
            local = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise GdocError(f"cannot read file: {e}", exit_code=3)
    # Compare the body with the tab that cat, pull and write use.
    from gdoc.api.docs import get_tab_text
    from gdoc.frontmatter import parse_frontmatter

    metadata, local = parse_frontmatter(local)
    local = local.replace("\r\n", "\n")
    # As for `write`, `tab` is provenance only in a file pulled from this doc.
    pulled = metadata.get("gdoc")
    file_tab = (metadata.get("tab")
                if pulled and _resolve_doc_id(pulled) == doc_id else None)
    _, document, selected = _read_native_tab(
        doc_id, getattr(args, "tab", None) or file_tab,
    )
    remote = get_tab_text(selected, markdown=not use_plain)

    # Diff
    remote_lines = remote.splitlines(keepends=True)
    local_lines = local.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        remote_lines, local_lines,
        fromfile=f"gdoc:{doc_id[:12]}", tofile=file_path,
    ))

    # Output
    from gdoc.format import get_output_mode, format_json

    mode = get_output_mode(args)

    if mode == "json":
        print(format_json(identical=len(diff) == 0, diff="".join(diff)))
    elif diff:
        print("".join(diff), end="")
    else:
        print("OK identical")

    # Update state
    from gdoc.state import update_state_after_command
    from gdoc.api.drive import get_file_version

    command_version = get_file_version(doc_id).get("version")
    update_state_after_command(
        doc_id, change_info, command="diff", quiet=quiet,
        command_version=command_version,
    )

    return 1 if diff else 0


def cmd_comments(args) -> int:
    """Handler for `gdoc comments`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)

    # Pre-flight awareness check
    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    # Full fetch for display (separate from pre-flight, per CONTEXT.md Decision #8)
    from gdoc.api.comments import list_comments
    include_resolved = getattr(args, "all", False)
    comments = list_comments(
        doc_id, include_resolved=include_resolved, include_anchor=True,
    )

    from gdoc.format import get_output_mode, format_json

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(comments=comments))
    elif mode == "plain":
        for c in comments:
            cid = c.get("id", "")
            resolved = c.get("resolved", False)
            status = "resolved" if resolved else "open"
            author = c.get("author", {})
            author_str = author.get("emailAddress") or author.get("displayName", "unknown")
            content = c.get("content", "")
            quoted = c.get("quotedFileContent", {}).get("value", "").replace("\t", " ")
            print(f"{cid}\t{status}\t{author_str}\t{content}\t{quoted}")
    elif not comments:
        print("No comments.")
    else:
        for c in comments:
            cid = c.get("id", "")
            resolved = c.get("resolved", False)
            status = "resolved" if resolved else "open"
            author = c.get("author", {})
            author_str = author.get("emailAddress") or author.get("displayName", "unknown")
            created = c.get("createdTime", "")
            if mode == "verbose":
                date_str = created
            else:
                date_str = created[:10] if created else ""
            print(f"#{cid} [{status}] {author_str} {date_str}")
            content = c.get("content", "")
            print(f'  "{content}"')
            quoted = c.get("quotedFileContent", {}).get("value", "")
            if quoted:
                print(f'  on "{quoted}"')
            for r in c.get("replies", []):
                reply_content = r.get("content", "")
                if not reply_content:
                    continue  # Skip action-only replies
                r_author = r.get("author", {})
                r_author_str = r_author.get("emailAddress") or r_author.get("displayName", "unknown")
                print(f'  -> {r_author_str}: "{reply_content}"')

    # Update state
    from gdoc.state import update_state_after_command
    update_state_after_command(doc_id, change_info, command="comments", quiet=quiet)

    return 0


@dataclass
class CommentAnchorResult:
    """An anchored write, a safe refusal, or a definite preview rejection."""

    status: Literal[
        "anchored", "ambiguous", "not_found", "conflict", "preview_unavailable",
    ]
    comment_id: str = ""
    locations: list[dict] = field(default_factory=list)
    detail: str = ""


def _try_anchored_comment(
    doc_id: str, text: str, quote: str, tab_name: str | None = None,
    occurrence: int | None = None,
) -> CommentAnchorResult:
    """Resolve a unique quote and write it, retrying one rejected revision.

    *occurrence* picks the Nth match (1-based) when the quote repeats. The
    order is stable rather than visual: tabs in outline order, and within a
    tab the body first, then headers, footers and footnotes each sorted by
    segment ID. It is re-applied to a fresh read after a conflict.

    Only a definite preview rejection permits Drive fallback. Successful but
    incomplete responses and transport failures propagate without another write.
    """
    from gdoc.api.docs import (
        CommentRevisionConflictError,
        find_text_in_document,
        get_document_with_tabs,
        insert_comment,
        resolve_tab,
    )
    from gdoc.util import PreviewUnavailableError, fold_unicode_spaces

    def fold_spaces(value):
        # One character stays one character: reuse the existing offset mapper.
        # Fold a copy of the body and the quote identically, including NBSP.
        if isinstance(value, str):
            return fold_unicode_spaces(value)
        if isinstance(value, dict):
            return {key: fold_spaces(item) for key, item in value.items()}
        if isinstance(value, list):
            return [fold_spaces(item) for item in value]
        return value

    for attempt in range(2):
        document = get_document_with_tabs(doc_id)
        revision_id = document.get("revisionId", "")
        tabs = []
        pending = list(document.get("tabs", []))
        while pending:
            raw_tab = pending.pop(0)
            props = raw_tab.get("tabProperties", {})
            tabs.append({
                **raw_tab.get("documentTab", {}),
                "id": props.get("tabId"), "title": props.get("title", ""),
            })
            pending[0:0] = raw_tab.get("childTabs", [])
        if not tabs:
            tabs = [{**document, "id": None, "title": "first tab"}]
        if tab_name is not None:
            tabs = [resolve_tab(tabs, tab_name)]

        # Matches with the quote's own letter case win; other casings count
        # only when none exist, so "Apple" is not ambiguous with "apple".
        by_case = {True: [], False: []}
        for tab in tabs:
            # Keyed segment maps have no document order, so number them by
            # kind and then by segment ID: stable across the conflict re-read.
            segments = [(None, tab.get("body", {}))]
            for kind in ("headers", "footers", "footnotes"):
                segments.extend(sorted(tab.get(kind, {}).items()))
            for segment_id, body in segments:
                for match_case, found in by_case.items():
                    for match in find_text_in_document(
                        None, fold_spaces(quote), body=fold_spaces(body),
                        normalize=True, allow_native_gaps=True,
                        match_case=match_case,
                    ):
                        found.append({
                            **match, "tabId": tab["id"], "tabTitle": tab["title"],
                            "segmentId": segment_id,
                        })
        locations = by_case[True] or by_case[False]
        if not locations:
            return CommentAnchorResult(
                "not_found", detail="Quote not found after Unicode normalization",
            )
        if occurrence is not None:
            if not 1 <= occurrence <= len(locations):
                return CommentAnchorResult(
                    "not_found", locations=locations,
                    detail=(
                        f"--occurrence {occurrence} is out of range: the quote "
                        f"matches {len(locations)} time(s)"
                    ),
                )
            locations = [locations[occurrence - 1]]
        if len(locations) > 1:
            return CommentAnchorResult("ambiguous", locations=locations)
        if not revision_id:
            return CommentAnchorResult(
                "conflict", locations=locations,
                detail="No revision ID available; cannot safely pin the comment",
            )
        location = locations[0]
        segment = (
            {"segment_id": location["segmentId"]} if location["segmentId"] else {}
        )
        try:
            comment_id = insert_comment(
                doc_id, text, location["startIndex"], location["endIndex"],
                tab_id=location["tabId"], revision_id=revision_id, **segment,
            )
        except CommentRevisionConflictError as e:
            if attempt == 0:
                continue
            return CommentAnchorResult("conflict", locations=locations, detail=str(e))
        except PreviewUnavailableError as e:
            return CommentAnchorResult(
                "preview_unavailable", locations=locations, detail=str(e),
            )
        return CommentAnchorResult("anchored", comment_id, locations)
    raise AssertionError("unreachable")


def cmd_comment(args) -> int:
    """Handler for `gdoc comment`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)

    # Local usage errors come before any API call, including pre-flight.
    quote = getattr(args, "quote", "") or ""
    occurrence = getattr(args, "occurrence", None)
    if occurrence is not None and occurrence < 1:
        raise GdocError(
            "--occurrence must be positive; no comment created", exit_code=3,
        )
    selectors = [
        flag for flag, value in (
            ("--tab", getattr(args, "tab", None)),
            ("--occurrence", getattr(args, "occurrence", None)),
        ) if value is not None
    ]
    if selectors and not quote:
        raise GdocError(
            f"{' and '.join(selectors)} require --quote; no comment created",
            exit_code=3,
        )

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    new_id = ""
    resolution = None
    if quote:
        resolution = _try_anchored_comment(
            doc_id, args.text, quote, tab_name=getattr(args, "tab", None),
            occurrence=getattr(args, "occurrence", None),
        )
        if resolution.status == "ambiguous":
            locations = "; ".join(
                f"tab {loc['tabTitle']!r} ({loc['tabId']}), "
                f"segment {loc['segmentId'] or 'body'}, "
                f"range {loc['startIndex']}:{loc['endIndex']}"
                for loc in resolution.locations
            )
            raise GdocError(
                f"Quote is ambiguous: {len(resolution.locations)} matches: "
                f"{locations}. Use a longer quote, --tab to select a single tab, "
                "or --occurrence N to pick the Nth match in document order.",
                exit_code=3,
            )
        if resolution.status in ("not_found", "conflict"):
            raise GdocError(resolution.detail + "; no comment created", exit_code=3)
        new_id = resolution.comment_id
    anchored = resolution is not None and resolution.status == "anchored"
    if not anchored:
        from gdoc.api.comments import create_comment
        result = create_comment(doc_id, args.text, quote=quote)
        new_id = result["id"]
        if quote:
            print(f"Comment created unanchored: {resolution.detail}", file=sys.stderr)

    from gdoc.api.drive import version_after_write
    command_version = version_after_write(doc_id)

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        extra = {"anchored": anchored}
        if not anchored:
            extra["reason"] = resolution.status if resolution else "no_quote"
        if anchored:
            extra["tabId"] = resolution.locations[0]["tabId"]
        print(format_json(id=new_id, status="created", **extra))
    elif mode == "plain":
        print(f"id\t{new_id}")
        print(f"anchored\t{'true' if anchored else 'false'}")
        if not anchored:
            print(f"reason\t{resolution.status if resolution else 'no_quote'}")
        if anchored:
            print(f"tabId\t{resolution.locations[0]['tabId']}")
    else:
        suffix = " (anchored)" if anchored else " (unanchored)" if quote else ""
        print(f"OK comment #{new_id}{suffix}")
        if anchored:
            location = resolution.locations[0]
            print(f"Tab: {location['tabTitle']} ({location['tabId']})")

    _update_state_after_write(
        doc_id, change_info, command="comment", quiet=quiet,
        command_version=command_version,
        comment_state_patch={"add_comment_id": new_id},
    )

    return 0


def cmd_reply(args) -> int:
    """Handler for `gdoc reply`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    comment_id = args.comment_id

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.comments import create_reply
    result = create_reply(doc_id, comment_id, content=args.text)
    reply_id = result["id"]

    from gdoc.api.drive import version_after_write
    command_version = version_after_write(doc_id)

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(commentId=comment_id, replyId=reply_id, status="created"))
    elif mode == "plain":
        print(f"commentId\t{comment_id}")
        print(f"replyId\t{reply_id}")
    else:
        print(f"OK reply on #{comment_id}")

    _update_state_after_write(
        doc_id, change_info, command="reply", quiet=quiet,
        command_version=command_version,
        comment_state_patch={"add_comment_id": comment_id},
    )

    return 0


def cmd_resolve(args) -> int:
    """Handler for `gdoc resolve`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    comment_id = args.comment_id
    message = getattr(args, "message", "") or ""

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.comments import create_reply
    create_reply(doc_id, comment_id, content=message, action="resolve")

    from gdoc.api.drive import version_after_write
    command_version = version_after_write(doc_id)

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=comment_id, status="resolved"))
    elif mode == "plain":
        print(f"id\t{comment_id}")
        print(f"status\tresolved")
    else:
        print(f"OK resolved comment #{comment_id}")

    _update_state_after_write(
        doc_id, change_info, command="resolve", quiet=quiet,
        command_version=command_version,
        comment_state_patch={"add_comment_id": comment_id, "add_resolved_id": comment_id},
    )

    return 0


def cmd_reopen(args) -> int:
    """Handler for `gdoc reopen`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    comment_id = args.comment_id

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.comments import create_reply
    create_reply(doc_id, comment_id, action="reopen")

    from gdoc.api.drive import version_after_write
    command_version = version_after_write(doc_id)

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=comment_id, status="reopened"))
    elif mode == "plain":
        print(f"id\t{comment_id}")
        print(f"status\treopened")
    else:
        print(f"OK reopened comment #{comment_id}")

    _update_state_after_write(
        doc_id, change_info, command="reopen", quiet=quiet,
        command_version=command_version,
        comment_state_patch={"add_comment_id": comment_id, "remove_resolved_id": comment_id},
    )

    return 0


def cmd_delete_comment(args) -> int:
    """Handler for `gdoc delete-comment`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    comment_id = args.comment_id
    force = getattr(args, "force", False)

    from gdoc.util import confirm_destructive
    confirm_destructive(f"delete comment #{comment_id}", force=force)

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.comments import delete_comment
    delete_comment(doc_id, comment_id)

    from gdoc.api.drive import version_after_write
    command_version = version_after_write(doc_id)

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=comment_id, status="deleted"))
    elif mode == "plain":
        print(f"id\t{comment_id}")
        print(f"status\tdeleted")
    else:
        print(f"OK deleted comment #{comment_id}")

    _update_state_after_write(
        doc_id, change_info, command="delete-comment", quiet=quiet,
        command_version=command_version,
        comment_state_patch={"remove_comment_id": comment_id},
    )

    return 0


def cmd_comment_info(args) -> int:
    """Handler for `gdoc comment-info`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    comment_id = args.comment_id

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.comments import get_comment
    comment = get_comment(doc_id, comment_id)

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)

    resolved = comment.get("resolved", False)
    status = "resolved" if resolved else "open"
    author = comment.get("author", {})
    author_str = author.get("emailAddress") or author.get("displayName", "unknown")
    content = comment.get("content", "")
    created = comment.get("createdTime", "")
    modified = comment.get("modifiedTime", "")
    quoted = comment.get("quotedFileContent", {}).get("value", "")
    replies = comment.get("replies", [])

    if mode == "json":
        print(format_json(comment=comment))
    elif mode == "plain":
        print(f"id\t{comment_id}")
        print(f"status\t{status}")
        print(f"author\t{author_str}")
        print(f"created\t{created}")
        print(f"content\t{content}")
        if quoted:
            print(f"quote\t{quoted}")
        print(f"replies\t{len(replies)}")
    elif mode == "verbose":
        print(f"#{comment_id} [{status}] {author_str} {created}")
        print(f'  "{content}"')
        if quoted:
            print(f'  on "{quoted}"')
        print(f"  Modified: {modified}")
        for r in replies:
            r_author = r.get("author", {})
            r_author_str = r_author.get("emailAddress") or r_author.get("displayName", "unknown")
            r_content = r.get("content", "")
            r_action = r.get("action", "")
            r_created = r.get("createdTime", "")
            if r_content:
                print(f'  -> {r_author_str} {r_created}: "{r_content}"')
            elif r_action:
                print(f"  -> {r_author_str} {r_created}: [{r_action}]")
    else:
        # terse
        print(f"#{comment_id} [{status}] {author_str} {created[:10] if created else ''}")
        print(f'  "{content}"')
        if replies:
            label = "reply" if len(replies) == 1 else "replies"
            print(f"  {len(replies)} {label}")

    from gdoc.state import update_state_after_command
    update_state_after_command(doc_id, change_info, command="comment-info", quiet=quiet)

    return 0


def cmd_images(args) -> int:
    """Handler for `gdoc images`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    image_id = getattr(args, "image_id", None)
    download_dir = getattr(args, "download", None)

    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)

    from gdoc.api.docs import list_inline_objects

    images = list_inline_objects(doc_id)

    if image_id:
        images = [img for img in images if img["id"] == image_id]
        if not images:
            raise GdocError(f"image not found: {image_id}", exit_code=3)

    if download_dir:
        os.makedirs(download_dir, exist_ok=True)

        from gdoc.api.docs import download_image

        for img in images:
            if img["type"] == "drawing":
                print(
                    f"WARN: {img['id']} is a drawing (cannot export)",
                    file=sys.stderr,
                )
                continue
            if not img.get("content_uri"):
                print(
                    f"WARN: {img['id']} has no content URI",
                    file=sys.stderr,
                )
                continue
            ext = "png"
            dest = os.path.join(download_dir, f"{img['id']}.{ext}")
            download_image(img["content_uri"], dest)
            print(dest)
    else:
        from gdoc.format import format_json, get_output_mode

        mode = get_output_mode(args)
        if mode == "json":
            print(format_json(images=images))
        elif mode == "plain":
            for img in images:
                print(
                    f"{img['id']}\t{img['type']}\t{img['title']}"
                    f"\t{img['width_pt']}\t{img['height_pt']}"
                    f"\t{img.get('tab', '')}"
                )
        elif not images:
            print("No images.")
        else:
            for img in images:
                title = f'"{img["title"]}"' if img["title"] else "(no title)"
                dims = f"{img['width_pt']}x{img['height_pt']}pt"
                if img["type"] == "drawing":
                    dims = "(not exportable)"
                if mode == "verbose":
                    desc = img["description"] or ""
                    print(f"{img['id']}  {img['type']}  {title}  {dims}  {desc}")
                else:
                    print(f"{img['id']}  {img['type']}  {title}  {dims}")

    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info, command="images", quiet=quiet,
    )

    return 0


# Rendered/binary formats must go to a file; text formats may hit stdout.
_EXPORT_MIME = {
    "pdf": "application/pdf",
    "docx": (
        "application/vnd.openxmlformats-officedocument"
        ".wordprocessingml.document"
    ),
    "odt": "application/vnd.oasis.opendocument.text",
    "epub": "application/epub+zip",
    "html": "text/html",
    "md": "text/markdown",
    "txt": "text/plain",
    "rtf": "application/rtf",
}
_BINARY_FORMATS = {"pdf", "docx", "odt", "epub"}
_EXT_TO_FORMAT = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".odt": "odt",
    ".epub": "epub",
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".rtf": "rtf",
}


def cmd_export(args) -> int:
    """Handler for `gdoc export`: render a doc to PDF/DOCX/HTML/etc."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    fmt = getattr(args, "format", None)
    out = getattr(args, "out", None)

    if not fmt:
        ext = os.path.splitext(out or "")[1].lower()
        fmt = _EXT_TO_FORMAT.get(ext)
        if not fmt:
            raise GdocError(
                "cannot infer format; pass --format or use a known "
                "--out extension (" + ", ".join(sorted(_EXPORT_MIME)) + ")",
                exit_code=3,
            )
    if fmt in _BINARY_FORMATS and not out:
        raise GdocError(
            f"--out is required for binary formats ({fmt})", exit_code=3,
        )
    tab_name = getattr(args, "tab", None)
    if tab_name and fmt != "md":
        raise GdocError(
            f"--tab applies only to Markdown export; {fmt} covers every tab",
            exit_code=3,
        )

    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)

    from gdoc.api.drive import export_doc_bytes

    document = selected = None
    if fmt == "md":
        from gdoc.frontmatter import protect_body

        markdown, document, selected = _read_native_tab(doc_id, tab_name)
        content = protect_body(markdown).encode("utf-8")
        _note_tab_scope(document, selected)
        omitted = _read_omissions([selected])
        _note_omissions(omitted)
    else:
        content = export_doc_bytes(doc_id, _EXPORT_MIME[fmt])
    tab_scope = ({"tab": selected["title"], "tab_id": selected["id"]}
                 if selected is not None else {})
    if selected is not None:
        # Same coverage fields as pull: JSON callers see an incomplete read.
        tab_scope["complete"] = _preview_complete([selected]) and not omitted
        if omitted:
            tab_scope["omitted"] = omitted

    if out:
        try:
            with open(out, "wb") as f:
                f.write(content)
        except OSError as e:
            raise GdocError(f"cannot write {out}: {e}", exit_code=3) from e

        from gdoc.format import format_json, get_output_mode

        mode = get_output_mode(args)
        if mode == "json":
            print(format_json(path=out, format=fmt, bytes=len(content), **tab_scope))
        elif mode == "plain":
            print(f"path\t{out}")
            print(f"format\t{fmt}")
            print(f"bytes\t{len(content)}")
        elif mode == "verbose":
            print(f"Exported: {doc_id}")
            print(f"Format: {fmt}")
            print(f"Path: {out}")
            print(f"Bytes: {len(content)}")
        else:
            print(f"OK exported {out} ({fmt}, {len(content)} bytes)")
    else:
        from gdoc.format import format_json, get_output_mode

        text = content.decode("utf-8")
        if get_output_mode(args) == "json":
            print(format_json(format=fmt, bytes=len(content), content=text,
                              **tab_scope))
        else:
            # terse/plain/verbose: the document content IS the output
            sys.stdout.write(text)

    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info, command="export-content", quiet=quiet,
    )
    if document is not None and _preview_complete([selected]):
        _record_read(doc_id, [selected], document.get("revisionId", ""))
    return 0


# Formats the Docs API insertInlineImage/replaceImage requests accept.
# Deliberately narrower than markdown import (`new --file` also takes
# WebP): the API rejects WebP, and failing fast here avoids creating a
# public-read temp file that the API is guaranteed to refuse
# (live-verified: 400 "should be ... in supported formats").
_INSERT_IMAGE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}


def _validate_image_source(image: str) -> str | None:
    """Validate an image argument (URL or local path).

    Returns the local file's MIME type, or None for a remote URL.
    Raises GdocError (exit 3) for a missing file or unsupported type,
    so handlers can fail fast before making API calls.
    """
    if image.startswith(("http://", "https://")):
        return None

    if not os.path.isfile(image):
        raise GdocError(f"image file not found: {image}", exit_code=3)

    ext = os.path.splitext(image)[1].lower()
    mime = _INSERT_IMAGE_MIMES.get(ext)
    if not mime:
        raise GdocError(
            f"unsupported image type: {ext or image} "
            "(the Docs API accepts png, jpg, gif)",
            exit_code=3,
        )
    return mime


def _resolve_image_source(image: str) -> tuple[str, str | None]:
    """Turn an image argument (URL or local path) into an insertable URI.

    Local files are uploaded to Drive as a temporary public-read file
    (Google's servers must be able to fetch the URI); the caller must
    delete the returned temp file ID when done.

    Returns (uri, temp_file_id) — temp_file_id is None for remote URLs.
    """
    mime = _validate_image_source(image)
    if mime is None:
        return image, None

    from gdoc.api.drive import upload_temp_image

    result = upload_temp_image(image, mime)
    uri = result.get("webContentLink")
    if not uri:
        # The temp file is already public-read; don't leak it just
        # because Drive omitted the download link from the response.
        _cleanup_temp_image(result.get("id"))
        raise GdocError(
            "Drive returned no download link for the temporary image "
            "upload; try again"
        )
    return uri, result["id"]


def _cleanup_temp_image(temp_file_id: str | None) -> None:
    """Delete a temp Drive image, warning loudly if the delete fails.

    The temp file is public-read (that's how Docs fetches it), so a failed
    cleanup is an exposure the user must know about, not a silent leak.
    """
    if not temp_file_id:
        return
    from gdoc.api.drive import delete_file

    try:
        delete_file(temp_file_id)
    except Exception as e:
        print(
            f"WARN: could not delete temporary Drive file {temp_file_id} "
            f"(public-read); delete it manually: {e}",
            file=sys.stderr,
        )


def _resolve_image_target_tab(doc: dict, tab_name: str | None) -> dict | None:
    """Pick the tab an image operation targets; None = legacy body.

    A multi-tab document with no --tab is ambiguous — refuse rather than
    guess, since an insert lands at a raw index.
    """
    from gdoc.api.docs import flatten_tabs, resolve_tab

    tabs = flatten_tabs(doc.get("tabs", []))
    if tab_name:
        return resolve_tab(tabs, tab_name)
    if len(tabs) > 1:
        raise GdocError(
            f"document has {len(tabs)} tabs; specify --tab", exit_code=3,
        )
    return tabs[0] if tabs else None


def _resolve_insert_index(
    body: dict, index: int | None, after: str | None,
) -> int:
    """Resolve the UTF-16 insertion index from --index, --after, or --end."""
    from gdoc.api.docs import find_text_in_document

    if index is not None:
        if index < 1:
            raise GdocError("--index must be >= 1", exit_code=3)
        return index
    if after is not None:
        matches = find_text_in_document(
            None, after, body=body, allow_native_gaps=True,
        )
        if not matches:
            matches = find_text_in_document(
                None, after, body=body, normalize=True,
                allow_native_gaps=True,
            )
        if not matches:
            from gdoc.api.docs import diagnose_no_match

            # normalize=True was already retried above, so don't let the
            # diagnosis suggest it.
            reason = diagnose_no_match(
                None, after, body=body, already_normalized=True,
            )
            msg = f"--after anchor not found: {after!r}"
            if reason:
                msg += f"; {reason}"
            raise GdocError(msg, exit_code=3)
        if len(matches) > 1:
            raise GdocError(
                f"--after anchor is ambiguous ({len(matches)} matches); "
                "use a longer anchor",
                exit_code=3,
            )
        return matches[0]["endIndex"]
    # --end: last index inside the body's final structural element
    # (the segment's closing newline — inserting there appends).
    content = body.get("content", [])
    return content[-1].get("endIndex", 2) - 1 if content else 1


def _record_targeted_write(doc_id, input_revision_id, acknowledged_revision_id):
    _record_acknowledged_write(
        doc_id, input_revision_id=input_revision_id,
        acknowledged_revision_id=acknowledged_revision_id,
    )


def _update_state_after_write(doc_id, change_info, **details):
    """Update awareness state after a write Google acknowledged.

    A local failure leaves the read baseline stale, which only asks for a
    fresh read; it must not report the saved write as failed.
    """
    from gdoc.state import update_state_after_command

    try:
        update_state_after_command(doc_id, change_info, **details)
    except OSError as error:
        print(f"WARN: write saved, but local state could not be updated: {error}",
              file=sys.stderr)


def _record_acknowledged_write(doc_id, **provenance):
    """Record a write Google acknowledged; a local failure must not hide it."""
    from gdoc.state import record_content_write

    try:
        record_content_write(doc_id, **provenance)
    except OSError as error:
        print("WARN: write saved, but content provenance could not be recorded: "
              f"{error}",
              file=sys.stderr)
    if provenance.get("rebased"):
        print("WARN: another edit landed during this write, and its later "
              "stages were rebased onto it. Read the tab again (`cat` or "
              "`pull`) before the next write.", file=sys.stderr)


def cmd_insert_image(args) -> int:
    """Handler for `gdoc insert-image`: add an image to an existing doc."""
    import math

    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    for name in ("width", "height"):
        val = getattr(args, name, None)
        if val is not None and (not math.isfinite(val) or val <= 0):
            raise GdocError(
                f"--{name} must be a positive number of points",
                exit_code=3,
            )
    _validate_image_source(args.image)

    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)
    if change_info and change_info.doc_edited:
        print(
            "WARN: doc changed since last interaction; checking the current target",
            file=sys.stderr,
        )

    from gdoc.api.docs import get_document_with_tabs

    doc = get_document_with_tabs(doc_id)
    revision_id = doc.get("revisionId", "")
    tab = _resolve_image_target_tab(doc, getattr(args, "tab", None))
    tab_id = tab["id"] if tab else None
    body = tab["body"] if tab else doc.get("body", {})

    insert_at = _resolve_insert_index(
        body, getattr(args, "index", None), getattr(args, "after", None),
    )

    uri, temp_file_id = _resolve_image_source(args.image)

    from gdoc.api.docs import insert_inline_image

    details = {}
    try:
        object_id = insert_inline_image(
            doc_id, uri, insert_at,
            tab_id=tab_id,
            revision_id=revision_id,
            width_pt=getattr(args, "width", None),
            height_pt=getattr(args, "height", None), result_details=details,
        )
    finally:
        _cleanup_temp_image(temp_file_id)

    _record_targeted_write(
        doc_id, revision_id, details.get("acknowledged_revision_id", ""),
    )
    from gdoc.api.drive import version_after_write

    command_version = version_after_write(doc_id)

    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(
            object_id=object_id, tab=tab_id or "", index=insert_at,
        ))
    elif mode == "plain":
        print(f"object_id\t{object_id}")
        print(f"index\t{insert_at}")
    elif mode == "verbose":
        print(f"Inserted image: {object_id}")
        print(f"Index: {insert_at}")
        if tab_id:
            print(f"Tab: {tab_id}")
    else:
        print(f"OK inserted image {object_id}")


    _update_state_after_write(
        doc_id, change_info, command="insert-image",
        quiet=quiet, command_version=command_version,
    )
    return 0


def cmd_replace_image(args) -> int:
    """Handler for `gdoc replace-image`: swap an image's content by ID."""
    doc_id = _resolve_doc_id(args.doc)
    object_id = args.object_id
    quiet = getattr(args, "quiet", False)
    _validate_image_source(args.image)

    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)
    if change_info and change_info.doc_edited:
        print(
            "WARN: doc changed since last interaction; checking the current target",
            file=sys.stderr,
        )

    from gdoc.api.docs import find_object_tab, get_document_with_tabs

    doc = get_document_with_tabs(doc_id)
    revision_id = doc.get("revisionId", "")
    tab_id = find_object_tab(doc, object_id)
    if tab_id is None and object_id not in doc.get("inlineObjects", {}):
        raise GdocError(
            f"image object not found: {object_id} (see `gdoc images`)",
            exit_code=3,
        )

    uri, temp_file_id = _resolve_image_source(args.image)

    from gdoc.api.docs import replace_image

    details = {}
    try:
        replace_image(
            doc_id, object_id, uri,
            tab_id=tab_id, revision_id=revision_id, result_details=details,
        )
    finally:
        _cleanup_temp_image(temp_file_id)

    _record_targeted_write(
        doc_id, revision_id, details.get("acknowledged_revision_id", ""),
    )
    from gdoc.api.drive import version_after_write

    command_version = version_after_write(doc_id)

    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(object_id=object_id, status="replaced"))
    elif mode == "plain":
        print(f"object_id\t{object_id}")
        print("status\treplaced")
    elif mode == "verbose":
        print(f"Replaced image: {object_id}")
        print("Method: CENTER_CROP (existing size kept)")
    else:
        print(f"OK replaced image {object_id}")


    _update_state_after_write(
        doc_id, change_info, command="replace-image",
        quiet=quiet, command_version=command_version,
    )
    return 0


def cmd_structure(args) -> int:
    """Handler for `gdoc structure`: raw document JSON for native edits."""
    import json

    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    tab_name = getattr(args, "tab", None)
    fields = getattr(args, "fields", None)
    svm = getattr(args, "suggestions_view_mode", None)
    svm = svm.upper() if svm else None
    heading = getattr(args, "heading", None)
    table_index = getattr(args, "table", None)
    if table_index is not None and table_index < 1:
        raise GdocError("--table must be positive", exit_code=3)
    if heading is not None and table_index is not None:
        raise GdocError("--heading and --table are mutually exclusive", exit_code=3)
    if fields and (heading is not None or table_index is not None):
        raise GdocError("--fields cannot be combined with --heading/--table", 3)

    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)

    from gdoc.api.docs import get_document_structure

    doc = get_document_structure(
        doc_id, fields=fields, suggestions_view_mode=svm,
    )
    # Indexes depend on the suggestions view; when a mode was explicitly
    # requested but the response doesn't echo it, state it ourselves.
    if svm and "suggestionsViewMode" not in doc:
        doc = {**doc, "suggestionsViewMode": svm}

    from gdoc.api.docs import flatten_tabs, resolve_raw_tab, resolve_tab
    all_flat = flatten_tabs(doc.get("tabs", []))
    covered_tabs = [item["id"] for item in all_flat]
    partial = bool(fields or (svm and svm != "SUGGESTIONS_INLINE"))
    if heading is not None or table_index is not None:
        if not all_flat:
            raise GdocError("document has no readable tabs", exit_code=3)
        candidates = [resolve_tab(all_flat, tab_name)] if tab_name else all_flat
        matches = []
        if heading is not None:
            for candidate in candidates:
                for element in candidate.get("body", {}).get("content", []):
                    paragraph = element.get("paragraph", {})
                    style = paragraph.get("paragraphStyle", {}).get(
                        "namedStyleType", "",
                    )
                    text = "".join(
                        run.get("textRun", {}).get("content", "")
                        for run in paragraph.get("elements", [])
                    ).strip()
                    if style.startswith("HEADING_") and text == heading:
                        matches.append((candidate, element))
            if len(matches) > 1:
                locations = ", ".join(
                    f"{tab['title']} ({tab['id']}) at {element.get('startIndex', 0)}"
                    for tab, element in matches
                )
                raise GdocError(
                    f"heading is ambiguous: {locations}; use --tab to narrow scope", 3,
                )
            if not matches:
                raise GdocError(f"heading not found: {heading}", 3)
        else:
            candidate = candidates[0]
            tables = [
                element for element in candidate.get("body", {}).get("content", [])
                if "table" in element
            ]
            if table_index > len(tables):
                raise GdocError(f"table {table_index} not found", 3)
            matches = [(candidate, tables[table_index - 1])]
        selected, element = matches[0]
        covered_tabs = [selected["id"]]
        partial = True
        out = {
            "documentId": doc.get("documentId", doc_id),
            "revisionId": doc.get("revisionId", ""),
            "tab_id": selected["id"], "content": [element],
            "scope": {"tab_ids": covered_tabs, "complete": False},
        }
    elif tab_name:
        tab = resolve_raw_tab(doc.get("tabs", []), tab_name)
        if tab is None:
            raise GdocError(
                f"tab not found: {tab_name} "
                "(with --fields, the mask must keep tabProperties)", exit_code=3,
            )
        covered_tabs = [item["id"] for item in flatten_tabs([tab])]
        out = {
            "documentId": doc.get("documentId", doc_id),
            "title": doc.get("title", ""),
            "revisionId": doc.get("revisionId", ""), "tab": tab,
        }
        if doc.get("suggestionsViewMode"):
            out["suggestionsViewMode"] = doc["suggestionsViewMode"]
    else:
        out = doc

    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(document=out))
    elif mode == "verbose":
        print(json.dumps(out, indent=2))
    else:
        print(json.dumps(out, separators=(",", ":")))

    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info, command="structure-content", quiet=quiet,
    )
    if not partial:
        from gdoc.state import record_content_read
        record_content_read(doc_id, covered_tabs, doc.get("revisionId", ""))
    return 0


def cmd_auth(args) -> int:
    """Handler for `gdoc auth`."""
    set_default = getattr(args, "set_default", None)
    if set_default:
        from gdoc.auth import configure_default_account
        configure_default_account(set_default)
        return 0

    if getattr(args, "list", False):
        from gdoc.auth import list_accounts
        accounts = list_accounts()
        if not accounts:
            print("No accounts found. Run `gdoc auth` to authenticate.", file=sys.stderr)
            return 0
        for acct in accounts:
            print(acct)
        return 0

    remove = getattr(args, "remove", None)
    if remove:
        from gdoc.util import confirm_destructive
        confirm_destructive(
            f"remove credentials for account {remove!r}",
            force=getattr(args, "force", False),
        )
        from gdoc.auth import remove_account
        remove_account(remove)
        return 0

    from gdoc.auth import authenticate
    authenticate(
        no_browser=getattr(args, "no_browser", False),
        setup_url=getattr(args, "setup_url", None),
        domain=getattr(args, "domain", None),
    )
    return 0


def _cmd_new_from_file(args) -> int:
    """Create a doc from a local markdown file, with image support."""
    import os

    title = args.title
    file_path = args.file_path
    folder_id = None
    if getattr(args, "folder", None):
        folder_id = _resolve_doc_id(args.folder)

    if not os.path.isfile(file_path):
        raise GdocError(f"file not found: {file_path}", exit_code=3)
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        raise GdocError(f"cannot read file: {e}", exit_code=3)

    base_dir = os.path.dirname(os.path.abspath(file_path))

    # Extract images from markdown
    from gdoc.mdimport import extract_images

    try:
        cleaned, images = extract_images(content, base_dir)
    except ValueError as e:
        raise GdocError(str(e), exit_code=3)

    # Create doc from markdown content
    from gdoc.api.drive import create_doc_from_markdown

    result = create_doc_from_markdown(
        title, cleaned, folder_id=folder_id,
    )
    new_id = result["id"]
    version = result.get("version")
    url = result.get("webViewLink", "")

    # The document already exists: a failure from here on must name it, or a
    # retry would create a second document. Partial completion exits 1.
    def created_then_failed(step, error):
        return GdocError(
            f"created document {new_id} ({url or 'no URL returned'}), but "
            f"{step} failed: {error}. Inspect that document instead of "
            "rerunning `new --file`, which would create another.", exit_code=1,
        )

    if images:
        try:
            _insert_images(new_id, images)
        except Exception as error:  # noqa: BLE001 — reported with the new ID
            raise created_then_failed("image insertion", error) from error
    try:
        new_version = _apply_page_mode(args, new_id)
    except Exception as error:  # noqa: BLE001 — reported with the new ID
        raise created_then_failed("setting page mode", error) from error
    if new_version is None and images:
        # Image inserts advanced the Drive version past the create-time value
        # (a page-mode write already folds in a refresh); re-read it
        # best-effort so state isn't seeded with a stale baseline that makes
        # the next command report a spurious "doc edited" change.
        from gdoc.api.drive import get_file_version

        try:
            new_version = get_file_version(new_id).get("version")
        except Exception:
            new_version = None
    if new_version is not None:
        version = new_version

    # Output
    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(
            id=new_id,
            title=result.get("name", title),
            url=url,
        ))
    elif mode == "plain":
        print(f"id\t{new_id}")
    elif mode == "verbose":
        print(f"Created: {result.get('name', title)}")
        print(f"ID: {new_id}")
        print(f"URL: {url}")
        print(f"Images: {len(images)}")
    else:
        print(new_id)

    # Seed state
    _update_state_after_write(
        new_id, None, command="new",
        quiet=False, command_version=version,
    )
    return 0


def _insert_images(doc_id: str, images) -> None:
    """Insert images into a doc by finding placeholders."""
    from gdoc.api.docs import find_text_in_document, get_document
    from gdoc.api.drive import delete_file, upload_temp_image

    temp_file_ids: list[str] = []
    try:
        for img in reversed(images):
            document = get_document(doc_id)
            matches = find_text_in_document(
                document, img.placeholder, match_case=True,
            )
            if not matches:
                continue

            match = matches[0]

            # Resolve image URI
            if img.is_remote:
                uri = img.path
            else:
                result = upload_temp_image(
                    img.resolved_path, img.mime_type,
                )
                temp_file_ids.append(result["id"])
                uri = result["webContentLink"]

            # Delete placeholder + insert image
            from gdoc.api.docs import get_docs_service

            service = get_docs_service()
            requests = [
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": match["startIndex"],
                            "endIndex": match["endIndex"],
                        }
                    }
                },
                {
                    "insertInlineImage": {
                        "location": {
                            "index": match["startIndex"],
                        },
                        "uri": uri,
                    }
                },
            ]
            service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": requests},
            ).execute()
    finally:
        # Cleanup temp files
        for fid in temp_file_ids:
            try:
                delete_file(fid)
            except Exception:
                pass


def cmd_config(args) -> int:
    """Handler for `gdoc config`."""
    from gdoc.format import format_json, get_output_mode
    from gdoc.util import get_default_page_mode, set_default_page_mode

    mode = get_output_mode(args)
    page_mode = getattr(args, "page_mode", None)
    if page_mode:
        set_default_page_mode(page_mode)
        # Human confirmation to stderr; the value itself is echoed to stdout
        # below (same as the GET path) so a script can capture what it set.
        print(f"OK page_mode set to: {page_mode}", file=sys.stderr)
        current = page_mode
    else:
        current = get_default_page_mode()

    if mode == "json":
        print(format_json(page_mode=current))
    else:
        # None = unset; the doc's mode is left to the create path.
        print(f"page_mode\t{current or 'unset'}")
    return 0


def _apply_page_mode(args, doc_id: str) -> int | None:
    """Set the page mode on a freshly created doc (best-effort).

    Resolution order: --pageless/--paged flag, then the configured default
    (`gdoc config --page-mode`). With no flag and no configured default the
    doc is left exactly as the create path produced it — blank docs inherit
    the account's page-mode default, markdown imports stay paged — and no API
    call is made.

    Returns the doc's post-update Drive version when a mode was actually
    written (the write advances the version, so the caller must refresh its
    state baseline or the next command reports a spurious "doc edited"
    change), else None. A failure here is non-fatal — the doc already
    exists — so it warns and returns None.
    """
    if getattr(args, "pageless", False):
        pageless = True
    elif getattr(args, "paged", False):
        pageless = False
    else:
        from gdoc.util import get_default_page_mode

        mode = get_default_page_mode()
        if mode is None:
            return None  # no explicit preference — leave the doc untouched
        pageless = mode == "pageless"

    from gdoc.api.docs import set_page_mode

    try:
        set_page_mode(doc_id, pageless)
    except Exception as e:  # best-effort: the doc is already created
        print(f"WARN: could not set page mode: {e}", file=sys.stderr)
        return None

    # The updateDocumentStyle write bumped the Drive version; re-read it so the
    # caller seeds state with the post-write baseline. Best-effort: a failed
    # refresh just falls back to the create-time version (a stale banner is
    # better than aborting after the doc exists).
    from gdoc.api.drive import get_file_version

    try:
        return get_file_version(doc_id).get("version")
    except Exception:
        return None


def cmd_new(args) -> int:
    """Handler for `gdoc new`."""
    if getattr(args, "file_path", None):
        return _cmd_new_from_file(args)

    title = args.title
    folder_id = None
    if getattr(args, "folder", None):
        folder_id = _resolve_doc_id(args.folder)

    from gdoc.api.drive import create_doc

    result = create_doc(title, folder_id=folder_id)
    new_id = result["id"]
    version = result.get("version")
    url = result.get("webViewLink", "")

    new_version = _apply_page_mode(args, new_id)
    if new_version is not None:
        version = new_version

    from gdoc.format import get_output_mode, format_json

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=new_id, title=result.get("name", title), url=url))
    elif mode == "plain":
        print(f"id\t{new_id}")
    elif mode == "verbose":
        print(f"Created: {result.get('name', title)}")
        print(f"ID: {new_id}")
        print(f"URL: {url}")
    else:
        print(new_id)

    # Seed state for the new doc

    _update_state_after_write(
        new_id, None, command="new",
        quiet=False, command_version=version,
    )

    return 0


def cmd_cp(args) -> int:
    """Handler for `gdoc cp`."""
    doc_id = _resolve_doc_id(args.doc)
    title = args.title
    quiet = getattr(args, "quiet", False)

    # Pre-flight on the source doc
    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.drive import copy_doc

    result = copy_doc(doc_id, title)
    new_id = result["id"]
    version = result.get("version")
    url = result.get("webViewLink", "")

    from gdoc.format import get_output_mode, format_json

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=new_id, title=result.get("name", title), url=url))
    elif mode == "plain":
        print(f"id\t{new_id}")
    elif mode == "verbose":
        print(f"Copied: {result.get('name', title)}")
        print(f"ID: {new_id}")
        print(f"URL: {url}")
    else:
        print(new_id)

    # Update state for the source doc

    _update_state_after_write(
        doc_id, change_info, command="cp", quiet=quiet,
    )

    # Seed state for the new copy
    _update_state_after_write(
        new_id, None, command="cp",
        quiet=False, command_version=version,
    )

    return 0


def cmd_share(args) -> int:
    """Handler for `gdoc share`."""
    doc_id = _resolve_doc_id(args.doc)
    email = getattr(args, "email", None)
    domain = getattr(args, "domain", None)
    anyone = getattr(args, "anyone", False)
    role = getattr(args, "role", "reader")
    discoverable = getattr(args, "discoverable", False)
    quiet = getattr(args, "quiet", False)

    targets = sum(1 for t in (email, domain, anyone) if t)
    if targets != 1:
        raise GdocError(
            "provide exactly one share target: EMAIL, --domain, or --anyone",
            exit_code=3,
        )
    if discoverable and email:
        raise GdocError(
            "--discoverable applies only to --domain/--anyone shares",
            exit_code=3,
        )

    # Pre-flight awareness check
    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.drive import create_permission

    create_permission(
        doc_id, email=email, role=role,
        domain=domain, anyone=anyone, discoverable=discoverable,
    )

    if email:
        share_type, target = "user", email
    elif domain:
        share_type, target = "domain", domain
    else:
        share_type, target = "anyone", "anyone with the link"

    from gdoc.format import get_output_mode, format_json

    mode = get_output_mode(args)
    if mode == "json":
        # User shares keep the exact pre-0.16 schema; domain/anyone
        # shares report target/type/discoverable instead.
        if email:
            print(format_json(email=email, role=role, status="shared"))
        else:
            print(format_json(
                type=share_type, target=target, role=role,
                status="shared", discoverable=discoverable,
            ))
    elif mode == "plain":
        if email:
            print(f"email\t{email}")
            print(f"role\t{role}")
        else:
            print(f"target\t{target}")
            print(f"type\t{share_type}")
            print(f"role\t{role}")
            print(f"discoverable\t{'true' if discoverable else 'false'}")
    else:
        suffix = " (discoverable)" if discoverable else ""
        print(f"OK shared with {target} as {role}{suffix}")

    # Update state for the doc

    _update_state_after_write(doc_id, change_info, command="share", quiet=quiet)

    return 0


def cmd_mkdir(args) -> int:
    """Handler for `gdoc mkdir`: create a Drive folder."""
    parent_id = None
    if getattr(args, "parent", None):
        parent_id = _resolve_doc_id(args.parent)

    from gdoc.api.drive import create_folder

    result = create_folder(args.title, parent_id=parent_id)
    folder_id = result["id"]
    url = result.get("webViewLink", "")

    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(
            id=folder_id, name=result.get("name", args.title), url=url,
        ))
    elif mode == "plain":
        print(f"id\t{folder_id}")
    elif mode == "verbose":
        print(f"Created folder: {result.get('name', args.title)}")
        print(f"ID: {folder_id}")
        print(f"URL: {url}")
    else:
        print(folder_id)
    return 0


def cmd_mv(args) -> int:
    """Handler for `gdoc mv`: move a file into a folder."""
    doc_id = _resolve_doc_id(args.doc)
    folder_id = _resolve_doc_id(args.folder)
    quiet = getattr(args, "quiet", False)

    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.drive import move_file

    result = move_file(doc_id, folder_id)
    parents = result.get("parents", [])

    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(
            id=doc_id, name=result.get("name", ""), parents=parents,
        ))
    elif mode == "plain":
        print(f"id\t{doc_id}")
        print(f"parents\t{','.join(parents)}")
    elif mode == "verbose":
        print(f"Moved: {result.get('name', doc_id)}")
        print(f"To: {','.join(parents)}")
    else:
        print(f"OK moved to {','.join(parents) or folder_id}")


    _update_state_after_write(
        doc_id, change_info, command="mv",
        quiet=quiet, command_version=result.get("version"),
        metadata_only_write=True,
    )
    return 0


def cmd_rename(args) -> int:
    """Handler for `gdoc rename`: retitle a file."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)

    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.drive import rename_file

    result = rename_file(doc_id, args.title)
    name = result.get("name", args.title)

    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=doc_id, name=name))
    elif mode == "plain":
        print(f"id\t{doc_id}")
        print(f"name\t{name}")
    elif mode == "verbose":
        print(f"Renamed: {doc_id}")
        print(f"Name: {name}")
    else:
        print(f"OK renamed to {name}")


    _update_state_after_write(
        doc_id, change_info, command="rename",
        quiet=quiet, command_version=result.get("version"),
        metadata_only_write=True,
    )
    return 0


def cmd_drives(args) -> int:
    """Handler for `gdoc drives`: list shared drives."""
    from gdoc.api.drive import list_shared_drives
    from gdoc.format import format_json, get_output_mode

    drives = list_shared_drives()
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(drives=drives))
    elif mode == "plain":
        for d in drives:
            print(f"{d.get('id', '')}\t{d.get('name', '')}")
    elif not drives:
        print("No shared drives.")
    else:
        for d in drives:
            print(f"{d.get('id', '')}  {d.get('name', '')}")
    return 0


def cmd_update(args) -> int:
    """Handler for `gdoc update`."""
    from gdoc.update import run_update
    return run_update()


def cmd_mcp(args) -> int:
    """Handler for `gdoc mcp`: serve gdoc over MCP on stdio."""
    from gdoc.mcp import MCPServer

    allow = None
    if getattr(args, "allow", None):
        allow = {c.strip().lower() for c in args.allow.split(",") if c.strip()}

    server = MCPServer(
        read_only=getattr(args, "read_only", False),
        allow=allow,
        account=getattr(args, "account", None),
    )
    return server.serve()


def build_parser() -> GdocArgumentParser:
    """Build the CLI argument parser with all subcommands."""
    parser = GdocArgumentParser(
        prog="gdoc",
        description="CLI for Google Docs & Drive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"gdoc {__version__}",
    )

    # Global output mode flags via a parent parser so they work
    # both before and after the subcommand name.
    output_parent = argparse.ArgumentParser(add_help=False)
    output_group = output_parent.add_mutually_exclusive_group()
    output_group.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="JSON output",
    )
    output_group.add_argument(
        "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="Detailed output",
    )
    output_group.add_argument(
        "--plain", action="store_true", default=argparse.SUPPRESS,
        help="Stable TSV output",
    )
    output_parent.add_argument(
        "--account",
        default=os.environ.get("GDOC_ACCOUNT"),
        help="Google account name for multi-account support (e.g. work, personal, or an email)",
    )

    # Also add to the top-level parser for `gdoc --json <cmd>` form
    top_output_group = parser.add_mutually_exclusive_group()
    top_output_group.add_argument("--json", action="store_true", help="JSON output")
    top_output_group.add_argument(
        "--verbose", action="store_true", help="Detailed output"
    )
    top_output_group.add_argument(
        "--plain", action="store_true", help="Stable TSV output"
    )

    parser.add_argument(
        "--allow-commands",
        default=os.environ.get("GDOC_ALLOW_COMMANDS", ""),
        help="Comma-separated list of allowed subcommands",
    )

    sub = parser.add_subparsers(dest="command")

    # update
    update_p = sub.add_parser("update", help="Update gdoc to the latest version")
    update_p.set_defaults(func=cmd_update)

    # mcp
    mcp_p = sub.add_parser(
        "mcp",
        help="Serve gdoc to desktop chat clients over MCP (stdio)",
        description=(
            "Run gdoc as a Model Context Protocol server on stdin/stdout, so "
            "clients that launch a local stdio server — Claude Desktop, the "
            "Codex CLI, and others — can read and edit Google Docs "
            "without shell access. Authenticate first with `gdoc auth`; the "
            "server cannot open a browser for the OAuth flow."
        ),
    )
    mcp_p.add_argument(
        "--read-only",
        action="store_true",
        help="Expose only commands that cannot modify Docs or Drive",
    )
    mcp_p.add_argument(
        "--allow",
        metavar="COMMANDS",
        help="Comma-separated subcommands to expose (default: all supported)",
    )
    mcp_p.add_argument(
        "--account",
        default=os.environ.get("GDOC_ACCOUNT"),
        help="Google account to use for every tool call",
    )
    mcp_p.set_defaults(func=cmd_mcp)

    # auth
    auth_p = sub.add_parser("auth", parents=[output_parent], help="Authenticate with Google")
    auth_p.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open browser, print URL for manual auth",
    )
    auth_action = auth_p.add_mutually_exclusive_group()
    auth_action.add_argument(
        "--list",
        action="store_true",
        help="List all authenticated accounts",
    )
    auth_action.add_argument(
        "--remove",
        metavar="ACCOUNT",
        help="Remove credentials for a named account",
    )
    auth_action.add_argument(
        "--set-default",
        metavar="ACCOUNT",
        help="Use an authenticated named account when --account is omitted",
    )
    auth_p.add_argument(
        "--force", "-y",
        action="store_true",
        help="Skip confirmation for --remove",
    )
    auth_p.add_argument(
        "--setup-url",
        metavar="URL",
        help="Fetch your org's OAuth client file from URL before authenticating",
    )
    auth_p.add_argument(
        "--domain",
        metavar="DOMAIN",
        help="Workspace domain hint for the Google account chooser (e.g. company.com)",
    )
    auth_p.set_defaults(func=cmd_auth)

    # config
    config_p = sub.add_parser(
        "config", parents=[output_parent],
        help="Get or set gdoc configuration (applies to all accounts)",
    )
    config_p.add_argument(
        "--page-mode", choices=["pageless", "paged"],
        help="Default page mode for docs created by `gdoc new` "
        "(unset = inherit the account default; markdown imports stay paged; "
        "applies to all accounts)",
    )
    config_p.set_defaults(func=cmd_config)

    # ls
    ls_p = sub.add_parser("ls", parents=[output_parent], help="List files in Drive")
    ls_p.add_argument("folder_id", nargs="?", help="Folder ID to list")
    ls_p.add_argument(
        "--type",
        choices=["docs", "sheets", "all"],
        default="all",
        help="File type filter",
    )
    ls_p.set_defaults(func=cmd_ls)

    # find
    find_p = sub.add_parser("find", parents=[output_parent], help="Search files by name/content")
    find_p.add_argument("query", help="Search query")
    find_p.add_argument("--title", action="store_true", help="Search title only")
    find_p.add_argument(
        "--raw", action="store_true",
        help="Treat QUERY as a raw Drive query, e.g. "
        "\"mimeType='application/vnd.google-apps.document' and "
        "'me' in owners\" (searches all drives, including shared "
        "drives you're a member of)",
    )
    find_p.set_defaults(func=cmd_find)

    # cat
    cat_p = sub.add_parser(
        "cat", parents=[output_parent],
        help="Export doc as markdown (spreadsheets print as a table)",
    )
    cat_p.add_argument("doc", help="Document ID or URL")
    cat_p.add_argument(
        "--comments", action="store_true", help="Include comment annotations"
    )
    cat_p.add_argument(
        "--all", action="store_true",
        help="Include resolved comments (with --comments)",
    )
    cat_tab_group = cat_p.add_mutually_exclusive_group()
    cat_tab_group.add_argument(
        "--tab", help="Read a specific tab by title or ID"
    )
    cat_tab_group.add_argument(
        "--all-tabs", action="store_true", help="Read all tabs"
    )
    cat_p.add_argument(
        "--range",
        help="A1 range to read, e.g. B2:D10 (spreadsheets only)",
    )
    cat_p.add_argument(
        "--max-bytes", type=int, default=0,
        help="Truncate output at N bytes (0 = unlimited)",
    )
    cat_p.add_argument(
        "--no-images", action="store_true",
        help="Strip image references from output",
    )
    cat_p.add_argument(
        "--revision", metavar="REV",
        help="Export a past revision (id, latest, head, prev, head~N, "
             "or @ISO; see `gdoc revisions`)",
    )
    cat_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    cat_p.set_defaults(func=cmd_cat)

    # revisions
    revisions_p = sub.add_parser(
        "revisions", parents=[output_parent], aliases=["history"],
        help="List retained revisions (milestones) of a doc",
        description=(
            "List the milestone revisions the Drive API retains for a "
            "document, oldest first. Revision ids are sparse, and "
            "non-pinned revisions are pruned by Google over time. "
            "Revision ids feed `cat/pull --revision` and `diff --rev`."
        ),
    )
    revisions_p.add_argument("doc", help="Document ID or URL")
    revisions_p.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="Show only the N most recent revisions",
    )
    revisions_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    revisions_p.set_defaults(func=cmd_revisions)

    # tabs
    tabs_p = sub.add_parser(
        "tabs", parents=[output_parent],
        help="List tabs in a doc (or worksheets in a spreadsheet)",
    )
    tabs_p.add_argument("doc", help="Document ID or URL")
    tabs_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    tabs_p.set_defaults(func=cmd_tabs)

    # cells
    cells_p = sub.add_parser(
        "cells", parents=[output_parent],
        help="Write values into a spreadsheet range",
    )
    cells_p.add_argument("doc", help="Spreadsheet ID or URL")
    cells_p.add_argument(
        "range",
        help="A1 range to write, e.g. B2 or 'Sheet1'!B2:C4",
    )
    cells_values_group = cells_p.add_mutually_exclusive_group()
    cells_values_group.add_argument(
        "-v", "--value", action="append",
        help="Cell value; repeat for multiple cells in one row",
    )
    cells_values_group.add_argument(
        "--file", help="Read rows from a local file (.csv, or TSV otherwise)"
    )
    cells_values_group.add_argument(
        "--stdin", action="store_true", help="Read TSV rows from stdin"
    )
    cells_p.add_argument(
        "--append", action="store_true",
        help="Append rows after the table containing the range",
    )
    cells_p.add_argument(
        "--user-entered", action="store_true",
        help="Parse values as if typed in the UI (formulas, numbers, dates)",
    )
    cells_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    cells_p.set_defaults(func=cmd_cells)

    # toc
    toc_p = sub.add_parser(
        "toc", parents=[output_parent],
        help="Extract table of contents with deep links",
    )
    toc_p.add_argument("doc", help="Document ID or URL")
    toc_p.add_argument("--tab", help="Read a specific tab by title or ID")
    toc_p.add_argument(
        "--max-depth", type=int, default=0,
        help="Only show headings up to level N (0 = all)",
    )
    toc_p.add_argument(
        "--no-links", action="store_true",
        help="Plain text outline without links",
    )
    toc_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks",
    )
    toc_p.set_defaults(func=cmd_toc)

    # add-tab
    add_tab_p = sub.add_parser(
        "add-tab", parents=[output_parent],
        help="Add a new tab to a document",
    )
    add_tab_p.add_argument("doc", help="Document ID or URL")
    add_tab_p.add_argument("title", help="Title for the new tab")
    add_tab_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks",
    )
    add_tab_p.set_defaults(func=cmd_add_tab)

    # edit
    edit_p = sub.add_parser(
        "edit", parents=[output_parent], help="Find and replace text",
        epilog="Edit matches raw document text (`gdoc cat --plain DOC`). "
               "One trailing newline is ignored in positional, stdin and file "
               "inputs. Wording edits preserve native paragraph marks, named "
               "styles and lists; multiline edits must keep the paragraph count. "
               "Empty replacement removes complete matched paragraphs, retaining "
               "the mandatory final newline. "
               "Partial-paragraph replacements support inline markdown formatting only "
               "(bold, italic, strikethrough, links and CommonMark code spans); "
               "block markers are literal. A complete paragraph can explicitly "
               "change its heading, list or quote style. Use --cell for whole-cell "
               "replacement; a cell holds text, not a nested table. Plain prose "
               "replacing cell list items "
               "removes their bullets and sets NORMAL_TEXT; an empty replacement "
               "leaves one NORMAL_TEXT paragraph. Markdown list markers request "
               "a list. "
               "Plain prose in non-list cells preserves paragraph styles.",
    )
    edit_p.add_argument("doc", help="Document ID or URL")
    edit_p.add_argument("old_text", nargs="?", default=None, help="Text to find")
    edit_p.add_argument("new_text", nargs="?", default=None, help="Replacement text")
    edit_p.add_argument("--old-file", help="Read old text from file")
    edit_p.add_argument("--new-file", help="Read new text from file")
    edit_p.add_argument(
        "--all", action="store_true",
        help="Replace all occurrences in every tab, or only --tab when supplied"
    )
    edit_p.add_argument(
        "--case-sensitive", action="store_true", help="Case-sensitive matching"
    )
    edit_p.add_argument(
        "--normalize", action="store_true",
        help="Match through smart-quote/dash differences (\u2019 matches ')",
    )
    edit_p.add_argument(
        "--cell",
        help="Target a table cell instead of searching text: a first-column label "
             "(replaces the cell to its right) or 'ROW,COL' coordinates; "
             "labels repeated in other rows or value columns are refused",
    )
    edit_p.add_argument(
        "--col", type=int,
        help="With --cell label, the 0-based column to replace "
             "(default: the column right of the label)",
    )
    edit_p.add_argument(
        "--table", type=int, default=None,
        help="Which table in the body to address with --cell (0-based). "
             "Coordinates default to the first table; a label searches all "
             "tables unless this is set.",
    )
    edit_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    edit_p.add_argument(
        "--tab", help="Limit search to this tab title or ID; search every tab "
        "otherwise and "
        "require --all for multiple matches"
    )
    edit_p.set_defaults(func=cmd_edit)

    # suggest
    suggest_p = sub.add_parser(
        "suggest", parents=[output_parent],
        help="Find and replace text as a suggested edit (review, not commit)",
        epilog="Like `edit`, but the change is made in suggest mode (Docs API "
               "Developer Preview): the original text stays until a reviewer "
               "accepts it. Replacement text supports inline markdown only "
               "(bold, italic, strikethrough, code, links). Inside a "
               "paragraph, block markers are literal text; a replacement that "
               "covers complete paragraphs rejects headings, lists, and "
               "tables. Needs comment or edit access on the "
               "doc and an OAuth client from a preview-enrolled Cloud "
               "project; never falls back to a direct edit.",
    )
    suggest_p.add_argument("doc", help="Document ID or URL")
    suggest_p.add_argument(
        "old_text", nargs="?", default=None, help="Text to find",
    )
    suggest_p.add_argument(
        "new_text", nargs="?", default=None, help="Replacement text",
    )
    suggest_p.add_argument("--old-file", help="Read old text from file")
    suggest_p.add_argument("--new-file", help="Read new text from file")
    suggest_p.add_argument(
        "--all", action="store_true",
        help="Suggest for all occurrences in every tab, or only --tab when supplied"
    )
    suggest_p.add_argument(
        "--case-sensitive", action="store_true", help="Case-sensitive matching"
    )
    suggest_p.add_argument(
        "--normalize", action="store_true",
        help="Match through smart-quote/dash differences (\u2019 matches ')",
    )
    suggest_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    suggest_p.add_argument(
        "--tab", help="Limit search to this tab title or ID; search every tab "
        "otherwise and "
        "require --all for multiple matches"
    )
    suggest_p.set_defaults(func=cmd_suggest)

    # diff
    diff_p = sub.add_parser(
        "diff", parents=[output_parent],
        help="Compare doc with a local file, or between revisions",
        description=(
            "With FILE, compare the current document against a local "
            "file (unified diff). With --rev or --since, compare two "
            "retained revisions with a readable coalesced word-diff. "
            "REV selectors: a revision id, latest/head, prev, head~N "
            "(by list position), or @ISO (last revision at/before the "
            "timestamp)."
        ),
    )
    diff_p.add_argument("doc", help="Document ID or URL")
    diff_p.add_argument(
        "file", nargs="?",
        help="Local file to compare against (current doc vs file)",
    )
    diff_p.add_argument(
        "--tab",
        help="File diffs: compare this tab title or ID (default: the file's "
             "pulled tab, else the first tab)",
    )
    diff_p.add_argument(
        "--rev", metavar="REV[..REV]",
        help="Diff revisions: A..B compares two; a single selector "
             "compares it against the latest",
    )
    diff_p.add_argument(
        "--since", metavar="ISO",
        help="Diff the last revision at/before this timestamp against "
             "the latest (what changed since I last read it)",
    )
    diff_p.add_argument(
        "--format",
        choices=["auto", "color", "plain", "json", "html"],
        default="auto",
        help="Revision-diff renderer (default: color on a TTY, else "
             "plain; html writes a styled artifact)",
    )
    diff_p.add_argument(
        "--out", metavar="PATH",
        help="Output path for --format html (default: gdoc-diff.html)",
    )
    diff_p.add_argument(
        "--with-comments", action="store_true",
        help="Anchor the doc's comment threads into html/json "
             "revision diffs",
    )
    diff_p.add_argument(
        "--min-common", type=int, default=DEFAULT_MIN_COMMON, metavar="N",
        help="Coalescing threshold for word-diff chunks "
             f"(higher = chunkier; default {DEFAULT_MIN_COMMON})",
    )
    diff_p.add_argument(
        "--context", type=int, default=DEFAULT_CONTEXT, metavar="N",
        help="Unchanged blocks kept around each change "
             f"(default {DEFAULT_CONTEXT})",
    )
    diff_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    diff_p.set_defaults(func=cmd_diff)

    # write
    write_p = sub.add_parser(
        "write", parents=[output_parent],
        help="Overwrite doc (or one tab) from local file",
        description=(
            "Replace one tab's body from a markdown file. Without --tab, "
            "the first tab is replaced and sibling tabs survive; "
            "--force-collapse-tabs explicitly removes siblings. Use "
            "`gdoc insert` to add content to an existing tab. YAML "
            "frontmatter in the input is stripped automatically; a pulled "
            "file's `tab` selects its tab. Input uses the format `cat` "
            "prints: each line is one paragraph and each blank line is an "
            "empty paragraph, so write paragraphs as single lines."
        ),
    )
    write_p.add_argument("doc", help="Document ID or URL")
    write_p.add_argument("file", help="Local markdown file")
    write_p.add_argument(
        "--tab",
        help="Replace only this tab (by title or ID); leaves siblings alone",
    )
    write_p.add_argument(
        "--force-collapse-tabs", action="store_true",
        help="Confirm you intend to collapse a multi-tab doc into one tab",
    )
    write_p.add_argument(
        "--allow-lossy", action="store_true",
        help="Knowingly discard native/rich content Markdown cannot preserve; "
             "does not bypass conflicts or permit tab collapse",
    )
    write_p.add_argument(
        "--force", action="store_true", help="Bypass write conflicts only"
    )
    write_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    write_p.set_defaults(func=cmd_write)

    # insert
    insert_p = sub.add_parser(
        "insert", parents=[output_parent],
        help="Insert local markdown into an existing tab",
        description=(
            "Insert the contents of a markdown file into a specific tab "
            "without touching any other tab. Frontmatter is stripped "
            "before upload. As with `write`, each line is one paragraph "
            "and each blank line is an empty paragraph."
        ),
    )
    insert_p.add_argument("doc", help="Document ID or URL")
    insert_p.add_argument("file", help="Local markdown file")
    insert_p.add_argument(
        "--tab", required=True,
        help="Target tab by title or ID",
    )
    insert_p.add_argument(
        "--position", choices=["start", "end"], default="start",
        help="Insert at the start (default) or end of the tab body",
    )
    insert_p.add_argument(
        "--force", action="store_true",
        help="Proceed even if the doc changed since the last read",
    )
    insert_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    insert_p.set_defaults(func=cmd_insert)

    # pull
    pull_p = sub.add_parser("pull", parents=[output_parent], help="Download doc as local markdown")
    pull_p.add_argument("doc", help="Document ID or URL")
    pull_p.add_argument("file", help="Local file to write")
    pull_p.add_argument(
        "--revision", metavar="REV",
        help="Download a past revision (id, latest, head, prev, head~N, "
             "or @ISO); the file gets `source:`/`revision:` frontmatter "
             "instead of `gdoc:` so it cannot be pushed back by accident",
    )
    pull_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    pull_p.add_argument("--tab", help="Read a selected tab (default: first tab)")
    pull_p.set_defaults(func=cmd_pull)

    # push
    push_p = sub.add_parser("push", parents=[output_parent], help="Upload local markdown to doc")
    push_p.add_argument("file", help="Local file with gdoc frontmatter")
    push_p.add_argument(
        "--allow-lossy", action="store_true",
        help="Knowingly discard native/rich content Markdown cannot preserve; "
             "does not bypass conflicts or permit tab collapse",
    )
    push_p.add_argument(
        "--force", action="store_true", help="Bypass write conflicts only"
    )
    push_p.add_argument(
        "--force-collapse-tabs", action="store_true",
        help="Confirm you intend to collapse a multi-tab doc into one tab",
    )
    push_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    push_p.set_defaults(func=cmd_push)

    # _sync-hook (hidden — no help text)
    sync_p = sub.add_parser("_sync-hook")
    sync_p.set_defaults(func=cmd_sync_hook)

    # _pull-hook (hidden — no help text)
    pull_hook_p = sub.add_parser("_pull-hook")
    pull_hook_p.set_defaults(func=cmd_pull_hook)

    # comments
    comments_p = sub.add_parser("comments", parents=[output_parent], help="List comments on a doc")
    comments_p.add_argument("doc", help="Document ID or URL")
    comments_p.add_argument(
        "--all", action="store_true", help="Include resolved comments"
    )
    comments_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    comments_p.set_defaults(func=cmd_comments)

    # comment
    comment_p = sub.add_parser("comment", parents=[output_parent], help="Add a comment to a doc")
    comment_p.add_argument("doc", help="Document ID or URL")
    comment_p.add_argument("text", help="Comment text")
    comment_p.add_argument(
        "--quote",
        help=(
            "Text to anchor uniquely across tabs and body/header/footer/footnote "
            "segments. "
            "If the Docs API preview is unavailable, creates an unanchored "
            "comment with quote metadata for cat --comments. No match or ambiguity: "
            "exit 3. One fresh attempt after a rejected revision; uncertain writes: "
            "exit 1, inspect comments before retrying"
        ),
    )
    comment_p.add_argument(
        "--tab", help="Search the quote in this tab by title or ID (default: all tabs)"
    )
    comment_p.add_argument(
        "--occurrence", type=int, metavar="N",
        help=(
            "Anchor to the Nth match of --quote (1-based; tabs in outline order, "
            "then body, headers, footers, footnotes by segment ID) when the text "
            "repeats; out of range: exit 3"
        ),
    )
    comment_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    comment_p.set_defaults(func=cmd_comment)

    # reply
    reply_p = sub.add_parser("reply", parents=[output_parent], help="Reply to a comment")
    reply_p.add_argument("doc", help="Document ID or URL")
    reply_p.add_argument("comment_id", help="Comment ID to reply to")
    reply_p.add_argument("text", help="Reply text")
    reply_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    reply_p.set_defaults(func=cmd_reply)

    # resolve
    resolve_p = sub.add_parser("resolve", parents=[output_parent], help="Resolve a comment")
    resolve_p.add_argument("doc", help="Document ID or URL")
    resolve_p.add_argument("comment_id", help="Comment ID to resolve")
    resolve_p.add_argument(
        "--message", "-m", default="", help="Message to include when resolving"
    )
    resolve_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    resolve_p.set_defaults(func=cmd_resolve)

    # reopen
    reopen_p = sub.add_parser("reopen", parents=[output_parent], help="Reopen a resolved comment")
    reopen_p.add_argument("doc", help="Document ID or URL")
    reopen_p.add_argument("comment_id", help="Comment ID to reopen")
    reopen_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    reopen_p.set_defaults(func=cmd_reopen)

    # delete-comment
    del_comment_p = sub.add_parser(
        "delete-comment", parents=[output_parent],
        help="Delete a comment",
    )
    del_comment_p.add_argument("doc", help="Document ID or URL")
    del_comment_p.add_argument("comment_id", help="Comment ID to delete")
    del_comment_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt",
    )
    del_comment_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks",
    )
    del_comment_p.set_defaults(func=cmd_delete_comment)

    # comment-info
    ci_p = sub.add_parser(
        "comment-info", parents=[output_parent],
        help="Get a single comment by ID",
    )
    ci_p.add_argument("doc", help="Document ID or URL")
    ci_p.add_argument("comment_id", help="Comment ID")
    ci_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    ci_p.set_defaults(func=cmd_comment_info)

    # images
    images_p = sub.add_parser(
        "images", parents=[output_parent],
        help="List images, charts, and drawings in a doc",
    )
    images_p.add_argument("doc", help="Document ID or URL")
    images_p.add_argument("image_id", nargs="?", help="Specific image object ID")
    images_p.add_argument(
        "--download", metavar="DIR", help="Download images to directory",
    )
    images_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks",
    )
    images_p.set_defaults(func=cmd_images)

    # export
    export_p = sub.add_parser(
        "export", parents=[output_parent],
        help="Export a doc to PDF, DOCX, HTML, and more",
        description=(
            "Export a document to a file. Markdown uses the native serializer "
            "for one tab (the first unless --tab selects another); other "
            "formats use Drive export of every tab. Binary formats "
            "(pdf, docx, "
            "odt, epub) require --out; text formats print to stdout "
            "without it."
        ),
    )
    export_p.add_argument("doc", help="Document ID or URL")
    export_p.add_argument(
        "--format", choices=sorted(_EXPORT_MIME),
        help="Export format (default: inferred from --out extension)",
    )
    export_p.add_argument(
        "--out", metavar="FILE", help="Output file path",
    )
    export_p.add_argument(
        "--tab", help="Markdown only: export this tab title or ID",
    )
    export_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    export_p.set_defaults(func=cmd_export)

    # insert-image
    ii_p = sub.add_parser(
        "insert-image", parents=[output_parent],
        help="Insert an image into an existing doc",
        description=(
            "Insert a local image file or a public image URL into a doc. "
            "Local files are uploaded to Drive as a temporary public-read "
            "file and deleted after the insert."
        ),
    )
    ii_p.add_argument("doc", help="Document ID or URL")
    ii_p.add_argument("image", help="Local image path or public image URL")
    ii_p.add_argument(
        "--tab", help="Tab title or ID (required for multi-tab docs)",
    )
    ii_where = ii_p.add_mutually_exclusive_group(required=True)
    ii_where.add_argument(
        "--after", metavar="TEXT",
        help="Insert immediately after this anchor text",
    )
    ii_where.add_argument(
        "--index", type=int, metavar="N",
        help="Insert at a raw UTF-16 document index (advanced)",
    )
    ii_where.add_argument(
        "--end", action="store_true", help="Append at the end of the tab",
    )
    ii_p.add_argument(
        "--width", type=float, metavar="PT", help="Display width in points",
    )
    ii_p.add_argument(
        "--height", type=float, metavar="PT",
        help="Display height in points",
    )
    ii_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    ii_p.set_defaults(func=cmd_insert_image)

    # replace-image
    ri_p = sub.add_parser(
        "replace-image", parents=[output_parent],
        help="Replace an existing image's content by object ID",
        description=(
            "Swap the content of an image in place (object IDs come from "
            "`gdoc images`). The image keeps its current size; the new "
            "content is scaled and center-cropped to fit."
        ),
    )
    ri_p.add_argument("doc", help="Document ID or URL")
    ri_p.add_argument("object_id", help="Image object ID (see `gdoc images`)")
    ri_p.add_argument("image", help="Local image path or public image URL")
    ri_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    ri_p.set_defaults(func=cmd_replace_image)

    # structure
    structure_p = sub.add_parser(
        "structure", parents=[output_parent],
        help="Native document JSON (structure, styles, UTF-16 ranges)",
        description=(
            "Dump the raw documents.get response — tab topology, "
            "paragraph/text styles, tables, inline objects, named "
            "ranges, headers/footers, and the UTF-16 startIndex/"
            "endIndex values native mutations need (Docs indices are "
            "UTF-16 code units, not Python character offsets; a smart "
            "chip occupies one code unit). Output is always JSON: "
            "compact by default, indented with --verbose, wrapped in "
            "the standard envelope with --json. Read-only."
        ),
    )
    structure_p.add_argument("doc", help="Document ID or URL")
    structure_p.add_argument(
        "--tab",
        help="Narrow to one tab by title or ID (returns that tab's raw "
        "subtree plus documentId/revisionId)",
    )
    structure_p.add_argument(
        "--fields",
        help="Docs API field mask passed verbatim, e.g. "
        "'revisionId,tabs(tabProperties)' (Google rejects masks that "
        "recursively expand childTabs)",
    )
    structure_p.add_argument(
        "--suggestions-view-mode",
        choices=[
            "default_for_current_access",
            "suggestions_inline",
            "preview_suggestions_accepted",
            "preview_without_suggestions",
        ],
        help="How suggestions are rendered; changes returned content "
        "and indexes (the used mode is echoed in the output)",
    )
    structure_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    structure_target = structure_p.add_mutually_exclusive_group()
    structure_target.add_argument(
        "--heading", help="Inspect one exact heading, optionally limited by --tab",
    )
    structure_target.add_argument(
        "--table", type=int, help="Inspect the 1-based table in the selected tab",
    )
    structure_p.set_defaults(func=cmd_structure)

    # info
    info_p = sub.add_parser("info", parents=[output_parent], help="Show document metadata")
    info_p.add_argument("doc", help="Document ID or URL")
    info_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    info_p.set_defaults(func=cmd_info)

    # share
    share_p = sub.add_parser("share", parents=[output_parent], help="Share a document")
    share_p.add_argument("doc", help="Document ID or URL")
    share_p.add_argument(
        "email", nargs="?",
        help="Email to share with (or use --domain / --anyone)",
    )
    share_target = share_p.add_mutually_exclusive_group()
    share_target.add_argument(
        "--domain", metavar="DOMAIN",
        help="Share with everyone in a Workspace domain (link-based)",
    )
    share_target.add_argument(
        "--anyone", action="store_true",
        help="Share with anyone who has the link",
    )
    share_p.add_argument(
        "--role",
        choices=["reader", "writer", "commenter"],
        default="reader",
        help="Permission role",
    )
    share_p.add_argument(
        "--discoverable", action="store_true",
        help="Let the file appear in search results "
        "(--domain/--anyone only; off by default)",
    )
    share_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    share_p.set_defaults(func=cmd_share)

    # mkdir
    mkdir_p = sub.add_parser(
        "mkdir", parents=[output_parent], help="Create a Drive folder",
    )
    mkdir_p.add_argument("title", help="Folder name")
    mkdir_p.add_argument(
        "--parent", metavar="FOLDER",
        help="Parent folder ID or URL (default: My Drive root)",
    )
    mkdir_p.set_defaults(func=cmd_mkdir)

    # mv
    mv_p = sub.add_parser(
        "mv", parents=[output_parent], aliases=["move"],
        help="Move a file into a folder",
    )
    mv_p.add_argument("doc", help="File ID or URL")
    mv_p.add_argument("folder", help="Destination folder ID or URL")
    mv_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    mv_p.set_defaults(func=cmd_mv)

    # rename
    rename_p = sub.add_parser(
        "rename", parents=[output_parent], help="Rename a file",
    )
    rename_p.add_argument("doc", help="File ID or URL")
    rename_p.add_argument("title", help="New title")
    rename_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    rename_p.set_defaults(func=cmd_rename)

    # drives
    drives_p = sub.add_parser(
        "drives", parents=[output_parent], help="List shared drives",
    )
    drives_p.set_defaults(func=cmd_drives)

    # new
    new_p = sub.add_parser("new", parents=[output_parent], help="Create a blank document")
    new_p.add_argument("title", help="Document title")
    new_p.add_argument("--folder", help="Folder ID to place doc in")
    new_p.add_argument(
        "--file", dest="file_path",
        help="Create doc from a local markdown file through Google's "
             "Markdown import, which reads CommonMark paragraphs (joined "
             "lines, blank-line separators) rather than gdoc's line-per-"
             "paragraph format; for that format, create the doc and `write` "
             "the file",
    )
    new_mode = new_p.add_mutually_exclusive_group()
    new_mode.add_argument(
        "--pageless", action="store_true",
        help="Create pageless (overrides the configured default)",
    )
    new_mode.add_argument(
        "--paged", action="store_true",
        help="Create paged (overrides the configured default)",
    )
    new_p.set_defaults(func=cmd_new)

    # cp
    cp_p = sub.add_parser("cp", parents=[output_parent], help="Duplicate a document")
    cp_p.add_argument("doc", help="Document ID or URL")
    cp_p.add_argument("title", help="Title for the copy")
    cp_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    cp_p.set_defaults(func=cmd_cp)

    return parser


def _is_top_level_help_invocation(argv: list[str]) -> bool:
    """True for `gdoc`, `gdoc --help`, `gdoc -h` — but not subcommand help."""
    rest = argv[1:]
    if not rest:
        return True
    return rest[0] in ("--help", "-h")


def run_argv(argv: list[str] | None = None, *, check_updates: bool = True) -> int:
    """Parse an argv list and run the matching subcommand.

    Shared by `main()` and by the MCP server (`gdoc mcp`), which dispatches
    tool calls in-process rather than shelling out to a new interpreter.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        return 3

    # Belt-and-suspenders check for mutually exclusive output modes
    output_flags = sum([
        getattr(args, "json", False),
        getattr(args, "verbose", False),
        getattr(args, "plain", False),
    ])
    if output_flags > 1:
        parser.error("--json, --verbose, and --plain are mutually exclusive")

    # Command allowlist enforcement
    allowed = getattr(args, "allow_commands", "")
    if allowed:
        allow_set = {c.strip().lower() for c in allowed.split(",") if c.strip()}
        if args.command.lower() not in allow_set:
            print(f"ERR: command not allowed: {args.command}", file=sys.stderr)
            return 3

    try:
        # Multi-account support
        account = getattr(args, "account", None)
        if account:
            from gdoc.util import set_active_account
            set_active_account(account)

        # Check for updates (skip for the update command itself and internal hooks)
        if check_updates and args.command not in (
            "update", "_sync-hook", "_pull-hook", "mcp",
        ):
            from gdoc.update import check_for_update
            check_for_update()

        # Pin one account for the whole invocation: unpinned, every service
        # access re-resolves the configured default, so a `gdoc auth
        # --set-default` from another process could hand one command's read
        # and write to different accounts. The MCP server is exempt — it
        # pins per tool call instead, so a default changed mid-serve is
        # still picked up on the next call.
        if args.command == "mcp":
            return args.func(args)
        from gdoc.util import account_context, resolve_account
        with account_context(resolve_account()):
            return args.func(args)
    except AuthError as e:
        print(f"ERR: {e}", file=sys.stderr)
        return 2
    except GdocError as e:
        print(f"ERR: {e}", file=sys.stderr)
        return e.exit_code
    except Exception as e:
        print(f"ERR: unexpected error: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """Entry point for the gdoc CLI."""
    if _is_top_level_help_invocation(sys.argv):
        from gdoc.update import auto_update_for_help
        auto_update_for_help()

    return run_argv()
