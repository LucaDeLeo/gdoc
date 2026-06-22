"""CLI parser, subcommand dispatch, and exception handler."""

import argparse
import os
import sys

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

    if (tab or all_tabs) and getattr(args, "comments", False):
        raise GdocError(
            "--tab/--all-tabs and --comments are mutually exclusive",
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
        content = _truncate_bytes(content, max_bytes)

        from gdoc.format import format_json, get_output_mode
        if get_output_mode(args) == "json":
            print(format_json(revision=rev["id"], content=content))
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

    if tab or all_tabs:
        from gdoc.api.docs import get_document_tabs, get_tab_text

        tabs = get_document_tabs(doc_id)

        if tab:
            # Match by title (case-insensitive) first, then by ID
            match = None
            for t in tabs:
                if t["title"].lower() == tab.lower():
                    match = t
                    break
            if match is None:
                for t in tabs:
                    if t["id"] == tab:
                        match = t
                        break
            if match is None:
                raise GdocError(f"tab not found: {tab}", exit_code=3)
            content = get_tab_text(match)
            if no_images:
                from gdoc.mdimport import strip_images
                content = strip_images(content)
            content = _truncate_bytes(content, max_bytes)

            from gdoc.format import format_json, get_output_mode
            mode = get_output_mode(args)
            if mode == "json":
                print(format_json(tab=match["title"], content=content))
            else:
                print(content, end="")
        else:
            # --all-tabs
            parts = []
            for t in tabs:
                parts.append(f"=== Tab: {t['title']} ===\n")
                parts.append(get_tab_text(t))
            content = "".join(parts)
            if no_images:
                from gdoc.mdimport import strip_images
                content = strip_images(content)
            content = _truncate_bytes(content, max_bytes)

            from gdoc.format import format_json, get_output_mode
            mode = get_output_mode(args)
            if mode == "json":
                print(format_json(content=content))
            else:
                print(content, end="")

        from gdoc.state import update_state_after_command
        update_state_after_command(doc_id, change_info, command="cat", quiet=quiet)
        return 0

    if getattr(args, "comments", False):
        # Annotated view: line-numbered content + inline comment annotations
        from gdoc.api.drive import export_doc
        markdown = export_doc(doc_id, mime_type="text/markdown")

        if no_images:
            from gdoc.mdimport import strip_images
            markdown = strip_images(markdown)

        from gdoc.api.comments import list_comments
        include_resolved = getattr(args, "all", False)
        comments = list_comments(
            doc_id,
            include_resolved=include_resolved,
            include_anchor=True,
        )

        from gdoc.annotate import annotate_markdown
        annotated = annotate_markdown(markdown, comments, show_resolved=include_resolved)
        annotated = _truncate_bytes(annotated, max_bytes)

        from gdoc.format import get_output_mode, format_json
        mode = get_output_mode(args)
        if mode == "json":
            print(format_json(content=annotated))
        else:
            print(annotated, end="")

        from gdoc.state import update_state_after_command
        update_state_after_command(doc_id, change_info, command="cat", quiet=quiet)

        return 0

    mime_type = "text/plain" if getattr(args, "plain", False) else "text/markdown"

    from gdoc.api.drive import export_doc

    content = export_doc(doc_id, mime_type=mime_type)
    if no_images:
        from gdoc.mdimport import strip_images
        content = strip_images(content)
    content = _truncate_bytes(content, max_bytes)

    from gdoc.format import get_output_mode, format_json

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(content=content))
    else:
        print(content, end="")

    # Update state after success
    from gdoc.state import update_state_after_command
    update_state_after_command(doc_id, change_info, command="cat", quiet=quiet)

    return 0


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

    from gdoc.api.drive import get_file_version
    command_version = get_file_version(doc_id).get("version")

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
        with open(file_path) as f:
            content = f.read()
    except OSError as e:
        raise GdocError(f"cannot read file: {e}", exit_code=3) from e

    from gdoc.frontmatter import parse_frontmatter
    _, content = parse_frontmatter(content)

    if not content.strip():
        raise GdocError("input file has no content to insert", exit_code=3)

    change_info, _ = _check_write_conflict(doc_id, quiet, force)

    from gdoc.api.docs import insert_markdown_into_tab

    result = insert_markdown_into_tab(
        doc_id, tab_name, content, position=position, replace=False,
    )

    from gdoc.api.drive import get_file_version
    version_data = get_file_version(doc_id)
    command_version = version_data.get("version")

    from gdoc.format import get_output_mode
    _print_tab_write_result(
        get_output_mode(args), doc_id, result, command_version,
        verb="inserted",
    )

    from gdoc.state import update_state_after_command
    update_state_after_command(
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
        except OSError as e:
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
    from gdoc.api.drive import get_file_version

    command_version = get_file_version(doc_id).get("version")

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
    from gdoc.api.drive import search_files
    from gdoc.format import get_output_mode

    title_only = getattr(args, "title", False)
    files = search_files(args.query, title_only=title_only)

    mode = get_output_mode(args)
    output = _format_file_list(files, mode)
    if output:
        print(output)
    elif mode not in ("json", "plain"):
        print("No files.")

    return 0


def _read_file(path: str) -> str:
    """Read file content, stripping one trailing newline."""
    import os

    if not os.path.isfile(path):
        raise GdocError(f"file not found: {path}", exit_code=3)
    try:
        with open(path) as f:
            content = f.read()
    except OSError as e:
        raise GdocError(f"cannot read file: {e}", exit_code=3)
    # Strip exactly one trailing newline (editors add one)
    if content.endswith("\n"):
        content = content[:-1]
    return content


def cmd_edit(args) -> int:
    """Handler for `gdoc edit`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    replace_all = getattr(args, "all", False)
    case_sensitive = getattr(args, "case_sensitive", False)
    normalize = getattr(args, "normalize", False)
    cell = getattr(args, "cell", None)
    col = getattr(args, "col", None)
    table_index = getattr(args, "table", None)

    # Resolve text from args or files (fail fast before API calls)
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

    # Pre-flight awareness check
    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)

    # Conflict warning (warn but don't block, per spec)
    if change_info and change_info.has_conflict:
        print("WARN: doc changed since last read", file=sys.stderr)

    # Get document structure + revision ID
    from gdoc.api.docs import find_text_in_document, get_document, replace_formatted

    tab_name = getattr(args, "tab", None)
    tab_id = None

    if tab_name:
        from gdoc.api.docs import flatten_tabs, get_document_with_tabs, resolve_tab
        doc = get_document_with_tabs(doc_id)
        revision_id = doc.get("revisionId", "")
        tabs = flatten_tabs(doc.get("tabs", []))
        tab_match = resolve_tab(tabs, tab_name)
        tab_id = tab_match["id"]
        search_body = tab_match["body"]
    else:
        document = get_document(doc_id)
        revision_id = document.get("revisionId", "")
        search_body = document.get("body", {})

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
            None, old_text, match_case=case_sensitive,
            body=search_body, normalize=normalize,
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
            raise GdocError(
                f"multiple matches ({len(matches)} found). Use --all",
                exit_code=3,
            )

    # Check if replacement contains tables — not supported with --all
    from gdoc.mdparse import parse_markdown as _parse_md
    _parsed = _parse_md(new_text)
    if _parsed.tables and len(matches) > 1:
        raise GdocError(
            "replacement with tables not supported with --all",
            exit_code=3,
        )

    # Perform formatted replacement via Docs API batchUpdate
    occurrences = replace_formatted(
        doc_id, matches, new_text, revision_id, tab_id=tab_id,
    )

    # Get post-edit version for state tracking (Decision #12)
    from gdoc.api.drive import get_file_version

    version_data = get_file_version(doc_id)
    command_version = version_data.get("version")

    # Output
    from gdoc.format import get_output_mode, format_json

    mode = get_output_mode(args)
    label = "occurrence" if occurrences == 1 else "occurrences"
    if mode == "json":
        print(format_json(replaced=occurrences))
    elif mode == "plain":
        print(f"id\t{doc_id}")
        print(f"status\tupdated")
    else:
        print(f"OK replaced {occurrences} {label}")

    # Update state
    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info, command="edit",
        quiet=quiet, command_version=command_version,
    )

    return 0


def _doc_matches(doc_id: str, body: str) -> bool:
    """True if the doc's current markdown export equals the content to write."""
    from gdoc.api.drive import export_doc

    try:
        current = export_doc(doc_id, mime_type="text/markdown")
    except GdocError:
        return False
    return current.strip() == body.strip()


def _finish_noop_write(
    doc_id: str, change_info, args, quiet: bool, command: str,
) -> int:
    """Conclude a write-like command whose content already matches the doc.

    Skips the upload, reports in-sync, and heals the read baseline so the
    next write doesn't trip conflict detection again.
    """
    from gdoc.api.drive import get_file_version
    from gdoc.format import format_json, get_output_mode
    from gdoc.state import update_state_after_command

    command_version = get_file_version(doc_id).get("version")
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(in_sync=True, version=command_version))
    elif mode == "plain":
        print(f"id\t{doc_id}")
        print("status\tin_sync")
    else:
        print("OK already in sync (doc matches local content; nothing to write)")
    update_state_after_command(
        doc_id, change_info, command=command,
        quiet=quiet, command_version=command_version,
        full_doc_write=True,
    )
    return 0


def _check_write_conflict(
    doc_id: str, quiet: bool, force: bool, body: str | None = None,
):
    """Run conflict detection for write-like commands.

    Returns (change_info, in_sync). in_sync is True when the version moved
    but the doc content already equals `body` (e.g. our own earlier write or
    a cosmetic Docs version bump) — the caller should skip the upload.
    Raises GdocError(exit_code=3) on a real conflict.
    """
    if not quiet:
        from gdoc.notify import pre_flight

        change_info = pre_flight(doc_id, quiet=False)
        _require_doc(doc_id, change_info)

        if not force:
            if change_info.last_read_version is None:
                if body is not None and _doc_matches(doc_id, body):
                    return change_info, True
                raise GdocError(
                    "no read baseline. Run 'gdoc cat' first, "
                    "or use --force to overwrite.",
                    exit_code=3,
                )
            if change_info.has_conflict:
                if body is not None and _doc_matches(doc_id, body):
                    return change_info, True
                raise GdocError(
                    "doc changed since last read. "
                    "Run 'gdoc cat' first, "
                    "or use --force to overwrite.",
                    exit_code=3,
                )
        return change_info, False

    if not force:
        from gdoc.state import load_state

        state = load_state(doc_id)

        if state is None or state.last_read_version is None:
            if body is not None and _doc_matches(doc_id, body):
                return None, True
            raise GdocError(
                "no read baseline. Run 'gdoc cat' first, "
                "or use --force to overwrite.",
                exit_code=3,
            )

        from gdoc.api.drive import get_file_version

        version_data = get_file_version(doc_id)
        current_version = version_data.get("version")
        if (
            current_version is not None
            and current_version != state.last_read_version
        ):
            if body is not None and _doc_matches(doc_id, body):
                return None, True
            raise GdocError(
                "doc changed since last read. "
                "Run 'gdoc cat' first, "
                "or use --force to overwrite.",
                exit_code=3,
            )

    return None, False


def cmd_write(args) -> int:
    """Handler for `gdoc write`."""
    import os

    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    force = getattr(args, "force", False)
    tab_name = getattr(args, "tab", None)
    force_collapse = getattr(args, "force_collapse_tabs", False)
    file_path = args.file

    # Read local file first (fail fast on missing file)
    if not os.path.isfile(file_path):
        raise GdocError(f"file not found: {file_path}", exit_code=3)
    try:
        with open(file_path) as f:
            content = f.read()
    except OSError as e:
        raise GdocError(f"cannot read file: {e}", exit_code=3) from e

    # Strip frontmatter — pull prepends it, and leaving it in the upload
    # dumps visible YAML into the doc body.
    from gdoc.frontmatter import parse_frontmatter
    _, content = parse_frontmatter(content)

    # Conflict detection. Content comparison only applies to full-doc
    # writes — a tab write's body never equals the whole-doc export.
    change_info, in_sync = _check_write_conflict(
        doc_id, quiet, force, body=None if tab_name else content,
    )
    if in_sync:
        return _finish_noop_write(doc_id, change_info, args, quiet, command="write")

    from gdoc.format import format_json, get_output_mode
    mode = get_output_mode(args)

    if tab_name:
        from gdoc.api.docs import insert_markdown_into_tab
        result = insert_markdown_into_tab(
            doc_id, tab_name, content, replace=True,
        )

        from gdoc.api.drive import get_file_version
        version_data = get_file_version(doc_id)
        command_version = version_data.get("version")

        _print_tab_write_result(
            mode, doc_id, result, command_version, verb="wrote",
        )
    else:
        # Refuse destructive multi-tab collapse unless the user opts in.
        if not force_collapse:
            from gdoc.api.docs import count_document_tabs
            tab_count = count_document_tabs(doc_id)
            if tab_count > 1:
                raise GdocError(
                    f"write would collapse {tab_count} tabs into 1. "
                    "Use `gdoc write --tab NAME FILE` for per-tab "
                    "writes, `gdoc insert --tab NAME FILE` to populate "
                    "a tab, or pass --force-collapse-tabs to confirm.",
                    exit_code=3,
                )

        from gdoc.api.drive import update_doc_content
        command_version = update_doc_content(doc_id, content)

        if mode == "json":
            print(format_json(written=True, version=command_version))
        elif mode == "plain":
            print(f"id\t{doc_id}")
            print("status\tupdated")
        else:
            print("OK written")

    # Update state
    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info, command="write",
        quiet=quiet, command_version=command_version,
        full_doc_write=not tab_name,
    )

    return 0


def cmd_pull(args) -> int:
    """Handler for `gdoc pull`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    file_path = args.file
    revision = getattr(args, "revision", None)

    # Pre-flight awareness check
    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)
    _require_doc(doc_id, change_info)

    # Export doc (or one past revision) as markdown
    from gdoc.api.drive import export_doc, get_file_info

    rev = None
    if revision:
        from gdoc.api.revisions import export_revision

        rev = _resolve_revision(doc_id, revision)
        markdown = export_revision(
            doc_id, rev["id"], mime_type="text/markdown",
            export_links=rev.get("exportLinks"),
        )
    else:
        markdown = export_doc(doc_id, mime_type="text/markdown")
    metadata = get_file_info(doc_id)
    title = metadata.get("name", "")

    # Add frontmatter and write to local file. Revision pulls
    # deliberately omit the `gdoc:` key — push and the sync hooks key
    # off it, and silently pushing a stale revision over the live doc
    # is a footgun.
    from gdoc.frontmatter import add_frontmatter

    if rev is not None:
        front = {"source": doc_id, "revision": rev["id"], "title": title}
    else:
        front = {"gdoc": doc_id, "title": title}
    content = add_frontmatter(markdown, front)

    try:
        with open(file_path, "w") as f:
            f.write(content)
    except OSError as e:
        raise GdocError(f"cannot write file: {e}", exit_code=3)

    # Output
    from gdoc.format import get_output_mode, format_json

    rev_label = f" @ rev {rev['id']}" if rev is not None else ""
    mode = get_output_mode(args)
    if mode == "json":
        if rev is not None:
            print(format_json(
                pulled=True, title=title, file=file_path,
                revision=rev["id"],
            ))
        else:
            print(format_json(pulled=True, title=title, file=file_path))
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
        command="pull" if rev is None else "pull-revision",
        quiet=quiet,
        command_version=command_version if rev is None else None,
    )

    return 0


def cmd_push(args) -> int:
    """Handler for `gdoc push`."""
    import os

    file_path = args.file
    quiet = getattr(args, "quiet", False)
    force = getattr(args, "force", False)
    force_collapse = getattr(args, "force_collapse_tabs", False)

    # Read local file (fail fast)
    if not os.path.isfile(file_path):
        raise GdocError(f"file not found: {file_path}", exit_code=3)
    try:
        with open(file_path) as f:
            content = f.read()
    except OSError as e:
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

    # Conflict detection (reuse shared helper)
    change_info, in_sync = _check_write_conflict(doc_id, quiet, force, body=body)
    if in_sync:
        return _finish_noop_write(doc_id, change_info, args, quiet, command="push")

    # Refuse destructive multi-tab collapse unless the user opts in.
    # `pull`/`push` round-trips a multi-tab doc through a flat markdown
    # file, so an unguarded push silently deletes every tab but the
    # first. Mirror the safety check from `cmd_write`.
    if not force_collapse:
        from gdoc.api.docs import count_document_tabs
        tab_count = count_document_tabs(doc_id)
        if tab_count > 1:
            raise GdocError(
                f"push would collapse {tab_count} tabs into 1. "
                "Use `gdoc edit --tab NAME` for find/replace within a "
                "tab, `gdoc insert --tab NAME FILE` to add content to a "
                "tab, or pass --force-collapse-tabs to confirm.",
                exit_code=3,
            )

    # Upload body (frontmatter stripped)
    from gdoc.api.drive import update_doc_content

    command_version = update_doc_content(doc_id, body)

    # Output
    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(pushed=True, file=file_path, version=command_version))
    elif mode == "plain":
        print(f"id\t{doc_id}")
        print(f"status\tupdated")
    else:
        print(f"OK pushed {file_path}")

    # Update state
    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info, command="push",
        quiet=quiet, command_version=command_version,
        full_doc_write=True,
    )

    return 0


def cmd_sync_hook(args) -> int:
    """Handler for `gdoc _sync-hook` (called by PostToolUse hook)."""
    import json
    import os

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

        with open(file_path) as f:
            content = f.read()

        from gdoc.frontmatter import parse_frontmatter

        metadata, body = parse_frontmatter(content)
        if "gdoc" not in metadata:
            return 0

        doc_id = _resolve_doc_id(metadata["gdoc"])

        # Refuse to silently flatten a multi-tab doc. The hook runs
        # without user attention on every matching file edit, so there
        # is no safe way to surface a confirmation prompt — skip
        # entirely and log to stderr.
        from gdoc.api.docs import count_document_tabs
        if count_document_tabs(doc_id) > 1:
            title = metadata.get("title", doc_id)
            print(
                f'SYNC: skipped "{title}" (multi-tab doc; sync would '
                "collapse tabs). Use `gdoc edit --tab` or "
                "`gdoc insert --tab` to write to a specific tab.",
                file=sys.stderr,
            )
            return 0

        from gdoc.api.drive import update_doc_content

        command_version = update_doc_content(doc_id, body)

        title = metadata.get("title", doc_id)
        print(
            f'SYNC: pushed to "{title}" (v{command_version})',
            file=sys.stderr,
        )

        from gdoc.state import update_state_after_command

        update_state_after_command(
            doc_id, None, command="push",
            quiet=True, command_version=command_version,
            full_doc_write=True,
        )

    except Exception:
        pass  # Never block the agent

    return 0


def cmd_pull_hook(args) -> int:
    """Handler for `gdoc _pull-hook` (called by PreToolUse hook)."""
    import json
    import os

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

        with open(file_path) as f:
            content = f.read()

        from gdoc.frontmatter import parse_frontmatter

        metadata, _ = parse_frontmatter(content)
        if "gdoc" not in metadata:
            return 0

        doc_id = _resolve_doc_id(metadata["gdoc"])

        from gdoc.api.drive import get_file_version

        version_data = get_file_version(doc_id)
        current_version = version_data.get("version")

        from gdoc.state import load_state

        state = load_state(doc_id)
        if state is not None and state.last_version == current_version:
            return 0  # No remote changes

        # Pull fresh content
        from gdoc.api.drive import export_doc, get_file_info

        markdown = export_doc(doc_id, mime_type="text/markdown")
        file_metadata = get_file_info(doc_id)
        title = file_metadata.get("name", "")
        version = file_metadata.get("version")
        if version is not None:
            version = int(version)

        from gdoc.frontmatter import add_frontmatter

        new_content = add_frontmatter(markdown, {"gdoc": doc_id, "title": title})

        with open(file_path, "w") as f:
            f.write(new_content)

        print(
            f'SYNC: pulled "{title}" (v{version})',
            file=sys.stderr,
        )

        from gdoc.state import update_state_after_command

        update_state_after_command(
            doc_id, None, command="pull",
            quiet=True, command_version=version,
        )

    except Exception:
        pass  # Never block the agent

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

    # Export doc
    from gdoc.api.drive import export_doc

    mime = "text/plain" if use_plain else "text/markdown"
    remote = export_doc(doc_id, mime_type=mime)

    # Read local file
    if not os.path.isfile(file_path):
        raise GdocError(f"file not found: {file_path}", exit_code=3)
    try:
        with open(file_path) as f:
            local = f.read()
    except OSError as e:
        raise GdocError(f"cannot read file: {e}", exit_code=3)

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


def cmd_comment(args) -> int:
    """Handler for `gdoc comment`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.comments import create_comment
    quote = getattr(args, "quote", "") or ""
    result = create_comment(doc_id, args.text, quote=quote)
    new_id = result["id"]

    from gdoc.api.drive import get_file_version
    command_version = get_file_version(doc_id).get("version")

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=new_id, status="created"))
    elif mode == "plain":
        print(f"id\t{new_id}")
    else:
        print(f"OK comment #{new_id}")

    from gdoc.state import update_state_after_command
    update_state_after_command(
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

    from gdoc.api.drive import get_file_version
    command_version = get_file_version(doc_id).get("version")

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(commentId=comment_id, replyId=reply_id, status="created"))
    elif mode == "plain":
        print(f"commentId\t{comment_id}")
        print(f"replyId\t{reply_id}")
    else:
        print(f"OK reply on #{comment_id}")

    from gdoc.state import update_state_after_command
    update_state_after_command(
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

    from gdoc.api.drive import get_file_version
    command_version = get_file_version(doc_id).get("version")

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=comment_id, status="resolved"))
    elif mode == "plain":
        print(f"id\t{comment_id}")
        print(f"status\tresolved")
    else:
        print(f"OK resolved comment #{comment_id}")

    from gdoc.state import update_state_after_command
    update_state_after_command(
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

    from gdoc.api.drive import get_file_version
    command_version = get_file_version(doc_id).get("version")

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=comment_id, status="reopened"))
    elif mode == "plain":
        print(f"id\t{comment_id}")
        print(f"status\treopened")
    else:
        print(f"OK reopened comment #{comment_id}")

    from gdoc.state import update_state_after_command
    update_state_after_command(
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

    from gdoc.api.drive import get_file_version
    command_version = get_file_version(doc_id).get("version")

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=comment_id, status="deleted"))
    elif mode == "plain":
        print(f"id\t{comment_id}")
        print(f"status\tdeleted")
    else:
        print(f"OK deleted comment #{comment_id}")

    from gdoc.state import update_state_after_command
    update_state_after_command(
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
        with open(file_path) as f:
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

    # Insert images if any
    if images:
        _insert_images(new_id, images)

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
    from gdoc.state import update_state_after_command

    update_state_after_command(
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
    from gdoc.state import update_state_after_command

    update_state_after_command(
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
    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info, command="cp", quiet=quiet,
    )

    # Seed state for the new copy
    update_state_after_command(
        new_id, None, command="cp",
        quiet=False, command_version=version,
    )

    return 0


def cmd_share(args) -> int:
    """Handler for `gdoc share`."""
    doc_id = _resolve_doc_id(args.doc)
    email = args.email
    role = getattr(args, "role", "reader")
    quiet = getattr(args, "quiet", False)

    # Pre-flight awareness check
    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.drive import create_permission

    create_permission(doc_id, email, role)

    from gdoc.format import get_output_mode, format_json

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(email=email, role=role, status="shared"))
    elif mode == "plain":
        print(f"email\t{email}")
        print(f"role\t{role}")
    else:
        print(f"OK shared with {email} as {role}")

    # Update state for the doc
    from gdoc.state import update_state_after_command

    update_state_after_command(doc_id, change_info, command="share", quiet=quiet)

    return 0


def cmd_update(args) -> int:
    """Handler for `gdoc update`."""
    from gdoc.update import run_update
    return run_update()


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
        epilog="Note: edit operates on raw document text. "
               "Use `gdoc cat --plain DOC` to see matchable text. "
               "Replacement text supports markdown formatting "
               "(bold, italic, headings, bullets, links).",
    )
    edit_p.add_argument("doc", help="Document ID or URL")
    edit_p.add_argument("old_text", nargs="?", default=None, help="Text to find")
    edit_p.add_argument("new_text", nargs="?", default=None, help="Replacement text")
    edit_p.add_argument("--old-file", help="Read old text from file")
    edit_p.add_argument("--new-file", help="Read new text from file")
    edit_p.add_argument(
        "--all", action="store_true", help="Replace all occurrences"
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
        help="Target a table cell instead of searching text: a label "
             "(replaces the cell to its right) or 'ROW,COL' coordinates",
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
        "--tab", help="Target a specific tab by title or ID"
    )
    edit_p.set_defaults(func=cmd_edit)

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
            "Upload a markdown file and replace the doc's contents. "
            "Without --tab, write replaces the entire document and "
            "collapses any additional tabs into one — use --tab NAME "
            "for per-tab writes, or `gdoc insert` to add content to an "
            "existing tab. YAML frontmatter in the input is stripped "
            "automatically."
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
        "--force", action="store_true", help="Force overwrite even if doc changed"
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
            "before upload."
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
    pull_p.set_defaults(func=cmd_pull)

    # push
    push_p = sub.add_parser("push", parents=[output_parent], help="Upload local markdown to doc")
    push_p.add_argument("file", help="Local file with gdoc frontmatter")
    push_p.add_argument(
        "--force", action="store_true", help="Force overwrite even if doc changed"
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
        "--quote", help="Quoted text the comment refers to",
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
    share_p.add_argument("email", help="Email to share with")
    share_p.add_argument(
        "--role",
        choices=["reader", "writer", "commenter"],
        default="reader",
        help="Permission role",
    )
    share_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    share_p.set_defaults(func=cmd_share)

    # new
    new_p = sub.add_parser("new", parents=[output_parent], help="Create a blank document")
    new_p.add_argument("title", help="Document title")
    new_p.add_argument("--folder", help="Folder ID to place doc in")
    new_p.add_argument(
        "--file", dest="file_path",
        help="Create doc from a local markdown file",
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


def main() -> int:
    """Entry point for the gdoc CLI."""
    if _is_top_level_help_invocation(sys.argv):
        from gdoc.update import auto_update_for_help
        auto_update_for_help()

    parser = build_parser()
    args = parser.parse_args()

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
        if args.command not in ("update", "_sync-hook", "_pull-hook"):
            from gdoc.update import check_for_update
            check_for_update()

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
