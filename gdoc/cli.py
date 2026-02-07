"""CLI parser, subcommand dispatch, and exception handler."""

import argparse
import sys

from gdoc.util import AuthError, GdocError


class GdocArgumentParser(argparse.ArgumentParser):
    """Custom parser that exits with code 3 on usage errors (not 2)."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"ERR: {message}", file=sys.stderr)
        sys.exit(3)


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

    if getattr(args, "comments", False):
        print("ERR: cat --comments is not yet implemented", file=sys.stderr)
        return 4  # STUB — removed when real implementation added

    mime_type = "text/plain" if getattr(args, "plain", False) else "text/markdown"

    from gdoc.api.drive import export_doc

    content = export_doc(doc_id, mime_type=mime_type)

    from gdoc.format import get_output_mode, format_json

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(content=content))
    else:
        print(content, end="")

    return 0


def cmd_info(args) -> int:
    """Handler for `gdoc info`."""
    doc_id = _resolve_doc_id(args.doc)

    from gdoc.api.drive import get_file_info, export_doc
    from gdoc.format import get_output_mode, format_json

    metadata = get_file_info(doc_id)
    text = export_doc(doc_id, mime_type="text/plain")
    word_count = len(text.split())

    title = metadata.get("name", "")
    owner_info = metadata.get("owners", [{}])[0]
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

    if mode == "json":
        print(
            format_json(
                id=doc_id,
                title=title,
                owner=owner,
                modified=modified,
                words=word_count,
            )
        )
    elif mode == "verbose":
        print(f"Title: {title}")
        print(f"Owner: {owner}")
        print(f"Modified: {modified}")
        print(f"Created: {created}")
        print(f"Last editor: {last_editor}")
        print(f"Type: {mime_type}")
        print(f"Size: {size or 'N/A'}")
        print(f"Words: {word_count}")
    else:
        print(f"Title: {title}")
        print(f"Owner: {owner}")
        print(f"Modified: {modified[:10]}")
        print(f"Words: {word_count}")

    return 0


def cmd_auth(args) -> int:
    """Handler for `gdoc auth`."""
    from gdoc.auth import authenticate

    authenticate(no_browser=getattr(args, "no_browser", False))
    return 0


def cmd_stub(args) -> int:
    """Placeholder for unimplemented commands."""
    print(f"ERR: {args.command} is not yet implemented", file=sys.stderr)
    return 4  # STUB — removed when real implementation added


def build_parser() -> GdocArgumentParser:
    """Build the CLI argument parser with all subcommands."""
    parser = GdocArgumentParser(
        prog="gdoc",
        description="CLI for Google Docs & Drive",
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

    # Also add to the top-level parser for `gdoc --json <cmd>` form
    top_output_group = parser.add_mutually_exclusive_group()
    top_output_group.add_argument("--json", action="store_true", help="JSON output")
    top_output_group.add_argument(
        "--verbose", action="store_true", help="Detailed output"
    )

    sub = parser.add_subparsers(dest="command")

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
    ls_p.set_defaults(func=cmd_stub)

    # find
    find_p = sub.add_parser("find", parents=[output_parent], help="Search files by name/content")
    find_p.add_argument("query", help="Search query")
    find_p.set_defaults(func=cmd_stub)

    # cat
    cat_p = sub.add_parser("cat", parents=[output_parent], help="Export doc as markdown")
    cat_p.add_argument("doc", help="Document ID or URL")
    cat_output = cat_p.add_mutually_exclusive_group()
    cat_output.add_argument(
        "--comments", action="store_true", help="Include comment annotations"
    )
    cat_output.add_argument(
        "--plain", action="store_true", help="Export as plain text"
    )
    cat_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    cat_p.set_defaults(func=cmd_cat)

    # edit
    edit_p = sub.add_parser("edit", parents=[output_parent], help="Find and replace text")
    edit_p.add_argument("doc", help="Document ID or URL")
    edit_p.add_argument("old_text", help="Text to find")
    edit_p.add_argument("new_text", help="Replacement text")
    edit_p.add_argument(
        "--all", action="store_true", help="Replace all occurrences"
    )
    edit_p.add_argument(
        "--case-sensitive", action="store_true", help="Case-sensitive matching"
    )
    edit_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    edit_p.set_defaults(func=cmd_stub)

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
    write_p.set_defaults(func=cmd_stub)

    # comments
    comments_p = sub.add_parser("comments", parents=[output_parent], help="List comments on a doc")
    comments_p.add_argument("doc", help="Document ID or URL")
    comments_p.add_argument(
        "--all", action="store_true", help="Include resolved comments"
    )
    comments_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    comments_p.set_defaults(func=cmd_stub)

    # comment
    comment_p = sub.add_parser("comment", parents=[output_parent], help="Add a comment to a doc")
    comment_p.add_argument("doc", help="Document ID or URL")
    comment_p.add_argument("text", help="Comment text")
    comment_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    comment_p.set_defaults(func=cmd_stub)

    # reply
    reply_p = sub.add_parser("reply", parents=[output_parent], help="Reply to a comment")
    reply_p.add_argument("doc", help="Document ID or URL")
    reply_p.add_argument("comment_id", help="Comment ID to reply to")
    reply_p.add_argument("text", help="Reply text")
    reply_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    reply_p.set_defaults(func=cmd_stub)

    # resolve
    resolve_p = sub.add_parser("resolve", parents=[output_parent], help="Resolve a comment")
    resolve_p.add_argument("doc", help="Document ID or URL")
    resolve_p.add_argument("comment_id", help="Comment ID to resolve")
    resolve_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    resolve_p.set_defaults(func=cmd_stub)

    # reopen
    reopen_p = sub.add_parser("reopen", parents=[output_parent], help="Reopen a resolved comment")
    reopen_p.add_argument("doc", help="Document ID or URL")
    reopen_p.add_argument("comment_id", help="Comment ID to reopen")
    reopen_p.add_argument(
        "--quiet", action="store_true", help="Skip pre-flight checks"
    )
    reopen_p.set_defaults(func=cmd_stub)

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
    share_p.set_defaults(func=cmd_stub)

    # new
    new_p = sub.add_parser("new", parents=[output_parent], help="Create a blank document")
    new_p.add_argument("title", help="Document title")
    new_p.add_argument("--folder", help="Folder ID to place doc in")
    new_p.set_defaults(func=cmd_stub)

    # cp
    cp_p = sub.add_parser("cp", parents=[output_parent], help="Duplicate a document")
    cp_p.add_argument("doc", help="Document ID or URL")
    cp_p.add_argument("title", help="Title for the copy")
    cp_p.set_defaults(func=cmd_stub)

    return parser


def main() -> int:
    """Entry point for the gdoc CLI."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help(sys.stderr)
        return 3

    if getattr(args, "json", False) and getattr(args, "verbose", False):
        parser.error("argument --verbose: not allowed with argument --json")

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
