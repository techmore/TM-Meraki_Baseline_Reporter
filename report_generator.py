#!/usr/bin/env python3
"""Compatibility entrypoint for report generation.

The canonical direct command is now `python -m reporting`. This wrapper remains
so `run.sh` and older local workflows can keep calling `report_generator.py`.
"""

from reporting.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
