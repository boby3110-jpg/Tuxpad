"""PySide6（＝実行環境そのもの）の健全性を見張るテスト.

ここで見るのは Tuxpad のコードではなく、**その下で動く PySide6 が
壊れていないか**。きっかけは 2026-10-11（72 回目）ルーティーンで、
**PySide6 6.12.0 + Python 3.11 の組み合わせだとテストが 68% の地点で
「Aborted」だけを残してプロセスごと消える**という事故に当たったこと。
（この環境の ``python3`` は 3.11 で、``requirements.txt`` の
``PySide6>=6.6`` が当時の最新 6.12.0 を拾っていた。）

原因は PySide6 6.12.0（shiboken6 6.12.0）の参照数バグで、
**戻り値の無い（void の）メソッドを 1 回呼ぶごとに ``None`` の参照を
1 つ取りこぼす**（計測では 200 回でちょうど -200、1000 回で -1000）。
``None`` が不滅（immortal）になった **Python 3.12 以降では無害**だが、
**Python 3.11 では参照数が 0 まで減り**、

    Fatal Python error: none_dealloc: deallocating None:
    bug likely caused by a refcount error in a C extension

でインタプリタごと落ちる。実機でこの組み合わせを使うと、
**10 万回ほど Qt を呼んだところ（＝普通の編集作業の途中）で
前触れなくアプリが消える**＝未保存の本文が失われる。利用者の最優先事項
（手元のものが壊れる・消える）に直結するので、3 段構えで守る:

1. ``requirements.txt`` が Python 3.11 以下に 6.12.0 を入れない
   （6.11.2 は Python 3.11 でも問題なしと確かめた）。
   ``install.sh --allow-pip-pyside6`` もこの ``requirements.txt`` を
   使うので、pip 版で入れる経路もここで塞がる。
2. ``tests/conftest.py`` の ``pytest_configure`` が、壊れた組み合わせでは
   **1 件も走らせる前に**理由を出して止める（「Aborted」だけ残る状態を
   作らない）。
3. このファイルが、1 と 2 のどちらが外されても気づけるようにする。

**手元で確かめた範囲**（2026-10-11）:

| Python | PySide6 | void 呼び出し 200 回での None の参照 |
| --- | --- | --- |
| 3.11.17 | 6.11.2 | ±0（健全） |
| 3.11.17 | 6.12.0 | **-200（1 回につき 1 つ）** |
| 3.13.16 | 6.12.0 | ±0（None が不滅なので減らない） |

6.12.1 以降で直ったとしても、**実際に測ってから**除外を緩めること。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from pyside_runtime import (
    NONE_IS_IMMORTAL,
    PROBE_CALLS,
    TOLERANCE,
    broken_pyside_message,
    none_references_lost,
    restore_none_references,
)


@pytest.mark.skipif(
    NONE_IS_IMMORTAL,
    reason="Python 3.12 以降は None が不滅なので、取りこぼしても参照数が減らない",
)
def test_void_method_calls_do_not_leak_none_references() -> None:
    """void のメソッドを呼んでも ``None`` の参照数が減らないこと.

    ``conftest.py`` の足切りが外されても、ここで気づけるようにしておく。
    """
    lost = none_references_lost()
    # 減っていたら返しておく（返さないと残りのテストで本当に 0 に達する）。
    restore_none_references(lost)
    assert lost <= TOLERANCE, broken_pyside_message(lost)


def test_probe_counts_are_sane() -> None:
    """探りの回数と許容が、バグの大きさを見分けられる比になっていること.

    許容が ``PROBE_CALLS`` に近づくと「1 回につき 1 つ」の取りこぼしを
    見逃す。逆に 0 にすると、別スレッドの都合でまぐれ赤が出る。
    """
    assert PROBE_CALLS >= 50
    assert 0 < TOLERANCE < PROBE_CALLS // 2


def test_conftest_stops_the_run_before_any_test_on_a_broken_pyside() -> None:
    """``conftest.py`` の足切り（``pytest_configure``）が残っていること."""
    source = (Path(__file__).resolve().parent / "conftest.py").read_text(
        encoding="utf-8"
    )
    assert "def pytest_configure(" in source, (
        "走り出す前の足切りが消えています。壊れた PySide6 では"
        "「Aborted」だけが残り、原因が分からなくなります"
    )
    assert "none_references_lost()" in source
    assert "pytest.exit(" in source


def test_immortality_reading_matches_the_running_python() -> None:
    """``NONE_IS_IMMORTAL`` の読みが、実際の参照数と食い違っていないこと.

    ここを取り違えると見張りが黙って無効になる（``None`` が有限なのに
    「不滅」と読めば足切りが働かず、元の「Aborted だけ」に戻る）。
    バージョン番号の比較を信じるのではなく、**実際に数えて**突き合わせる。
    """
    looks_immortal = sys.getrefcount(None) > 2**30
    assert NONE_IS_IMMORTAL == looks_immortal, (
        f"NONE_IS_IMMORTAL={NONE_IS_IMMORTAL} ですが、実際の None の参照数は "
        f"{sys.getrefcount(None)} です（Python "
        f"{sys.version_info.major}.{sys.version_info.minor}）"
    )


def test_requirements_keeps_pyside6_6_12_0_out_of_python_3_11() -> None:
    """``requirements.txt`` が Python 3.11 以下への 6.12.0 を断っていること."""
    text = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(
        encoding="utf-8"
    )
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    pyside_lines = [line for line in lines if line.lower().startswith("pyside6")]

    assert len(pyside_lines) == 2, (
        "PySide6 の行は「3.12 以降むけ」と「3.11 以下むけ」の 2 行のはず: "
        f"{pyside_lines}"
    )

    old_python = [
        line
        for line in pyside_lines
        if re.search(r'python_version\s*<\s*"3\.12"', line)
    ]
    assert old_python, f"Python 3.11 以下むけの行が見当たりません: {pyside_lines}"
    assert "!=6.12.0" in old_python[0].replace(" ", ""), (
        "Python 3.11 以下では PySide6 6.12.0 を除外したままにしてください"
        "（None の参照数バグでアプリが突然落ちます）: " + old_python[0]
    )

    new_python = [
        line
        for line in pyside_lines
        if re.search(r'python_version\s*>=\s*"3\.12"', line)
    ]
    assert new_python, (
        "Python 3.12 以降むけの行が無いと、新しい Python で PySide6 が"
        f"まったく入らなくなります: {pyside_lines}"
    )
    assert "!=6.12.0" not in new_python[0].replace(" ", ""), (
        "Python 3.12 以降では 6.12.0 は無害なので、除外しないこと: "
        + new_python[0]
    )
