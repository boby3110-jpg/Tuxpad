"""保存する直前に「外部で変わっていないか」を確かめる（利用者の依頼による追加）。

NAS や rclone のようにネットワーク越しのファイルシステムでは、
``QFileSystemWatcher`` による監視 (:mod:`editor_app.file_watch`) に変更の通知が
そもそも届かない。そこで**ディスクへ書く直前にもう一度 stat を取り**、
読み込んだときと ``(更新日時, サイズ)`` が変わっていたら保存しない。

利用者の指定は「**書き込みを止める**」「**終了時であれば終了処理を中断
する**」の 2 点で、当初は知らせるだけだった。その後の実機フィードバックで
**「ディスクの内容を見てから決める」**（統一差分を出して 3 択）に変わって
いるので、そちらを固定する。

ダイアログは monkeypatch で塞ぐ。**塞ぎ忘れると本物のモーダルダイアログが
開き、ヘッドレスのテストが応答する人を待って固まる**ので、パッチ先
（`file_commands` / `main_window` / `file_watch`）はコードの引っ越しに
合わせること。塞ぐ先は 2 つある——差分付きの 3 択
(:data:`CHANGE_PROMPT`) と、差分を出せないときのフォールバック
(:data:`SAVE_DIALOG`)。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from editor_app.dialogs import EXTERNAL_CHANGE_OVERWRITE, EXTERNAL_CHANGE_RELOAD
from editor_app.fileio import file_signature
from editor_app.main_window import MainWindow

#: 保存直前のチェックが出すフォールバックのダイアログ（`file_commands` にある）。
SAVE_DIALOG = "editor_app.file_commands.show_message_box"
#: 差分を見せて行き先を選ばせるダイアログ（`file_commands` にある）。
CHANGE_PROMPT = "editor_app.file_commands.prompt_external_change"
#: 終了時の「保存しますか？」（チェックボックス付き、`main_window` にある）。
CLOSE_DIALOG = "editor_app.main_window.show_message_box_with_checkbox"


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    path = tmp_path / "sample.txt"
    path.write_text("最初の内容\n", encoding="utf-8")
    return path


class DialogRecorder:
    """ダイアログの呼び出しを記録し、決めた答えを返すモック。"""

    def __init__(self, answer=QMessageBox.StandardButton.Ok) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def __call__(self, _parent, _icon, title, text, *args, **kwargs):
        self.calls.append((title, text))
        return self.answer


class ChangePromptRecorder:
    """差分ダイアログの呼び出しを記録し、決めた行動を返すモック。"""

    def __init__(self, choice: str | None = None) -> None:
        self.choice = choice
        self.calls: list[tuple[str, str]] = []

    def __call__(self, _parent, *, name: str, diff_text: str) -> str | None:
        self.calls.append((name, diff_text))
        return self.choice


def patch_save_dialog(monkeypatch) -> DialogRecorder:
    recorder = DialogRecorder()
    monkeypatch.setattr(SAVE_DIALOG, recorder)
    return recorder


def patch_change_prompt(monkeypatch, choice: str | None = None) -> ChangePromptRecorder:
    """差分付きの 3 択を塞ぐ。既定は「キャンセル」(None)。"""
    recorder = ChangePromptRecorder(choice)
    monkeypatch.setattr(CHANGE_PROMPT, recorder)
    return recorder


def edit_text(editor, text: str) -> None:
    editor.selectAll()
    editor.insertPlainText(text)


def change_externally(path: Path, text: str) -> None:
    """外部のツールがファイルを書き換えた状況を作る。"""
    path.write_text(text, encoding="utf-8")


def _raise(exc: BaseException):
    """呼ばれたら ``exc`` を投げるだけの差し替え用関数を返す。"""

    def raiser(*_args, **_kwargs):
        raise exc

    return raiser


def touch_only(path: Path) -> None:
    """中身も大きさも変えずに、更新日時だけを動かす。"""
    stamp = 1_000_000_000 * 10**9  # 2001-09-09、確実に「今」と違う値
    os.utime(path, ns=(stamp, stamp))


# ----------------------------------------------------------------------
# 見分け札そのもの（file_signature）
# ----------------------------------------------------------------------


def test_signature_changes_when_content_changes(sample: Path) -> None:
    before = file_signature(sample)
    sample.write_text("だいぶ長さの違う内容にしておく\n", encoding="utf-8")
    assert file_signature(sample) != before


def test_signature_is_none_without_a_file(tmp_path: Path) -> None:
    """存在しないパスと、パスそのものが無い場合（無題タブ）は None。"""
    assert file_signature(tmp_path / "ありません.txt") is None
    assert file_signature(None) is None


def test_untitled_tab_has_no_signature(window: MainWindow) -> None:
    editor = window.new_file()
    editor.remember_disk_state()
    assert editor.disk_signature is None


# ----------------------------------------------------------------------
# (a) 外部で変わっていたら書き込まず、差分を見せて選ばせる
# ----------------------------------------------------------------------


def test_external_change_blocks_write_and_warns(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    prompt = patch_change_prompt(monkeypatch)  # 既定はキャンセル
    fallback = patch_save_dialog(monkeypatch)

    change_externally(sample, "別の PC が書いた内容\n")

    assert window.save_editor(editor) is False
    # ディスクは外部が書いた内容のまま（踏み潰していない）。
    assert sample.read_text(encoding="utf-8") == "別の PC が書いた内容\n"
    # 本文は失っていない。保存できていないので未保存のまま。
    assert editor.toPlainText() == "こちらの編集\n"
    assert editor.is_modified is True
    assert len(prompt.calls) == 1
    assert prompt.calls[0][0] == sample.name
    # 差分を出せている以上、簡易メッセージへは落ちていない。
    assert fallback.calls == []


def test_size_unchanged_but_mtime_moved_is_detected(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """大きさが同じでも、更新日時が動いていれば外部変更とみなす。"""
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    prompt = patch_change_prompt(monkeypatch)

    touch_only(sample)

    assert window.save_editor(editor) is False
    assert len(prompt.calls) == 1


# ----------------------------------------------------------------------
# (a-2) 差分の中身と 3 つの選択肢
# ----------------------------------------------------------------------


def test_dialog_shows_the_difference_between_disk_and_tab(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """差分は「ディスクの内容 → タブの内容」の向きで、両方の行が出る。"""
    editor = window.open_path(sample)
    edit_text(editor, "共通の行\nこちらの編集\n")
    prompt = patch_change_prompt(monkeypatch)

    change_externally(sample, "共通の行\n別の PC が書いた内容\n")
    window.save_editor(editor)

    diff = prompt.calls[0][1]
    assert "-別の PC が書いた内容" in diff  # ディスク側は削除行
    assert "+こちらの編集" in diff  # タブ側は追加行
    assert " 共通の行" in diff  # 変わっていない行は文脈として出る


def test_dialog_reads_the_disk_again_every_time(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """差分は毎回**その時点の**ディスクの内容と比べる（覚えた内容を使わない）。

    検知してからダイアログを出すまでの間に、さらに書き換わることがある
    （同期ソフトが続けて書く等）。古い内容の差分を見せて決めさせると、
    利用者は実際とは違うものを見て「上書きする」を選ぶことになる。
    """
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    prompt = patch_change_prompt(monkeypatch)

    change_externally(sample, "1 回目に書かれた内容\n")
    window.save_editor(editor)
    assert "-1 回目に書かれた内容" in prompt.calls[0][1]

    change_externally(sample, "2 回目に書かれた内容\n")
    window.save_editor(editor)
    assert "-2 回目に書かれた内容" in prompt.calls[1][1]
    assert "1 回目" not in prompt.calls[1][1]


def test_reload_choice_replaces_the_tab_with_the_disk_content(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """「ディスクの内容を読み込む」… 本文が入れ替わり、以後は普通に保存できる。"""
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    patch_change_prompt(monkeypatch, EXTERNAL_CHANGE_RELOAD)
    fallback = patch_save_dialog(monkeypatch)

    change_externally(sample, "別の PC が書いた内容\n")

    # 書き込んではいないが、このタブの用は済んだので True
    #（終了処理の途中なら、そのまま先へ進んでよい）。
    assert window.save_editor(editor) is True
    assert editor.toPlainText() == "別の PC が書いた内容\n"
    assert editor.is_modified is False
    # ディスクは触っていない。
    assert sample.read_text(encoding="utf-8") == "別の PC が書いた内容\n"

    # 見分け札が更新されているので、次の保存は止まらない。
    edit_text(editor, "取り込んだうえでの編集\n")
    assert window.save_editor(editor) is True
    assert sample.read_text(encoding="utf-8") == "取り込んだうえでの編集\n"
    assert fallback.calls == []


def test_overwrite_choice_writes_the_tab_content(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """「自分の内容のまま上書き保存する」… 実際に書けて、以後も普通に保存できる。"""
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    prompt = patch_change_prompt(monkeypatch, EXTERNAL_CHANGE_OVERWRITE)

    change_externally(sample, "別の PC が書いた内容\n")

    assert window.save_editor(editor) is True
    assert sample.read_text(encoding="utf-8") == "こちらの編集\n"
    assert editor.is_modified is False
    assert editor.disk_signature == file_signature(sample)

    # バイパスはこの 1 回だけ。次の保存はもう普通に通る。
    edit_text(editor, "続きの編集\n")
    assert window.save_editor(editor) is True
    assert sample.read_text(encoding="utf-8") == "続きの編集\n"
    assert len(prompt.calls) == 1


def test_cancel_choice_changes_nothing_and_can_be_retried(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """「キャンセル」… 何も起きず、**次に保存しようとすればまた出る**。

    行き止まりにならないこと（諦めるしかない状態にしないこと）の確認。
    """
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    prompt = patch_change_prompt(monkeypatch)

    change_externally(sample, "別の PC が書いた内容\n")

    assert window.save_editor(editor) is False
    assert sample.read_text(encoding="utf-8") == "別の PC が書いた内容\n"
    assert editor.toPlainText() == "こちらの編集\n"
    assert editor.is_modified is True

    # もう一度保存しようとすれば、同じダイアログがまた出る。
    assert window.save_editor(editor) is False
    assert len(prompt.calls) == 2

    # そこで「上書き保存する」に切り替えれば、ちゃんと立て直せる。
    prompt.choice = EXTERNAL_CHANGE_OVERWRITE
    assert window.save_editor(editor) is True
    assert sample.read_text(encoding="utf-8") == "こちらの編集\n"


# ----------------------------------------------------------------------
# (a-3) 差分を出せないときのフォールバック
# ----------------------------------------------------------------------


def test_missing_file_falls_back_to_a_plain_message(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """ディスクから消えていたら、差分は出さずその旨だけ伝える。"""
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    prompt = patch_change_prompt(monkeypatch, EXTERNAL_CHANGE_OVERWRITE)
    fallback = patch_save_dialog(monkeypatch)

    # 見分け札は「在って、変わっている」のに、読む時点では消えている
    #（検知してから読むまでの間に消された、という競合状態）。
    change_externally(sample, "別の PC が書いた内容\n")
    monkeypatch.setattr(
        "editor_app.file_commands.read_text_file_detail",
        _raise(FileNotFoundError(sample)),
    )

    assert window.save_editor(editor) is False
    assert prompt.calls == []
    assert len(fallback.calls) == 1
    assert "見つかりません" in fallback.calls[0][1]


def test_unreadable_disk_content_falls_back_to_a_plain_message(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """文字コード判定に失敗する等で読めないときも、落ちずに従来の案内へ。"""
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    prompt = patch_change_prompt(monkeypatch, EXTERNAL_CHANGE_OVERWRITE)
    fallback = patch_save_dialog(monkeypatch)

    change_externally(sample, "別の PC が書いた内容\n")
    monkeypatch.setattr(
        "editor_app.file_commands.read_text_file_detail",
        _raise(ValueError("読めません")),
    )

    assert window.save_editor(editor) is False
    assert prompt.calls == []
    assert len(fallback.calls) == 1
    assert "外部で変更されています" in fallback.calls[0][1]
    # 踏み潰していない。
    assert sample.read_text(encoding="utf-8") == "別の PC が書いた内容\n"


# ----------------------------------------------------------------------
# (b) 変わっていなければ、従来どおり保存される（回帰）
# ----------------------------------------------------------------------


def test_save_succeeds_when_file_is_untouched(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    recorder = patch_save_dialog(monkeypatch)

    assert window.save_editor(editor) is True
    assert sample.read_text(encoding="utf-8") == "こちらの編集\n"
    assert editor.is_modified is False
    assert recorder.calls == []


def test_repeated_saves_keep_working(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """自分の保存を「外部変更」と誤認して 2 回目が止まらないこと。"""
    editor = window.open_path(sample)
    recorder = patch_save_dialog(monkeypatch)

    for i in range(3):
        edit_text(editor, f"{i} 回目\n")
        assert window.save_editor(editor) is True

    assert sample.read_text(encoding="utf-8") == "2 回目\n"
    assert recorder.calls == []


# ----------------------------------------------------------------------
# (c) 監視が先に検知・再読み込みしていたら、ダイアログは二重に出ない
# ----------------------------------------------------------------------


def test_no_second_dialog_after_watcher_reloaded(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    editor = window.open_path(sample)
    save_dialog = patch_save_dialog(monkeypatch)
    monkeypatch.setattr(
        "editor_app.file_watch.show_message_box",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )

    change_externally(sample, "別の PC が書いた内容\n")
    # 監視が拾って再読み込みした（＝利用者は「はい」を選んだ）。
    window._maybe_reload_changed_file(sample)
    assert editor.toPlainText() == "別の PC が書いた内容\n"

    # そのうえで保存する。既に取り込んである以上、改めて止める理由は無い。
    edit_text(editor, "取り込んだうえでの編集\n")
    assert window.save_editor(editor) is True
    assert save_dialog.calls == []


def test_no_dialog_when_watcher_saw_identical_content(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """中身が同じまま書き直された場合、監視が拾ったあとの保存も止めない。

    監視は「中身が本文と同じ」と分かった時点で確認を出さずに戻るが、
    そのときに控えを更新しておかないと、**保存直前のチェックだけが古い
    控えを握ったまま**になり、中身は同じなのに保存できなくなる。
    """
    editor = window.open_path(sample)
    save_dialog = patch_save_dialog(monkeypatch)
    monkeypatch.setattr(
        "editor_app.file_watch.show_message_box",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )

    # 中身は同じで更新日時だけ動く（rclone の再同期などで起こる）。
    touch_only(sample)
    window._maybe_reload_changed_file(sample)

    edit_text(editor, "こちらの編集\n")
    assert window.save_editor(editor) is True
    assert save_dialog.calls == []


# ----------------------------------------------------------------------
# (d) 終了フローの一括保存中に不一致 → 終了処理そのものを中断する
# ----------------------------------------------------------------------


def make_unsaved_window(make_window, tmp_path: Path, count: int):
    """保存先が決まっている未保存タブを ``count`` 個持つウィンドウを作る。"""
    win = make_window()
    paths = []
    for i in range(count):
        path = tmp_path / f"file{i}.txt"
        path.write_text("元の内容\n", encoding="utf-8")
        editor = win.open_path(path)
        assert editor is not None
        edit_text(editor, f"変更 {i}\n")
        paths.append(path)
    return win, paths


class BulkSaveRecorder:
    """終了時の確認で「保存」を選び、残りにも同じ操作を適用するモック。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        return QMessageBox.StandardButton.Save, True


def test_quit_is_aborted_when_a_tab_changed_externally(
    make_window, tmp_path: Path, monkeypatch
) -> None:
    """一括保存の途中で不一致に当たったら、終了そのものを取りやめる。

    そのタブの保存だけ諦めて残りを閉じてしまうと、利用者は「終了できた」と
    思ったまま編集内容を失う。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 3)
    monkeypatch.setattr(CLOSE_DIALOG, BulkSaveRecorder())
    prompt = patch_change_prompt(monkeypatch)  # 差分を見て「キャンセル」
    save_dialog = patch_save_dialog(monkeypatch)

    # 2 つめのタブのファイルだけ、外部で書き換わっている。
    change_externally(paths[1], "別の PC が書いた内容\n")

    assert win.close() is False
    assert win.isVisible() is True
    assert win in MainWindow.open_windows()
    assert prompt.calls  # 中止の理由は伝えてある
    assert save_dialog.calls == []

    # 踏み潰していない。
    assert paths[1].read_text(encoding="utf-8") == "別の PC が書いた内容\n"
    # 編集内容もタブも残っている（終了していない）。
    assert len(win.editors()) == len(paths)
    assert any(editor.is_modified for editor in win.editors())


def test_quit_completes_when_nothing_changed_externally(
    make_window, tmp_path: Path, monkeypatch
) -> None:
    """回帰。外部変更が無ければ、今までどおり一括保存して終了できる。"""
    win, paths = make_unsaved_window(make_window, tmp_path, 3)
    monkeypatch.setattr(CLOSE_DIALOG, BulkSaveRecorder())
    prompt = patch_change_prompt(monkeypatch)
    save_dialog = patch_save_dialog(monkeypatch)

    assert win.close() is True
    assert prompt.calls == []
    assert save_dialog.calls == []
    for i, path in enumerate(paths):
        assert path.read_text(encoding="utf-8") == f"変更 {i}\n"


@pytest.mark.parametrize(
    "choice, expected_on_disk",
    [
        (EXTERNAL_CHANGE_RELOAD, "別の PC が書いた内容\n"),
        (EXTERNAL_CHANGE_OVERWRITE, "変更 1\n"),
    ],
)
def test_quit_continues_when_the_tab_is_resolved(
    make_window, tmp_path: Path, monkeypatch, choice: str, expected_on_disk: str
) -> None:
    """終了の途中でも、「読み込む」「上書き保存する」を選べば終了は続く。

    行き止まりにしないのがこの機能の主眼なので、**選べば終了できる**ことまで
    確かめる（キャンセルのときだけ中断する）。
    """
    win, paths = make_unsaved_window(make_window, tmp_path, 3)
    monkeypatch.setattr(CLOSE_DIALOG, BulkSaveRecorder())
    prompt = patch_change_prompt(monkeypatch, choice)
    save_dialog = patch_save_dialog(monkeypatch)

    change_externally(paths[1], "別の PC が書いた内容\n")

    assert win.close() is True
    assert win not in MainWindow.open_windows()
    assert len(prompt.calls) == 1
    assert save_dialog.calls == []
    assert paths[1].read_text(encoding="utf-8") == expected_on_disk
    # 巻き込まれていない他のタブは、いつもどおり保存されている。
    assert paths[0].read_text(encoding="utf-8") == "変更 0\n"
    assert paths[2].read_text(encoding="utf-8") == "変更 2\n"


def test_closing_a_single_tab_is_aborted_too(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """タブ 1 つを閉じるときも、キャンセルされた以上は閉じない。"""
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    monkeypatch.setattr(
        "editor_app.main_window.show_message_box",
        lambda *a, **k: QMessageBox.StandardButton.Save,
    )
    patch_change_prompt(monkeypatch)
    patch_save_dialog(monkeypatch)

    change_externally(sample, "別の PC が書いた内容\n")

    assert window.close_editor(editor) is False
    assert editor in window.editors()
    assert sample.read_text(encoding="utf-8") == "別の PC が書いた内容\n"


def test_closing_a_single_tab_continues_after_reload(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """「ディスクの内容を読み込む」を選べば、そのタブは閉じてよい。

    読み直したタブに未保存の変更は残らないので、閉じても失うものは無い。
    """
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    monkeypatch.setattr(
        "editor_app.main_window.show_message_box",
        lambda *a, **k: QMessageBox.StandardButton.Save,
    )
    patch_change_prompt(monkeypatch, EXTERNAL_CHANGE_RELOAD)
    patch_save_dialog(monkeypatch)

    change_externally(sample, "別の PC が書いた内容\n")

    assert window.close_editor(editor) is True
    assert editor not in window.editors()
    assert sample.read_text(encoding="utf-8") == "別の PC が書いた内容\n"


# ----------------------------------------------------------------------
# (e) 保存が成功した直後の控えは、書いたあとの値になっている
# ----------------------------------------------------------------------


def test_signature_is_refreshed_after_successful_save(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    editor = window.open_path(sample)
    patch_save_dialog(monkeypatch)
    before = editor.disk_signature
    assert before == file_signature(sample)

    edit_text(editor, "だいぶ長さの違う内容にしておく\n")
    assert window.save_editor(editor) is True

    assert editor.disk_signature == file_signature(sample)
    assert editor.disk_signature != before


def test_signature_is_recorded_when_opening(
    window: MainWindow, sample: Path
) -> None:
    editor = window.open_path(sample)
    assert editor.disk_signature == file_signature(sample)


def test_signature_is_refreshed_after_reload(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    editor = window.open_path(sample)
    monkeypatch.setattr(
        "editor_app.file_watch.show_message_box",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )

    change_externally(sample, "別の PC が書いた内容\n")
    window._maybe_reload_changed_file(sample)

    assert editor.disk_signature == file_signature(sample)


# ----------------------------------------------------------------------
# 対象外にしている場面
# ----------------------------------------------------------------------


def test_untitled_first_save_is_not_blocked(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    """無題タブの初回保存には控えが無いので、止めない。"""
    editor = window.new_file()
    edit_text(editor, "新しい内容\n")
    recorder = patch_save_dialog(monkeypatch)

    target = tmp_path / "新規.txt"
    assert window._write_editor(editor, target) is True
    assert target.read_text(encoding="utf-8") == "新しい内容\n"
    assert recorder.calls == []
    assert editor.disk_signature == file_signature(target)


def test_save_is_not_blocked_when_there_is_no_baseline(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """控えが取れていないタブは、見比べる基準が無いので止めない。

    「無題タブの初回保存」は保存先がまだ無いので別の条件で通るが、
    **保存先は在るのに控えだけが無い**状態も作れる（控えを取ろうとした
    その瞬間にファイルが無かった場合）。基準が無い以上「変わった」とは
    言えないので、止めずに通す——止めてしまうと、そのタブは以後
    どうやっても保存できなくなる。
    """
    editor = window.open_path(sample)
    recorder = patch_save_dialog(monkeypatch)

    # 控えを取ろうとした時点でファイルが無かった、という状況を作る。
    sample.unlink()
    editor.remember_disk_state()
    assert editor.disk_signature is None

    # そのあとファイルが在る状態に戻る（別の PC が書き戻した等）。
    sample.write_text("誰かが書き戻した内容\n", encoding="utf-8")
    edit_text(editor, "こちらの編集\n")

    assert window.save_editor(editor) is True
    assert sample.read_text(encoding="utf-8") == "こちらの編集\n"
    assert recorder.calls == []


def test_save_as_to_another_path_is_not_blocked(
    window: MainWindow, sample: Path, tmp_path: Path, monkeypatch
) -> None:
    """別のファイルへの「名前を付けて保存」は、控えと比べても意味が無い。

    比べてしまうと、必ず不一致になって保存できなくなる。
    """
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    recorder = patch_save_dialog(monkeypatch)

    other = tmp_path / "別名.txt"
    other.write_text("先にあった内容\n", encoding="utf-8")

    assert window._write_editor(editor, other) is True
    assert other.read_text(encoding="utf-8") == "こちらの編集\n"
    assert recorder.calls == []
    # 保存先が移った以上、控えも新しい保存先のもの。
    assert editor.path == other
    assert editor.disk_signature == file_signature(other)


def test_save_is_not_blocked_when_file_was_deleted(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """外部で消されていた場合は、作り直すだけなので止めない。"""
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    recorder = patch_save_dialog(monkeypatch)

    sample.unlink()

    assert window.save_editor(editor) is True
    assert sample.read_text(encoding="utf-8") == "こちらの編集\n"
    assert recorder.calls == []


def test_save_with_encoding_is_checked_too(
    window: MainWindow, sample: Path, monkeypatch
) -> None:
    """「文字コードを指定して保存」も同じ書き込み口を通るので、同じく止まる。"""
    editor = window.open_path(sample)
    edit_text(editor, "こちらの編集\n")
    prompt = patch_change_prompt(monkeypatch)
    monkeypatch.setattr(
        "editor_app.file_commands.prompt_choice", lambda *a, **k: 0
    )

    change_externally(sample, "別の PC が書いた内容\n")

    assert window.save_editor_with_encoding(editor) is False
    assert sample.read_text(encoding="utf-8") == "別の PC が書いた内容\n"
    assert len(prompt.calls) == 1


def test_path_change_drops_the_old_signature(
    window: MainWindow, sample: Path, tmp_path: Path
) -> None:
    """保存先を差し替えたら、前のファイルの控えは持ち越さない。"""
    editor = window.open_path(sample)
    assert editor.disk_signature is not None

    editor.path = tmp_path / "よそのファイル.txt"
    assert editor.disk_signature is None
