"""README の「ディレクトリ構成」が、実際のモジュールと食い違っていないか。

README のこの一覧は、**どのファイルに何を書くかの案内**として使っている
（「タブのウィンドウ間移動は `tab_transfer.py` に書く」など、各モジュールの
先頭にも同じ趣旨が書いてある）。ところがモジュールを増やしても README を
直し忘れれば、**誰も気づかないまま案内だけが古びる**。実際に
`tab_transfer.py`（`main_window.py` から切り出したもの）が一覧から抜けた
まま残っていた（2026-08-18（31 回目）に発見）。

食い違うと困るのは「案内を見て別の場所へ書いてしまう」ことなので、
**足りない側だけでなく、消えたのに残っている側も見る**。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
PACKAGE_DIR = REPO_ROOT / "src" / "editor_app"

#: 一覧に出さないもの（`__init__.py` は中身がほぼ無い包み）。
NOT_LISTED = {"__init__.py"}


def documented_modules() -> set[str]:
    """README の「ディレクトリ構成」のコードブロックに出てくる .py の名前。"""
    body = README.read_text(encoding="utf-8")
    section = body.split("## ディレクトリ構成", 1)
    assert len(section) == 2, "README に「## ディレクトリ構成」が無い"
    block = section[1].split("```")[1]
    return set(re.findall(r"[\w]+\.py", block))


def actual_modules() -> set[str]:
    """実際に在るモジュール（エントリポイントとパッケージの中身）。"""
    modules = {path.name for path in PACKAGE_DIR.glob("*.py")} - NOT_LISTED
    return modules | {"main.py"}


def test_readme_lists_every_module() -> None:
    """新しく足したモジュールが README の一覧に載っている。"""
    missing = actual_modules() - documented_modules()

    assert not missing, (
        "README の「ディレクトリ構成」に載っていないモジュールがある: "
        f"{sorted(missing)}"
    )


def test_readme_lists_no_removed_module() -> None:
    """消した・改名したモジュールが README の一覧に残っていない。"""
    stale = documented_modules() - actual_modules()

    assert not stale, (
        "README の「ディレクトリ構成」に、もう無いモジュールが載っている: "
        f"{sorted(stale)}"
    )
