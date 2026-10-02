"""Run exact finding bindings offline; arguments after -- pass through to pytest."""

import argparse
import json
import socket
from pathlib import Path

import pytest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing-only", action="store_true")
    args, pytest_args = parser.parse_known_args()
    bindings = json.loads(Path(__file__).with_name("finding-bindings.json").read_text())
    nodes = sorted(
        {
            n
            for b in bindings
            for n in b["assertions"]
            if not args.existing_only or "/acceptance/" not in n
        }
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Finding binding checks must not access the network")

    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    return pytest.main(["-q", *nodes, *[x for x in pytest_args if x != "--"]])


if __name__ == "__main__":
    raise SystemExit(main())
