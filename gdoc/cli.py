"""CLI parser, subcommand dispatch, and exception handler."""

import argparse
import os
import sys

from gdoc import __version__
from gdoc.util import AuthError, GdocError


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

    if tab or all_tabs:
        from gdoc.notify import pre_flight
        change_info = pre_flight(doc_id, quiet=quiet)

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
        from gdoc.notify import pre_flight
        change_info = pre_flight(doc_id, quiet=quiet)

        from gdoc.api.drive import export_doc
        markdown = export_doc(doc_id, mime_type="text/markdown")

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

    # Pre-flight awareness check
    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    mime_type = "text/plain" if getattr(args, "plain", False) else "text/markdown"

    from gdoc.api.drive import export_doc

    content = export_doc(doc_id, mime_type=mime_type)
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


def cmd_tabs(args) -> int:
    """Handler for `gdoc tabs`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

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


def cmd_add_tab(args) -> int:
    """Handler for `gdoc add-tab`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    title = args.title

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.docs import add_tab
    result = add_tab(doc_id, title)
    tab_id = result["tabId"]

    from gdoc.api.drive import get_file_version
    command_version = get_file_version(doc_id).get("version")

    from gdoc.format import format_json, get_output_mode
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(
            id=tab_id, title=result["title"],
            index=result["index"], doc_id=doc_id,
        ))
    elif mode == "verbose":
        print(f"Added tab: {result['title']}")
        print(f"ID: {tab_id}")
        print(f"Index: {result['index']}")
    elif mode == "plain":
        print(f"id\t{tab_id}")
        print(f"title\t{result['title']}")
        print(f"index\t{result['index']}")
    else:
        print(f"{tab_id}\t{result['title']}")

    from gdoc.state import update_state_after_command
    update_state_after_command(
        doc_id, change_info, command="add-tab", quiet=quiet,
        command_version=command_version,
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

    words_display = word_count if word_count is not None else "N/A"

    if mode == "json":
        print(
            format_json(
                id=doc_id,
                title=title,
                owner=owner,
                modified=modified,
                words=words_display,
            )
        )
    elif mode == "plain":
        print(f"title\t{title}")
        print(f"owner\t{owner}")
        print(f"modified\t{modified}")
        print(f"words\t{words_display}")
    elif mode == "verbose":
        print(f"Title: {title}")
        print(f"Owner: {owner}")
        print(f"Modified: {modified}")
        print(f"Created: {created}")
        print(f"Last editor: {last_editor}")
        print(f"Type: {mime_type}")
        print(f"Size: {size or 'N/A'}")
        print(f"Words: {words_display}")
    else:
        print(f"Title: {title}")
        print(f"Owner: {owner}")
        print(f"Modified: {modified[:10]}")
        print(f"Words: {words_display}")

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

    # Resolve text from args or files (fail fast before API calls)
    old_text = args.old_text
    new_text = args.new_text
    old_file = getattr(args, "old_file", None)
    new_file = getattr(args, "new_file", None)

    if old_file or new_file:
        if not (old_file and new_file):
            raise GdocError(
                "--old-file and --new-file must be used together",
                exit_code=3,
            )
        old_text = _read_file(old_file)
        new_text = _read_file(new_file)
    elif old_text is None or new_text is None:
        raise GdocError(
            "old_text and new_text required "
            "(or use --old-file/--new-file)",
            exit_code=3,
        )

    # Pre-flight awareness check
    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)

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
        matches = find_text_in_document(
            None, old_text, match_case=case_sensitive, body=tab_match["body"],
        )
    else:
        document = get_document(doc_id)
        revision_id = document.get("revisionId", "")
        matches = find_text_in_document(document, old_text, match_case=case_sensitive)

    if not matches:
        raise GdocError("no match found", exit_code=3)
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


def _check_write_conflict(doc_id: str, quiet: bool, force: bool):
    """Run conflict detection for write-like commands.

    Returns ChangeInfo or None. Raises GdocError(exit_code=3) on conflict.
    """
    if not quiet:
        from gdoc.notify import pre_flight

        change_info = pre_flight(doc_id, quiet=False)

        if not force:
            if change_info.last_read_version is None:
                raise GdocError(
                    "no read baseline. Run 'gdoc cat' first, "
                    "or use --force to overwrite.",
                    exit_code=3,
                )
            if change_info.has_conflict:
                raise GdocError(
                    "doc changed since last read. "
                    "Run 'gdoc cat' first, "
                    "or use --force to overwrite.",
                    exit_code=3,
                )
        return change_info

    if not force:
        from gdoc.state import load_state

        state = load_state(doc_id)

        if state is None or state.last_read_version is None:
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
            raise GdocError(
                "doc changed since last read. "
                "Run 'gdoc cat' first, "
                "or use --force to overwrite.",
                exit_code=3,
            )

    return None


def cmd_write(args) -> int:
    """Handler for `gdoc write`."""
    import os

    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    force = getattr(args, "force", False)
    file_path = args.file

    # Read local file first (fail fast on missing file)
    if not os.path.isfile(file_path):
        raise GdocError(f"file not found: {file_path}", exit_code=3)
    try:
        with open(file_path) as f:
            content = f.read()
    except OSError as e:
        raise GdocError(f"cannot read file: {e}", exit_code=3)

    # Conflict detection
    change_info = _check_write_conflict(doc_id, quiet, force)

    # Upload content via Drive API
    from gdoc.api.drive import update_doc_content

    command_version = update_doc_content(doc_id, content)

    # Output
    from gdoc.format import format_json, get_output_mode

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(written=True, version=command_version))
    elif mode == "plain":
        print(f"id\t{doc_id}")
        print(f"status\tupdated")
    else:
        print("OK written")

    # Update state (Decision #4: last_version only)
    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info, command="write",
        quiet=quiet, command_version=command_version,
    )

    return 0


def cmd_pull(args) -> int:
    """Handler for `gdoc pull`."""
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    file_path = args.file

    # Pre-flight awareness check
    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)

    # Export doc as markdown
    from gdoc.api.drive import export_doc, get_file_info

    markdown = export_doc(doc_id, mime_type="text/markdown")
    metadata = get_file_info(doc_id)
    title = metadata.get("name", "")

    # Add frontmatter and write to local file
    from gdoc.frontmatter import add_frontmatter

    content = add_frontmatter(markdown, {"gdoc": doc_id, "title": title})

    try:
        with open(file_path, "w") as f:
            f.write(content)
    except OSError as e:
        raise GdocError(f"cannot write file: {e}", exit_code=3)

    # Output
    from gdoc.format import get_output_mode, format_json

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(pulled=True, title=title, file=file_path))
    elif mode == "plain":
        print(f"path\t{file_path}")
    elif mode == "verbose":
        print(f'Pulled: "{title}"')
        print(f"File: {file_path}")
        print(f"Doc ID: {doc_id}")
    else:
        print(f'OK pulled "{title}" -> {file_path}')

    # Update state (pull is a read command)
    command_version = metadata.get("version")
    if command_version is not None:
        command_version = int(command_version)
    from gdoc.state import update_state_after_command

    update_state_after_command(
        doc_id, change_info, command="pull",
        quiet=quiet, command_version=command_version,
    )

    return 0


def cmd_push(args) -> int:
    """Handler for `gdoc push`."""
    import os

    file_path = args.file
    quiet = getattr(args, "quiet", False)
    force = getattr(args, "force", False)

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
        raise GdocError(
            "no gdoc frontmatter found. Use 'gdoc pull' first.",
            exit_code=3,
        )

    doc_id = _resolve_doc_id(metadata["gdoc"])

    # Conflict detection (reuse shared helper)
    change_info = _check_write_conflict(doc_id, quiet, force)

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


def cmd_diff(args) -> int:
    """Handler for `gdoc diff`."""
    import difflib
    import os

    doc_id = _resolve_doc_id(args.doc)
    file_path = args.file
    quiet = getattr(args, "quiet", False)
    use_plain = getattr(args, "plain", False)

    # Pre-flight
    from gdoc.notify import pre_flight

    change_info = pre_flight(doc_id, quiet=quiet)

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
    comments = list_comments(doc_id, include_resolved=include_resolved)

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
            print(f"{cid}\t{status}\t{author_str}\t{content}")
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
    from gdoc.auth import authenticate

    authenticate(no_browser=getattr(args, "no_browser", False))
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
    cat_p = sub.add_parser("cat", parents=[output_parent], help="Export doc as markdown")
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
        "--max-bytes", type=int, default=0,
        help="Truncate output at N bytes (0 = unlimited)",
    )
    cat_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    cat_p.set_defaults(func=cmd_cat)

    # tabs
    tabs_p = sub.add_parser("tabs", parents=[output_parent], help="List tabs in a doc")
    tabs_p.add_argument("doc", help="Document ID or URL")
    tabs_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    tabs_p.set_defaults(func=cmd_tabs)

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
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    edit_p.add_argument(
        "--tab", help="Target a specific tab by title or ID"
    )
    edit_p.set_defaults(func=cmd_edit)

    # diff
    diff_p = sub.add_parser("diff", parents=[output_parent], help="Compare doc with local file")
    diff_p.add_argument("doc", help="Document ID or URL")
    diff_p.add_argument("file", help="Local file to compare against")
    diff_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    diff_p.set_defaults(func=cmd_diff)

    # write
    write_p = sub.add_parser("write", parents=[output_parent], help="Overwrite doc from local file")
    write_p.add_argument("doc", help="Document ID or URL")
    write_p.add_argument("file", help="Local markdown file")
    write_p.add_argument(
        "--force", action="store_true", help="Force overwrite even if doc changed"
    )
    write_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    write_p.set_defaults(func=cmd_write)

    # pull
    pull_p = sub.add_parser("pull", parents=[output_parent], help="Download doc as local markdown")
    pull_p.add_argument("doc", help="Document ID or URL")
    pull_p.add_argument("file", help="Local file to write")
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


def main() -> int:
    """Entry point for the gdoc CLI."""
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

    # Check for updates (skip for the update command itself and internal hooks)
    if args.command not in ("update", "_sync-hook", "_pull-hook"):
        from gdoc.update import check_for_update
        check_for_update()

    try:
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
