"""ドラッグ&ドロップの「受け取り口」のテスト.

ここで見るのは ``dragEnterEvent`` / ``dragMoveEvent`` / ``dropEvent`` の
**振り分け**そのもの（``MainWindow`` と ``EditorWidget`` の両方）。

これまでのテストは、落ちる危険を避けるために振り分けの**先**
（``open_dropped_urls()`` / ``_handle_tab_drag_event()``）だけを直接呼んでいた。
そのため「ファイルマネージャからファイルを落としたら開く」「本文の上に
落としてもパスの文字列が貼り付かない」「他ウィンドウのタブを本文の上で
離してもウィンドウ側が引き取る」といった**入り口の判断**は、誰も見ていない
状態だった（実機で目視するしかなかった）。ここを埋める。

**本物のドラッグイベントを組み立てるときの注意（過去に落ちた原因）**:
``QDragEnterEvent`` / ``QDropEvent`` は渡された ``QMimeData`` を**持ち主として
抱えない**（ポインタを覚えるだけ）。Python 側で参照が消えると ``QMimeData``
が回収され、イベントが解放済みメモリを指してセグフォルトする。
そのため、ここでは :func:`drop_event` などのヘルパーで

* ``QMimeData`` をイベントより長生きさせる（イベントと一緒に返して、
  呼び出し側の変数に残す）

という形にしてある。イベントだけを受け取って mime を捨てる書き方をすると、
テストがランダムに落ちるようになるので注意。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QMessageBox

from editor_app.main_window import MainWindow
from editor_app.tab_bar import TAB_MIME_TYPE, MultiRowTabBar

BUTTONS = Qt.MouseButton.LeftButton
NO_MODIFIER = Qt.KeyboardModifier.NoModifier


# ----------------------------------------------------------------------
# ヘルパー
#
# いずれも (イベント, mime) の組で返す。**mime を呼び出し側の変数で
# 受け止めて、イベントを使い終わるまで生かしておくこと**（冒頭の注意）。
# ----------------------------------------------------------------------
def file_mime(*paths: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def tab_mime() -> QMimeData:
    """タブのウィンドウ間ドラッグで積まれる目印（``tab_bar.py`` と同じ）。"""
    mime = QMimeData()
    mime.setData(TAB_MIME_TYPE, b"tuxpad-tab")
    return mime


def text_mime(text: str = "ただのテキスト") -> QMimeData:
    mime = QMimeData()
    mime.setText(text)
    return mime


def enter_event(mime: QMimeData) -> QDragEnterEvent:
    return QDragEnterEvent(
        QPoint(10, 10), Qt.DropAction.CopyAction, mime, BUTTONS, NO_MODIFIER
    )


def move_event(mime: QMimeData) -> QDragMoveEvent:
    return QDragMoveEvent(
        QPoint(10, 10), Qt.DropAction.CopyAction, mime, BUTTONS, NO_MODIFIER
    )


def drop_event(mime: QMimeData) -> QDropEvent:
    return QDropEvent(
        QPointF(10, 10), Qt.DropAction.CopyAction, mime, BUTTONS, NO_MODIFIER
    )


def written(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# ウィンドウへファイルを落とす
#
# ``dragEnterEvent`` と ``dragMoveEvent`` の**両方**で受け取る意思を示さないと、
# Qt は ``dropEvent`` まで運んでくれない（移動中に一度でも断ると、そこで
# 「受け取らない」に確定してカーソルが禁止マークになる）。ファイル
# マネージャからの D&D が「何も起きない」に化ける典型なので、3 つとも見る。
# ----------------------------------------------------------------------
def test_window_accepts_dragged_files_on_enter(window: MainWindow, tmp_path) -> None:
    mime = file_mime(written(tmp_path, "a.txt", "A\n"))
    event = enter_event(mime)

    window.dragEnterEvent(event)

    assert event.isAccepted() is True


def test_window_keeps_accepting_files_while_moving(window: MainWindow, tmp_path) -> None:
    mime = file_mime(written(tmp_path, "a.txt", "A\n"))
    event = move_event(mime)

    window.dragMoveEvent(event)

    assert event.isAccepted() is True


def test_dropping_a_file_on_the_window_opens_it(window: MainWindow, tmp_path) -> None:
    path = written(tmp_path, "落としたファイル.txt", "落として開いた内容\n")
    mime = file_mime(path)
    event = drop_event(mime)

    window.dropEvent(event)

    assert event.isAccepted() is True
    assert window.current_editor().toPlainText() == "落として開いた内容\n"


def test_dropping_several_files_opens_all_of_them(window: MainWindow, tmp_path) -> None:
    first = written(tmp_path, "one.txt", "1\n")
    second = written(tmp_path, "two.txt", "2\n")
    mime = file_mime(first, second)
    event = drop_event(mime)

    window.dropEvent(event)

    names = [editor.display_name for editor in window.editors()]
    assert "one.txt" in names
    assert "two.txt" in names


def test_window_ignores_a_drop_with_no_local_file(window: MainWindow) -> None:
    """ブラウザからのリンク等（ローカルファイルでない URL）は受け取らない。

    ここで受け取ってしまうと、落とした側には「渡した」と見えるのに
    何も開かない（＝落としたつもりのものが消えたように見える）。
    """
    before = window.tabs.count()
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/not-a-file.txt")])
    event = drop_event(mime)

    window.dropEvent(event)

    assert event.isAccepted() is False
    assert window.tabs.count() == before


def test_window_does_not_claim_plain_text_drags(window: MainWindow) -> None:
    """URL でもタブでもないもの（選択テキスト等）は Qt の既定に任せる。"""
    mime = text_mime()
    enter = enter_event(mime)
    move = move_event(mime)

    window.dragEnterEvent(enter)
    window.dragMoveEvent(move)

    assert enter.isAccepted() is False
    assert move.isAccepted() is False


# ----------------------------------------------------------------------
# ウィンドウへ他ウィンドウのタブを落とす
#
# タブの目印が付いているときは、ファイルの処理へ回してはいけない
# （mime には URL が無いので「何も開かない」になり、タブも移らない）。
# ----------------------------------------------------------------------
def test_window_accepts_a_tab_dragged_from_another_window(make_window) -> None:
    source = make_window()
    target = make_window()
    editor = source.current_editor()
    MultiRowTabBar.set_drag_source(source.tabs.tabBar(), source.tabs.indexOf(editor))

    mime = tab_mime()
    enter = enter_event(mime)
    move = move_event(mime)

    target.dragEnterEvent(enter)
    target.dragMoveEvent(move)

    assert enter.isAccepted() is True
    assert move.isAccepted() is True
    # 通過しただけでは、まだ移さない。
    assert source.tabs.indexOf(editor) >= 0


def test_dropping_a_tab_on_another_window_moves_it(make_window) -> None:
    source = make_window()
    target = make_window()
    source.new_file()
    editor = source.editors()[0]
    MultiRowTabBar.set_drag_source(source.tabs.tabBar(), source.tabs.indexOf(editor))

    mime = tab_mime()
    event = drop_event(mime)

    target.dropEvent(event)

    assert event.isAccepted() is True
    assert target.tabs.indexOf(editor) >= 0
    assert source.tabs.indexOf(editor) < 0


def test_window_ignores_its_own_tab_being_dragged_over_it(window: MainWindow) -> None:
    """自分のウィンドウの上では断る（＝離せば新しいウィンドウへ切り離し）。"""
    editor = window.current_editor()
    MultiRowTabBar.set_drag_source(window.tabs.tabBar(), window.tabs.indexOf(editor))

    mime = tab_mime()
    enter = enter_event(mime)

    window.dragEnterEvent(enter)

    assert enter.isAccepted() is False


# ----------------------------------------------------------------------
# 本文（エディタ）の上へ落とす
#
# 本文は ``QPlainTextEdit`` なので、既定では「落とされた物を文字として
# 貼り付ける」。ファイルを落としたときにパス文字列が本文へ入ってしまうと、
# 気づかないまま保存して実際のファイルを壊しかねない。
# ----------------------------------------------------------------------
def test_editor_accepts_dragged_files(window: MainWindow, tmp_path) -> None:
    editor = window.current_editor()
    mime = file_mime(written(tmp_path, "a.txt", "A\n"))
    event = enter_event(mime)

    editor.dragEnterEvent(event)

    assert event.isAccepted() is True


def test_dropping_a_file_on_the_text_area_opens_it_without_pasting(
    window: MainWindow, tmp_path
) -> None:
    editor = window.current_editor()
    editor.setPlainText("元からある本文\n")
    path = written(tmp_path, "落としたファイル.txt", "別ファイルの内容\n")
    mime = file_mime(path)
    event = drop_event(mime)

    editor.dropEvent(event)

    assert event.isAccepted() is True
    # 本文はそのまま。ファイルは別タブで開く。
    assert editor.toPlainText() == "元からある本文\n"
    opened = [e for e in window.editors() if e.display_name == "落としたファイル.txt"]
    assert len(opened) == 1
    assert opened[0].toPlainText() == "別ファイルの内容\n"


def test_editor_leaves_tab_drags_to_the_window(window: MainWindow) -> None:
    """本文の上を通っても、タブのドラッグは本文が横取りしない。

    横取りすると、ウィンドウ側の ``dropEvent`` へ届かず、タブが移らない
    （本文の広さの分だけ「落としても効かない場所」ができる）。

    ここでは **``accept()`` 済みのイベントを渡す**。実機で Qt が本文へ
    イベントを届けるときは、既にウィンドウ側が「受け取る」と答えた後
    （＝受理状態）で渡ってくるためで、本文が**自分から断らない限り**
    受理されたままウィンドウへ戻らない。素の（未受理の）イベントで試すと、
    ``QTextEdit`` の既定動作でもたまたま受理されないため、
    「断り忘れ」を見逃してしまう（実際に、この書き方でないと
    ``dropEvent`` の ``ignore()`` を消しても赤くならなかった）。
    """
    editor = window.current_editor()
    editor.setPlainText("元からある本文\n")
    mime = tab_mime()
    enter = enter_event(mime)
    move = move_event(mime)
    drop = drop_event(mime)
    for event in (enter, move, drop):
        event.accept()

    editor.dragEnterEvent(enter)
    editor.dragMoveEvent(move)
    editor.dropEvent(drop)

    assert enter.isAccepted() is False
    assert move.isAccepted() is False
    assert drop.isAccepted() is False
    # 本文にも何も入っていない。
    assert editor.toPlainText() == "元からある本文\n"


def test_editor_still_accepts_plain_text_drops(window: MainWindow) -> None:
    """文字のドラッグ&ドロップ（本文の編集）は今まで通り効く。"""
    editor = window.current_editor()
    editor.setPlainText("")
    mime = text_mime("落とした文字")
    enter = enter_event(mime)
    move = move_event(mime)
    drop = drop_event(mime)

    editor.dragEnterEvent(enter)
    editor.dragMoveEvent(move)
    editor.dropEvent(drop)

    # 移動中に断られると、本文への文字の D&D がその場でできなくなる。
    assert move.isAccepted() is True
    assert "落とした文字" in editor.toPlainText()


def test_editor_ignores_a_drop_with_no_local_file(window: MainWindow) -> None:
    """ローカルファイルでない URL は「ファイルを開く」に回さない。"""
    editor = window.current_editor()
    editor.setPlainText("")
    before = window.tabs.count()
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/not-a-file.txt")])
    event = drop_event(mime)

    editor.dropEvent(event)

    assert window.tabs.count() == before


# ----------------------------------------------------------------------
# 閉じるときのガード（範囲外の位置・別ウィンドウのタブ）
#
# タブの × は「押した時点の位置」で呼ばれるので、その間に並びが変わると
# 存在しない位置を指しうる。落ちずに何もしないことを固定しておく。
# ----------------------------------------------------------------------
@pytest.mark.parametrize("index", [-1, 99])
def test_closing_a_nonexistent_tab_does_nothing(window: MainWindow, index: int) -> None:
    before = window.tabs.count()

    assert window.close_tab(index) is False
    assert window.tabs.count() == before


def test_closing_an_editor_of_another_window_does_nothing(make_window) -> None:
    """よそのウィンドウのタブを閉じろと言われても、勝手に閉じない。"""
    owner = make_window()
    other = make_window()
    editor = owner.current_editor()

    assert other.close_editor(editor) is False
    assert owner.tabs.indexOf(editor) >= 0


# ----------------------------------------------------------------------
# 保存確認ダイアログを × で閉じたとき
#
# 「保存」でも「破棄」でもない答え（ダイアログの × / Esc）は、中止として
# 扱わないと**未保存の内容が黙って消える**。ここは実データが失われる側の
# 間違いなので、明示的に固定しておく。
# ----------------------------------------------------------------------
def test_dismissing_the_save_dialog_keeps_the_window_open(
    make_window, tmp_path, monkeypatch
) -> None:
    win = make_window()
    path = written(tmp_path, "編集中.txt", "元の内容\n")
    editor = win.open_path(path)
    assert editor is not None
    editor.selectAll()
    editor.insertPlainText("保存していない変更\n")
    assert editor.is_modified is True

    # × で閉じた場合、Qt は「押されていないボタン」を返す。
    monkeypatch.setattr(
        "editor_app.main_window.show_message_box_with_checkbox",
        lambda *args, **kwargs: (QMessageBox.StandardButton.NoButton, False),
    )

    assert win.close() is False
    assert win.isVisible() is True
    assert win in MainWindow.open_windows()
    assert editor.toPlainText() == "保存していない変更\n"
    assert path.read_text(encoding="utf-8") == "元の内容\n"


# ----------------------------------------------------------------------
# 最後にアクティブだったウィンドウ
# ----------------------------------------------------------------------
def test_most_recently_active_window_is_none_when_no_window_is_open() -> None:
    """1 つも開いていなければ None（転送先を探すときの入り口。

    ここが落ちると、起動中のインスタンスへファイルを転送する経路が
    そのまま落ちる）。
    """
    for win in MainWindow.open_windows():
        for editor in win.editors():
            editor.set_modified(False)
        win.close()
    assert MainWindow.open_windows() == []

    assert MainWindow.most_recently_active_window() is None
