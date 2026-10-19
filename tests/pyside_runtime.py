"""PySide6（実行環境そのもの）が壊れていないかを探る下ごしらえ.

``tests/conftest.py`` の ``pytest_configure``（走り出す前の足切り）と
``tests/test_pyside_runtime_sanity.py``（通常の見張り）の**両方**から使う。
背景と、なぜここまでするのかは ``test_pyside_runtime_sanity.py`` の
冒頭に書いてある。
"""

from __future__ import annotations

import ctypes
import sys

#: ``None`` を不滅（immortal）として扱う Python（3.12 以降）。
#: ここでは参照数が 2**32-1 に張り付くので、取りこぼしても減らない
#: ＝この探り方では何も見えない（＆落ちもしない）。
NONE_IS_IMMORTAL = sys.version_info >= (3, 12)

#: 探りに使う void メソッドの呼び出し回数。壊れた PySide6 では
#: ちょうどこの数だけ ``None`` の参照が減る。
PROBE_CALLS = 200

#: 揺らぎの許容。別スレッドの都合で 1〜2 ずれることはあり得るが、
#: 「1 回につき 1 つ減る」バグはこの桁では収まらない。
TOLERANCE = PROBE_CALLS // 10


def none_references_lost() -> int:
    """void のメソッドを ``PROBE_CALLS`` 回呼んで、減った ``None`` の参照数を返す.

    返り値が正なら取りこぼしている（0 以下なら健全）。
    ``QApplication`` は要らない（この取りこぼしは shiboken の
    戻り値の扱いそのものなので、GUI を立ち上げる前から再現する）。
    """
    from PySide6.QtCore import QObject

    probe = QObject()
    # 初回だけの寄り道（キャッシュの作成など）を測定から外す。
    probe.setObjectName("warm-up")

    before = sys.getrefcount(None)
    for _ in range(PROBE_CALLS):
        probe.setObjectName("probe")
    after = sys.getrefcount(None)
    return before - after


def restore_none_references(lost: int) -> None:
    """``none_references_lost()`` が減らしたぶんの参照を返す.

    返さないと、報告したあとの後片付けや残りのテストで本当に参照数が
    0 に達し、プロセスごと落ちて「どこが悪いのか分からない」状態に
    戻ってしまう。
    """
    for _ in range(max(0, lost)):
        ctypes.pythonapi.Py_IncRef(ctypes.py_object(None))


def broken_pyside_message(lost: int) -> str:
    """壊れた組み合わせだと分かったときに出す文面."""
    import PySide6

    return (
        f"PySide6 {PySide6.__version__} は Python "
        f"{sys.version_info.major}.{sys.version_info.minor} では使えません: "
        f"void のメソッド呼び出し {PROBE_CALLS} 回で None の参照を "
        f"{lost} 個取りこぼしています（1 回につき 1 つ）。"
        "そのまま使うと、Qt を 10 万回ほど呼んだところで "
        "「Fatal Python error: none_dealloc」でプロセスごと落ちます"
        "（＝編集中に前触れなくアプリが消え、未保存の本文が失われる）。"
        "対処: PySide6 を 6.11.2 など別のバージョンへ落とす"
        "（requirements.txt は Python 3.11 以下では 6.12.0 を除外しています）、"
        "または Python 3.12 以降で動かしてください。"
    )
