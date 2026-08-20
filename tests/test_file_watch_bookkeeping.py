"""外部変更の監視 (`file_watch.py`) の「裏方の後始末」を縛るテスト。

`test_file_reload.py` は「訊いてくるか」「読み直すか」を見ているが、
2026-08-19（39 回目）の変異検査で、その周りの**目に見えない後始末**が
壊しても全部緑のままだと分かったので、ここで別に縛る。

いずれも実機では「同じ問いが何度も出る」「閉じたはずのファイルの問いが
後から出る」「外部変更に気づかない」という形で出るもので、どれも
2026-08-06〜08-08 に実機報告として上がった種類の不具合と同じ性質を持つ。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from editor_app.main_window import MainWindow


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    path = tmp_path / "sample.txt"
    path.write_text("最初の内容\n", encoding="utf-8")
    return path


def _patch_prompt(monkeypatch, answer: QMessageBox.StandardButton) -> list[int]:
    """再読み込みの確認ダイアログを差し替え、出た回数を数えるリストを返す。"""
    calls: list[int] = []

    def fake(*_args, **_kwargs):
        calls.append(1)
        return answer

    monkeypatch.setattr("editor_app.file_watch.show_message_box", fake)
    return calls


def test_watched_path_through_a_symlink_finds_the_tab(
    window: MainWindow, tmp_path: Path
) -> None:
    """``QFileSystemWatcher`` が正規化前のパスで通知してきても、タブを見つける。

    タブが持つパスは必ず :func:`~editor_app.fileio.resolve_path` を通した
    形（シンボリックリンクを解いた実体）になっている。Qt 側が登録したときの
    文字列をそのまま返してくれる限りは単純比較で当たるが、**当たらなかった
    ときの解決し直し**が無いと、リンク越しに開いたファイルの外部変更を
    まるごと取り逃がす。
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    target = real_dir / "sample.txt"
    target.write_text("最初の内容\n", encoding="utf-8")
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir)

    through_link = link_dir / "sample.txt"
    editor = window.open_path(through_link)
    assert editor is not None
    # open_path はリンクを解いた実体をタブのパスにする（＝単純比較では当たらない）。
    assert editor.path != through_link

    assert window._find_editor_for_watched_path(str(through_link)) is editor


def test_change_notice_after_closing_drops_the_pending_entry(
    window: MainWindow, tmp_path: Path
) -> None:
    """閉じたファイルの通知が遅れて届いたら、保留も取り消すこと。

    ``QFileSystemWatcher`` は ``removePaths`` の直後でも、既にカーネルが
    積んだ変更イベントを届けてくることがある。ここで保留を残すと、
    **閉じたはずのファイルについて「再読み込みしますか？」が後から出る**
    （2026-08-06 の実機報告と同じ形）。
    """
    first = tmp_path / "first.txt"
    first.write_text("あ\n", encoding="utf-8")
    second = tmp_path / "second.txt"
    second.write_text("い\n", encoding="utf-8")

    closing = window.open_path(first)
    window.open_path(second)
    assert closing is not None
    path = str(closing.path)

    window._pending_reload_paths.add(path)
    assert window.close_editor(closing) is True

    window._on_watched_file_changed(path)

    assert path not in window._pending_reload_paths


def test_processing_empties_the_pending_list(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """一度処理した保留は消すこと（同じ問いが何度も出ないように）。

    消さないと、次に別のファイルが変わってタイマーが起きたときに、
    **もう答えたはずのファイルまでもう一度訊いてくる**。
    """
    editor = window.open_path(sample)
    assert editor is not None
    calls = _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)

    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._pending_reload_paths.add(str(editor.path))
    window._process_pending_reloads()

    assert len(calls) == 1
    assert window._pending_reload_paths == set()

    # 保留が空なので、次にタイマーが起きても何も訊いてこない。
    window._process_pending_reloads()
    assert len(calls) == 1


def test_changed_file_tab_comes_to_the_front(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    """外部変更を訊く前に、そのタブを前面に出すこと。

    出さないと、**どのファイルの話なのか分からないまま**「再読み込み
    しますか？」に答えることになる（本文が見えている別のタブの話だと
    勘違いして「はい」を押すと、編集がその場で消える）。
    """
    first = tmp_path / "first.txt"
    first.write_text("あ\n", encoding="utf-8")
    second = tmp_path / "second.txt"
    second.write_text("い\n", encoding="utf-8")

    changed = window.open_path(first)
    front = window.open_path(second)
    assert changed is not None and front is not None
    assert window.tabs.currentWidget() is front

    _patch_prompt(monkeypatch, QMessageBox.StandardButton.No)
    first.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(changed.path)

    assert window.tabs.currentWidget() is changed


def _capture_prompt_text(monkeypatch, answer: QMessageBox.StandardButton) -> list[str]:
    """再読み込みの確認ダイアログを差し替え、**出た本文**を集めるリストを返す。"""
    messages: list[str] = []

    def fake(_parent, _icon, _title, text, *_args, **_kwargs):
        messages.append(text)
        return answer

    monkeypatch.setattr("editor_app.file_watch.show_message_box", fake)
    return messages


def test_prompt_warns_that_unsaved_edits_will_be_discarded(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """未保存の編集があるタブでは、「その編集は破棄されます」と伝えること。

    同じ「再読み込みしますか？」でも、**未保存の編集があるかどうかで
    失うものがまるで違う**。伝えないまま「はい」を押させると、書きかけの
    本文が黙って消える。
    """
    editor = window.open_path(sample)
    assert editor is not None
    editor.setPlainText("書きかけの本文")
    editor.set_modified(True)

    messages = _capture_prompt_text(monkeypatch, QMessageBox.StandardButton.No)
    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(sample)

    assert len(messages) == 1
    assert "破棄されます" in messages[0]


def test_prompt_without_unsaved_edits_does_not_mention_discarding(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """逆に、失うものが無いときに「破棄されます」と脅さないこと。"""
    editor = window.open_path(sample)
    assert editor is not None
    assert editor.is_modified is False

    messages = _capture_prompt_text(monkeypatch, QMessageBox.StandardButton.No)
    sample.write_text("外部で書き換えた内容\n", encoding="utf-8")
    window._maybe_reload_changed_file(sample)

    assert len(messages) == 1
    assert "破棄されます" not in messages[0]
