#!/usr/bin/env python3
"""List the Meraki organizations the configured API key can see.

Used by ./run.sh as a connectivity check before the backup stage; also handy on
its own to find organization IDs.
"""

import argparse
import json
import sys
from typing import List, Optional

from meraki_client import MerakiRequestError, paged_get
from meraki_env import mask_key, require_api_key, resolve_api_key


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meraki_query.py",
        description="Verify Meraki API access and list the organizations visible to the API key.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "credentials:\n"
            "  MERAKI_API_KEY (environment or .env), MERAKI_API_KEY_FILE, or the OS keyring.\n"
            "  The key is never accepted on the command line.\n"
            "\n"
            "examples:\n"
            "  python meraki_query.py            # JSON list of organizations\n"
            "  python meraki_query.py --table    # one line per org: id and name\n"
            "  python meraki_query.py --check    # exit 0 if the key works, 1 otherwise (no output)"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--table", action="store_true", help="Print a compact id/name listing instead of JSON.")
    group.add_argument("--check", action="store_true", help="Only verify access; print nothing on success.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    api_key = require_api_key()
    if not api_key:
        return 1

    try:
        orgs = paged_get("/organizations", api_key)
    except MerakiRequestError as exc:
        _key, source = resolve_api_key()
        print(f"Meraki API request failed using key {mask_key(api_key)} from {source}: {exc}", file=sys.stderr)
        return 1

    if args.check:
        return 0
    if args.table:
        for org in orgs:
            print(f"{org.get('id', '?'):<22} {org.get('name', '')}")
        return 0
    json.dump(orgs, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
