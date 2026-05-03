import os
import sys

from meraki_env import load_env
from meraki_client import paged_get


def main():
    load_env()
    api_key = os.getenv("MERAKI_API_KEY")
    if not api_key:
        print("Missing MERAKI_API_KEY env var.", file=sys.stderr)
        print("Example: export MERAKI_API_KEY=your_key_here", file=sys.stderr)
        sys.exit(1)

    orgs = paged_get("/organizations", api_key)
    __import__("json").dump(orgs, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
