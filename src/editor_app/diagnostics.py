"""動作の引っかかりを後から調べるための記録（診断ログ）.

**画面が一瞬固まる**不具合は、起きた瞬間を見ていないと原因が分からない
（後から調べても痕跡が残らない）。2026-10-20、「最前面に出すと 1 秒
反応しない」という実機報告で、推測を何度も外したことがきっかけで入れた。

- :class:`StallMonitor` … 画面の処理（Qt のイベントループ）が
  :data:`STALL_THRESHOLD_SECONDS` 以上止まったら、**止まっている最中に**
  何を実行していたか（Python のスタック）を記録する。
  止まっている間は Python 側から何も書けないので、``faulthandler`` の
  見張り（C の別スレッド）に書かせる。スタックの先頭が ``app.exec()``
  なら **Qt/画面側の中で止まっていた**、Tuxpad のコードの行なら
  **その行で止まっていた**、と読める。
- :func:`log_diagnostic` … 処理の所要時間など、1 行の記録を足す。

記録先は :func:`diagnostics_log_path`（``~/.local/state/tuxpad/stall.log``）。
**普段は何も書かない**（止まったとき・遅かったときだけ書く）。
"""

from __future__ import annotations

import faulthandler
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO

from PySide6.QtCore import QObject, QTimer

#: これ以上イベントループが止まったら「固まった」として記録する（秒）。
#: 利用者が「1 秒反応しない」と感じる手前で拾えるように 0.7 秒。
STALL_THRESHOLD_SECONDS = 0.7

#: 見張りを張り直す間隔（ミリ秒）。止まっていなければこの間隔で
#: 張り直され続けるので、``faulthandler`` が書くのは本当に止まったときだけ。
STALL_TICK_MS = 200

#: 記録がこれより大きくなったら、次に開くときに空にする（際限なく
#: 育たないように。診断用なので古いものは要らない）。
MAX_LOG_BYTES = 1_000_000


def diagnostics_log_path() -> Path:
    """診断ログの置き場所。``XDG_STATE_HOME`` があればそれに従う。"""
    base = os.environ.get("XDG_STATE_HOME") or ""
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "tuxpad" / "stall.log"


def _open_log() -> TextIO | None:
    """追記用に開く（大きすぎれば空にしてから）。開けなければ ``None``。

    **診断ログが書けないことで本体が落ちてはいけない**ので、失敗は黙って
    諦める。
    """
    path = diagnostics_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.write_text("", encoding="utf-8")
        # 必ず追記モードで開く。見張り（faulthandler）と時刻の 1 行は
        # 別々のファイルオブジェクトから同じファイルへ書くので、
        # 追記モードでないと互いの書いた位置を上書きしてしまう。
        return open(path, "a", encoding="utf-8")  # noqa: SIM115 - 開いたまま使う
    except OSError:
        return None


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_diagnostic(message: str) -> None:
    """診断ログに 1 行足す（時刻付き）。書けなければ黙って諦める。"""
    handle = _open_log()
    if handle is None:
        return
    with handle:
        handle.write(f"{_timestamp()} {message}\n")


class StallMonitor(QObject):
    """画面の処理が止まった瞬間の中身を記録する見張り。

    :data:`STALL_TICK_MS` ごとにタイマーで
    ``faulthandler.dump_traceback_later(STALL_THRESHOLD_SECONDS)`` を
    張り直す。イベントループが回っていれば期限前に張り直されるので
    何も起きず、**止まっていれば期限が来て、止まっている最中のスタックが
    ログに書かれる**。動き出した直前の空きも測り、何秒止まっていたかを
    時刻付きで 1 行添える。

    ``arm``・``cancel``・``clock`` を差し替えられるのはテストのため
    （``faulthandler`` の見張りはプロセスに 1 つしか無く、pytest も
    テストの時間切れに使っているので、テストで本物を張ると壊れる）。
    """

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        threshold: float = STALL_THRESHOLD_SECONDS,
        arm: Callable[..., None] | None = None,
        cancel: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = log_diagnostic,
    ) -> None:
        super().__init__(parent)
        self._threshold = threshold
        self._arm = arm if arm is not None else faulthandler.dump_traceback_later
        self._cancel = (
            cancel if cancel is not None else faulthandler.cancel_dump_traceback_later
        )
        self._clock = clock
        self._log = log
        self._handle: TextIO | None = None
        self._last_tick: float | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(STALL_TICK_MS)
        self._timer.timeout.connect(self.tick)

    def start(self) -> None:
        """見張りを始める。記録先が開けなければ何もしない（本体は止めない）。"""
        self._handle = _open_log()
        if self._handle is None:
            return
        self._last_tick = self._clock()
        self._rearm()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._cancel()
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def tick(self) -> None:
        """タイマーから呼ばれる。前回からの空きを見て、見張りを張り直す。"""
        now = self._clock()
        if self._last_tick is not None:
            gap = now - self._last_tick
            if gap >= self._threshold:
                self._log(
                    f"画面の処理が {gap:.2f} 秒止まっていました"
                    "（直前に書かれたスタックが、止まっていた間の処理）"
                )
        self._last_tick = now
        self._rearm()

    def _rearm(self) -> None:
        if self._handle is None:
            return
        self._arm(self._threshold, repeat=False, file=self._handle, exit=False)
