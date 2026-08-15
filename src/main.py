#!/usr/bin/env python3
"""エントリポイント: ``python src/main.py`` で起動する."""

from __future__ import annotations

import sys
from pathlib import Path

# スクリプトとして直接実行された場合でも editor_app を import できるようにする。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from editor_app.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
