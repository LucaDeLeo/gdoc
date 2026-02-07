"""Output mode selection and formatting helpers."""

import json


def get_output_mode(args) -> str:
    """Determine output mode from parsed args."""
    if getattr(args, "json", False):
        return "json"
    if getattr(args, "verbose", False):
        return "verbose"
    return "terse"


def format_success(message: str, mode: str = "terse") -> str:
    """Format a success message for the given output mode."""
    if mode == "json":
        return json.dumps({"ok": True, "message": message})
    return message


def format_error(message: str) -> str:
    """Format an error message. Always plain text, always stderr."""
    return f"ERR: {message}"
