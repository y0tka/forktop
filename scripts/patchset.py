#!/usr/bin/env python3
"""Telegram Desktop patchset pipeline entry point."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from patchsetlib.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
