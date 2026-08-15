"""ファイルを開く・保存するときのファイルシステムアクセス回数の回帰テスト。

「rclone で同期しているフォルダのファイルを開くときが一番動作が重い」という
実機報告への対応（2026-08-08）。rclone/FUSE のようなファイルシステムでは
``stat`` 1 回がローカルの何十倍も遅いため、**1 回の操作で何回 stat するか**が
そのまま体感の遅さになる。

以前は :meth:`MainWindow._refresh_file_watches` が呼ばれるたびに開いている
全タブへ ``is_file()`` していたため、ファイルを 1 つ開くのに
「タブの数 × 2 回」の stat が走っていた（タブが増えるほど開くのが遅くなる）。
ここでは **開いているタブの数が増えても stat の回数が増えないこと**を、
``os.stat`` を数えることで守る。回数の絶対値ではなく「増えないこと」を見て
いるのは、Python や Qt の実装都合で数回ぶれても壊れないようにするため。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from editor_app.main_window import MainWindow


class _StatCounter:
    """``os.stat`` / ``os.lstat`` の呼び出し回数を数える（monkeypatch 用）。"""

    def __init__(self) -> None:
        self.stat = 0
        self.lstat = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_stat, real_lstat = os.stat, os.lstat

        def counted_stat(*args, **kwargs):
            self.stat += 1
            return real_stat(*args, **kwargs)

        def counted_lstat(*args, **kwargs):
            self.lstat += 1
            return real_lstat(*args, **kwargs)

        monkeypatch.setattr(os, "stat", counted_stat)
        monkeypatch.setattr(os, "lstat", counted_lstat)

    @property
    def total(self) -> int:
        return self.stat + self.lstat


@pytest.fixture
def many_files(tmp_path: Path) -> list[Path]:
    paths = []
    for i in range(12):
        path = tmp_path / f"file{i:02d}.txt"
        path.write_text(f"{i} 番目のファイルです。\n", encoding="utf-8")
        paths.append(path)
    return paths


def _open_and_count(
    window: MainWindow, path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    counter = _StatCounter()
    with monkeypatch.context() as patch:
        counter.install(patch)
        assert window.open_path(path) is not None
    return counter.total


def test_open_cost_does_not_grow_with_tab_count(
    window: MainWindow, many_files: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """タブが増えても、1 ファイル開くときのファイルシステムアクセスが増えない。"""
    first = _open_and_count(window, many_files[0], monkeypatch)

    for path in many_files[1:-1]:
        window.open_path(path)
    assert len(window.editors()) >= 10

    last = _open_and_count(window, many_files[-1], monkeypatch)

    # 多少のぶれは許すが、タブの数に比例して増えていないこと。
    assert last <= first + 2, (
        f"タブが 1 個のとき {first} 回だったファイルシステムアクセスが、"
        f"タブが {len(window.editors())} 個で {last} 回に増えている"
        "（_refresh_file_watches が全タブを stat していないか確認すること）"
    )


def test_save_cost_does_not_grow_with_tab_count(
    window: MainWindow, many_files: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """上書き保存のファイルシステムアクセスも、タブの数に比例しない。"""
    editor = window.open_path(many_files[0])
    assert editor is not None
    editor.insertPlainText("追記\n")

    counter = _StatCounter()
    with monkeypatch.context() as patch:
        counter.install(patch)
        assert window.save_editor(editor) is True
    few_tabs = counter.total

    for path in many_files[1:]:
        window.open_path(path)
    assert len(window.editors()) >= 10

    window.tabs.setCurrentWidget(editor)
    editor.insertPlainText("もう一度追記\n")
    counter = _StatCounter()
    with monkeypatch.context() as patch:
        counter.install(patch)
        assert window.save_editor(editor) is True
    many_tabs = counter.total

    assert many_tabs <= few_tabs + 2, (
        f"タブが少ないとき {few_tabs} 回だった保存時のファイルシステム"
        f"アクセスが、タブ {len(window.editors())} 個で {many_tabs} 回に増えている"
    )


def test_watches_still_track_open_files(
    window: MainWindow, many_files: list[Path]
) -> None:
    """回数を減らしても、外部変更の監視そのものは今までどおり働くこと。"""
    for path in many_files[:3]:
        window.open_path(path)

    watched = set(window._file_watcher.files())
    assert watched == {str(path) for path in many_files[:3]}

    # タブを閉じたら監視から外れる。
    editor = window.find_editor_by_path(many_files[0])
    assert editor is not None
    window.close_editor(editor)
    assert str(many_files[0]) not in set(window._file_watcher.files())


def test_untitled_tab_is_not_watched(window: MainWindow, many_files: list[Path]) -> None:
    """まだ保存していない無題タブは監視対象にならない（パスが無いため）。"""
    window.open_path(many_files[0])
    window.new_file()
    window._refresh_file_watches()

    assert set(window._file_watcher.files()) == {str(many_files[0])}


def test_deleted_file_is_not_re_added(window: MainWindow, many_files: list[Path]) -> None:
    """開いた後に外部で消されたファイルは、監視に足し直されない。"""
    path = many_files[0]
    window.open_path(path)
    window._file_watcher.removePaths(window._file_watcher.files())
    path.unlink()

    window._refresh_file_watches()

    assert window._file_watcher.files() == []
