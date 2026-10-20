"""画面の固まりを捕まえる見張り（``diagnostics.py``）のテスト.

背景（2026-10-20、実機）
------------------------
「Tuxpad を最前面に出した瞬間に約 1 秒反応しない」という報告で、原因を
推測しては外すことを繰り返した。固まりは**起きた瞬間を見ていないと
痕跡が残らない**ので、イベントループが止まったら止まっている最中の
Python のスタックを書き残す見張り（:class:`StallMonitor`）を入れた。

ここで見張るのは次のこと。

1. **止まったときだけ書く** — 普段（間隔どおりに回っているとき）は
   1 行も書かない。境目（ちょうどしきい値）は「止まった」側に入れる。
2. **見張りを張り直し続ける** — 張り直しが途切れると、止まっていない
   のに期限が来てスタックが書かれる（＝誤報で調査を惑わせる）。
3. **止めたら取り消す** — 残したままだと、終了処理の最中に期限が来る。
4. **記録が書けなくても本体は落ちない** — 診断のために本体を壊さない。
5. **本当に書かれる** — 上の 1〜4 は差し替えた部品で見るが、それだけ
   では「``faulthandler`` に渡す引数が間違っていて何も書かれない」が
   すり抜ける。**別プロセス**で本物を張り、実際にイベントループを
   止めて、スタックがファイルに出ることを確かめる。

**テストのプロセスで本物の見張りを張ってはいけない。**
``faulthandler`` の時間切れの見張りはプロセスに 1 つしか無く、pytest が
``faulthandler_timeout = 60``（``pytest.ini``）で同じものを使っている。
張ると pytest の安全装置を上書きしてしまう。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from editor_app import diagnostics
from editor_app.diagnostics import (
    MAX_LOG_BYTES,
    STALL_THRESHOLD_SECONDS,
    STALL_TICK_MS,
    StallMonitor,
    diagnostics_log_path,
    log_diagnostic,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """記録先をテスト用のフォルダへ向ける（利用者の本物の記録に書かない）。"""
    root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    return root


class _Clock:
    """好きなだけ進められる時計。"""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _Recorder:
    """``StallMonitor`` に渡す部品の差し替え一式（呼ばれ方を記録する）。"""

    def __init__(self) -> None:
        self.clock = _Clock()
        self.armed: list[tuple[tuple, dict]] = []
        self.cancelled = 0
        self.logged: list[str] = []

    def arm(self, *args, **kwargs) -> None:
        self.armed.append((args, kwargs))

    def cancel(self) -> None:
        self.cancelled += 1

    def log(self, message: str) -> None:
        self.logged.append(message)

    def monitor(self, **kwargs) -> StallMonitor:
        return StallMonitor(
            arm=self.arm,
            cancel=self.cancel,
            clock=self.clock,
            log=self.log,
            **kwargs,
        )


@pytest.fixture
def parts(state_home: Path, qapp) -> _Recorder:
    return _Recorder()


# ----------------------------------------------------------------------
# 記録先
# ----------------------------------------------------------------------
def test_log_path_follows_xdg_state_home(state_home: Path) -> None:
    assert diagnostics_log_path() == state_home / "tuxpad" / "stall.log"


@pytest.mark.parametrize("value", [None, ""])
def test_log_path_falls_back_to_local_state(
    value: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``XDG_STATE_HOME`` が無い・空なら ``~/.local/state``（XDG の既定）。

    空文字を「カレントフォルダ」と読むと、起動した場所に記録が散らばる。
    """
    if value is None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_STATE_HOME", value)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert diagnostics_log_path() == tmp_path / ".local" / "state" / "tuxpad" / "stall.log"


def test_log_diagnostic_appends_timestamped_lines(state_home: Path) -> None:
    log_diagnostic("1 行目")
    log_diagnostic("2 行目")

    lines = diagnostics_log_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, "追記ではなく上書きしている"
    assert lines[0].endswith(" 1 行目")
    assert lines[1].endswith(" 2 行目")
    # 時刻は「いつ固まったか」を利用者の体感と突き合わせるのに要る。
    stamp = lines[0].removesuffix(" 1 行目")
    assert len(stamp) == len("2026-01-01 00:00:00")
    assert stamp[4] == "-" and stamp[13] == ":"


def test_log_diagnostic_survives_an_unwritable_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """記録先が作れなくても、例外を本体へ漏らさない。"""
    blocker = tmp_path / "ふつうのファイル"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))  # フォルダを作れない

    log_diagnostic("書けないはず")  # 例外にならないこと

    assert blocker.read_text(encoding="utf-8") == ""


def test_oversized_log_is_emptied_before_writing(state_home: Path) -> None:
    """際限なく育たない（大きすぎたら空にしてから書く）。"""
    path = diagnostics_log_path()
    path.parent.mkdir(parents=True)
    path.write_text("x" * (MAX_LOG_BYTES + 1), encoding="utf-8")

    log_diagnostic("新しい記録")

    assert path.read_text(encoding="utf-8").endswith(" 新しい記録\n")
    assert path.stat().st_size < 100


def test_log_at_exactly_the_limit_is_kept(state_home: Path) -> None:
    """ちょうど上限までは消さない（直近の記録を不用意に捨てない）。"""
    path = diagnostics_log_path()
    path.parent.mkdir(parents=True)
    path.write_text("x" * MAX_LOG_BYTES, encoding="utf-8")

    log_diagnostic("足した記録")

    assert path.read_text(encoding="utf-8").startswith("x" * MAX_LOG_BYTES)


def test_log_size_limit_is_about_a_megabyte() -> None:
    """記録は**数日ぶんの固まり**が残れば足りる。小さすぎると 1 回ぶんの
    スタック（数 KB）で毎回空になり、大きすぎると誰も消さずに育つ。"""
    assert 100_000 <= MAX_LOG_BYTES <= 10_000_000


# ----------------------------------------------------------------------
# 見張りの張り方
# ----------------------------------------------------------------------
def test_threshold_and_tick_are_sane() -> None:
    """しきい値は「1 秒固まる」を拾える手前で、張り直しより十分長いこと。

    張り直しの間隔がしきい値に近いと、**普通に回っているだけで**期限が
    来て、止まっていないのにスタックが書かれる（誤報）。
    """
    assert 0.3 <= STALL_THRESHOLD_SECONDS < 1.0
    assert STALL_TICK_MS * 3 <= STALL_THRESHOLD_SECONDS * 1000


def test_start_arms_the_watchdog_and_starts_the_timer(parts: _Recorder) -> None:
    monitor = parts.monitor()

    monitor.start()
    try:
        assert len(parts.armed) == 1
        args, kwargs = parts.armed[0]
        assert args == (STALL_THRESHOLD_SECONDS,)
        # 1 回きり（張り直すのはこちら）・書いたあと終了しない・記録先へ。
        assert kwargs["repeat"] is False
        assert kwargs["exit"] is False
        assert Path(kwargs["file"].name) == diagnostics_log_path()
        assert kwargs["file"].mode == "a", "追記でないと他の記録を上書きする"
        assert monitor._timer.isActive()
        assert monitor._timer.interval() == STALL_TICK_MS
    finally:
        monitor.stop()


def test_custom_threshold_is_used_for_arming_and_judging(parts: _Recorder) -> None:
    monitor = parts.monitor(threshold=2.0)
    monitor.start()
    try:
        assert parts.armed[0][0] == (2.0,)
        parts.clock.now += 1.5
        monitor.tick()
        assert parts.logged == []
    finally:
        monitor.stop()


def test_tick_rearms_without_logging_when_running_normally(parts: _Recorder) -> None:
    monitor = parts.monitor()
    monitor.start()
    try:
        for _ in range(3):
            parts.clock.now += STALL_TICK_MS / 1000
            monitor.tick()

        assert len(parts.armed) == 4, "張り直しが途切れると、止まっていないのに書かれる"
        assert parts.logged == []
    finally:
        monitor.stop()


def test_tick_after_a_stall_logs_how_long_it_stopped(parts: _Recorder) -> None:
    monitor = parts.monitor()
    monitor.start()
    try:
        parts.clock.now += 1.23
        monitor.tick()

        assert len(parts.logged) == 1
        assert "1.23 秒止まっていました" in parts.logged[0]
        assert len(parts.armed) == 2  # 記録したあとも見張りは続ける
    finally:
        monitor.stop()


def test_stall_of_exactly_the_threshold_is_logged(parts: _Recorder) -> None:
    """境目は「止まった」側。``faulthandler`` も期限ちょうどで書くので揃える。

    時計を 0 から始めるのが大事。100.0 から始めると ``100.7 - 100.0`` が
    浮動小数点で 0.7 を少し超え、``>`` に変えても緑のまま（＝境目を
    踏めていない）になる（変異 ``diag-stall-boundary`` で実際にそうなった）。
    """
    parts.clock.now = 0.0
    monitor = parts.monitor()
    monitor.start()
    try:
        parts.clock.now += STALL_THRESHOLD_SECONDS
        monitor.tick()

        assert len(parts.logged) == 1
    finally:
        monitor.stop()


def test_gap_is_measured_from_the_previous_tick_not_from_start(
    parts: _Recorder,
) -> None:
    """短い間隔が積み重なっただけで「止まった」と書かないこと。"""
    monitor = parts.monitor()
    monitor.start()
    try:
        for _ in range(10):  # 合計 2 秒だが、1 回ずつは短い
            parts.clock.now += 0.2
            monitor.tick()

        assert parts.logged == []
    finally:
        monitor.stop()


def test_stop_cancels_the_watchdog_and_the_timer(parts: _Recorder) -> None:
    monitor = parts.monitor()
    monitor.start()
    handle = monitor._handle

    monitor.stop()

    assert parts.cancelled == 1
    assert not monitor._timer.isActive()
    assert handle.closed
    # 止めたあとにタイマーの残りが届いても、張り直さない。
    monitor.tick()
    assert len(parts.armed) == 1


def test_timer_is_wired_to_tick(parts: _Recorder, qtbot) -> None:
    """タイマーの結線が外れると、最初の 1 回で見張りが切れて**誤報になる**。"""
    monitor = parts.monitor()
    monitor.start()
    try:
        qtbot.waitUntil(lambda: len(parts.armed) >= 2, timeout=2000)
    finally:
        monitor.stop()


def test_start_does_nothing_when_the_log_cannot_be_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp
) -> None:
    """記録先が開けないなら見張らない（本体は普通に起動する）。

    ``faulthandler`` に開けていないファイルを渡すと例外になる。
    """
    blocker = tmp_path / "ふつうのファイル"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    parts = _Recorder()
    monitor = parts.monitor()

    monitor.start()

    assert parts.armed == []
    assert not monitor._timer.isActive()
    monitor.stop()  # 始まっていなくても止められる


def test_defaults_use_the_real_faulthandler() -> None:
    """差し替えない限り、本物の ``faulthandler`` を使うこと（張るだけで、
    ここでは呼ばない）。"""
    import faulthandler

    monitor = StallMonitor()
    assert monitor._arm is faulthandler.dump_traceback_later
    assert monitor._cancel is faulthandler.cancel_dump_traceback_later
    assert monitor._log is diagnostics.log_diagnostic


# ----------------------------------------------------------------------
# 本物で、実際に止めて確かめる（別プロセス）
# ----------------------------------------------------------------------
#: イベントループの中でわざと 1.5 秒眠り、そのあと終わるだけのアプリ。
#: 眠っている関数の名前（``deliberately_stall``）がスタックに出るはず。
_STALL_RUNNER = """
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from editor_app.diagnostics import StallMonitor


def deliberately_stall():
    time.sleep(1.5)


app = QApplication(sys.argv)
monitor = StallMonitor(app)
monitor.start()
QTimer.singleShot(300, deliberately_stall)
QTimer.singleShot(2300, app.quit)
app.exec()
monitor.stop()
"""


def test_a_real_stall_writes_the_stack_while_stopped(tmp_path: Path) -> None:
    state = tmp_path / "state"
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "XDG_STATE_HOME": str(state),
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }

    proc = subprocess.run(
        [sys.executable, "-c", _STALL_RUNNER],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    log = (state / "tuxpad" / "stall.log").read_text(encoding="utf-8")
    # faulthandler が止まっている最中に書いたスタック（眠っている関数が見える）。
    assert "deliberately_stall" in log, log
    # 動き出してから足す「何秒止まっていたか」。スタックの**後**に来ること
    # （「直前に書かれたスタックが…」と案内しているため）。
    summary = log.index("秒止まっていました")
    assert log.index("deliberately_stall") < summary
    # 普段は書かない：止めたのは 1 回だけなので、記録も 1 回ぶん。
    assert log.count("秒止まっていました") == 1, log
    assert log.count("deliberately_stall") == 1, log
