#!/usr/bin/env python3
"""Compatibility entrypoint for report generation.

The implementation now lives under `reporting/` to keep report logic separate
from the top-level script surface.
"""

from reporting.app import main

if __name__ == "__main__":
    raise SystemExit(main())
