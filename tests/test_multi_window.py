"""タブのウィンドウ間ドラッグ&ドロップ（複数ウィンドウ対応）のテスト。

「タブをウィンドウ外へ D&D したら新規ウィンドウになる」
「他ウィンドウへ D&D したらそのウィンドウへタブが移動する」の 2 つを、
低レベルのメソッド（``_tear_off_tab_to_new_window`` / ``_move_tab_to_window`` /
``accept_dropped_tab`` / ``finish_cross_window_drag``）を直接呼んで検証する。
ウィンドウ間の移動は Qt 標準のドラッグ&ドロップ（``QDrag``）で行っているが、
本物のドラッグはヘッドレスでは再現できないので、``MultiRowTabBar.set_drag_source()``
で「ドラッグ中」の状態だけを作って、受け手側のロジックを直接呼ぶ。
実際のマウスドラッグ操作は実機での確認に譲る（PROGRESS.md 参照）。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtWidgets import QMenu

from editor_app.main_window import MainWindow
from editor_app.tab_bar import MultiRowTabBar


def edit_text(editor, text: str) -> None:
    editor.selectAll()
    editor.insertPlainText(text)


def make_positioned_window(make_window, x: int, y: int) -> MainWindow:
    win = make_window()
    win.move(x, y)
    win.resize(800, 600)
    win.show()
    return win


# ----------------------------------------------------------------------
# ウィンドウの追跡（MainWindow.open_windows）
# ----------------------------------------------------------------------
def test_new_window_registers_itself(window: MainWindow) -> None:
    assert window in MainWindow.open_windows()


def test_closed_window_unregisters_itself(window: MainWindow) -> None:
    window.close()
    assert window not in MainWindow.open_windows()


# ----------------------------------------------------------------------
# タブの切り離し（新規ウィンドウ化）
# ----------------------------------------------------------------------
def test_tear_off_creates_new_window_with_the_tab(window: MainWindow) -> None:
    editor = window.current_editor()
    edit_text(editor, "切り離すタブの内容")
    editor.set_modified(False)

    new_window = window._tear_off_tab_to_new_window(editor, QPoint(300, 300))

    assert new_window is not None
    assert new_window is not window
    assert window.tabs.count() == 0
    assert new_window.tabs.count() == 1
    assert new_window.current_editor() is editor
    assert editor.toPlainText() == "切り離すタブの内容"


def test_tear_off_preserves_editor_state(window: MainWindow) -> None:
    """切り離してもタブの中身（テキスト・未保存状態・undo履歴）は保たれる。"""
    editor = window.current_editor()
    edit_text(editor, "abc")

    window._tear_off_tab_to_new_window(editor, QPoint(100, 100))

    assert editor.is_modified  # 未保存状態はそのまま
    assert editor.toPlainText() == "abc"
    editor.undo()
    assert editor.toPlainText() == ""  # undo 履歴も保たれている


def test_tear_off_updates_title_in_new_window_after_move(window: MainWindow) -> None:
    """切り離した後にタブ名を更新すると、新しいウィンドウの方に反映される
    （display_state_changed の接続が正しく張り替わっていること）。"""
    editor = window.current_editor()
    new_window = window._tear_off_tab_to_new_window(editor, QPoint(100, 100))

    edit_text(editor, "編集した")

    assert new_window.tabs.tabText(0).startswith("*")
    # 元のウィンドウにはもうこのタブは無いので、影響が残っていないこと。
    assert window.tabs.count() == 0


# ----------------------------------------------------------------------
# ウィンドウ間でのタブ移動
# ----------------------------------------------------------------------
def test_move_tab_to_window_transfers_editor(make_window) -> None:
    win_a = make_positioned_window(make_window, 0, 0)
    win_b = make_positioned_window(make_window, 2000, 0)

    editor = win_a.current_editor()
    edit_text(editor, "移動するタブ")

    win_a._move_tab_to_window(editor, win_b)

    assert win_a.tabs.count() == 0
    assert win_b.tabs.count() == 2  # win_b の起動時の無題タブ + 移動してきたタブ
    assert win_b.tabs.indexOf(editor) >= 0
    assert win_b.current_editor() is editor


def test_move_tab_to_window_updates_source_window_title(make_window) -> None:
    """タブを別ウィンドウへ移したら、元のウィンドウのタイトルも切り替わる。

    真ん中のタブを移すと、残ったタブの「番号」は変わらないまま中身だけが
    別の文書に変わる。以前はこの場合にタイトルが更新されず、移したはずの
    タブの名前が元のウィンドウのタイトルバーに残っていた。
    """
    win_a = make_positioned_window(make_window, 0, 0)
    win_b = make_positioned_window(make_window, 2000, 0)

    win_a.new_file()  # 無題-2（真ん中になる）
    third = win_a.new_file()  # 無題-3
    middle = win_a.editors()[1]
    win_a.tabs.setCurrentWidget(middle)
    assert win_a.windowTitle().startswith("無題-2")

    win_a._move_tab_to_window(middle, win_b)

    assert win_a.current_editor() is third
    assert win_a.windowTitle().startswith("無題-3")


def test_move_tab_to_window_rewires_title_updates(make_window) -> None:
    win_a = make_positioned_window(make_window, 0, 0)
    win_b = make_positioned_window(make_window, 2000, 0)

    editor = win_a.current_editor()
    win_a._move_tab_to_window(editor, win_b)

    edit_text(editor, "編集")

    index = win_b.tabs.indexOf(editor)
    assert win_b.tabs.tabText(index).startswith("*")


def drag_tab(source: MainWindow, editor) -> None:
    """実際のマウス操作なしに「このタブをドラッグ中」の状態を作る。"""
    MultiRowTabBar.set_drag_source(source.tabs.tabBar(), source.tabs.indexOf(editor))


def test_other_window_accepts_a_dragged_tab(make_window) -> None:
    """ドロップを受けたウィンドウが、ドラッグ中のタブを引き取る。"""
    win_a = make_positioned_window(make_window, 0, 0)
    win_b = make_positioned_window(make_window, 2000, 0)

    editor = win_a.current_editor()
    edit_text(editor, "移動するタブ")
    drag_tab(win_a, editor)

    assert win_b.can_accept_dropped_tab() is True
    assert win_b.accept_dropped_tab() is True

    assert win_b.tabs.indexOf(editor) >= 0
    assert win_b.current_editor() is editor
    assert editor.toPlainText() == "移動するタブ"
    assert win_a.tabs.count() == 0


def test_source_window_does_not_accept_its_own_tab(make_window) -> None:
    """自分のウィンドウへのドロップは受け取らない（＝切り離しになる）。"""
    win_a = make_positioned_window(make_window, 0, 0)
    editor = win_a.current_editor()
    drag_tab(win_a, editor)

    assert win_a.can_accept_dropped_tab() is False
    assert win_a.accept_dropped_tab() is False
    assert win_a.tabs.indexOf(editor) >= 0


def test_drop_is_refused_when_no_tab_is_being_dragged(window: MainWindow) -> None:
    MultiRowTabBar.set_drag_source(None)
    assert window.can_accept_dropped_tab() is False
    assert window.accept_dropped_tab() is False


def test_dropped_tab_does_not_close_source_window_during_the_drag(
    make_window,
) -> None:
    """ドラッグ中（QDrag.exec() の中）に元ウィンドウを閉じてしまわないこと。

    ドラッグ元のタブバーごと片付けられると危ないので、空になった
    ウィンドウを閉じるのはドラッグが終わってから
    （``_on_tab_drag_finished``）に限る。
    """
    win_a = make_positioned_window(make_window, 0, 0)
    win_b = make_positioned_window(make_window, 2000, 0)

    editor = win_a.current_editor()  # win_a には唯一のタブしか無い
    drag_tab(win_a, editor)
    win_b.accept_dropped_tab()

    assert win_a.tabs.count() == 0
    assert win_a in MainWindow.open_windows()  # まだ閉じていない


def test_drag_finished_closes_window_if_now_empty(make_window) -> None:
    """ドラッグが終わった時点でタブが 0 個なら、そのウィンドウは閉じる。"""
    win_a = make_positioned_window(make_window, 0, 0)
    win_b = make_positioned_window(make_window, 2000, 0)

    editor = win_a.current_editor()
    drag_tab(win_a, editor)
    win_b.accept_dropped_tab()
    MultiRowTabBar.set_drag_source(None)

    win_a.tabs.tabBar().finish_cross_window_drag(0, Qt.DropAction.MoveAction, QPoint())

    assert win_a not in MainWindow.open_windows()


def test_drag_finished_keeps_window_open_if_tabs_remain(make_window) -> None:
    """移動後も別のタブが残っていれば、元ウィンドウは閉じない。"""
    win_a = make_positioned_window(make_window, 0, 0)
    win_b = make_positioned_window(make_window, 2000, 0)
    win_a.new_file()  # win_a に 2 つ目のタブを作る

    editor = win_a.editors()[0]
    drag_tab(win_a, editor)
    win_b.accept_dropped_tab()
    MultiRowTabBar.set_drag_source(None)

    win_a.tabs.tabBar().finish_cross_window_drag(0, Qt.DropAction.MoveAction, QPoint())

    assert win_a in MainWindow.open_windows()
    assert win_a.tabs.count() == 1


def test_drag_dropped_nowhere_tears_off_to_new_window(window: MainWindow) -> None:
    """どのウィンドウにも受け取られなかった（IgnoreAction）ら切り離す。

    Wayland 対応で Qt 標準の D&D に作り替えたあとも、この従来の挙動
    （何も無い場所で離したら新しいウィンドウになる）は維持する。
    """
    editor = window.current_editor()
    edit_text(editor, "切り離される内容")

    window.tabs.tabBar().finish_cross_window_drag(
        0, Qt.DropAction.IgnoreAction, QPoint(300, 300)
    )

    others = [w for w in MainWindow.open_windows() if w is not window]
    assert len(others) == 1
    assert others[0].tabs.indexOf(editor) >= 0
    assert editor.toPlainText() == "切り離される内容"
    # 唯一のタブを切り離したので、元ウィンドウは自動的に閉じている。
    assert window not in MainWindow.open_windows()


def test_successful_drop_does_not_tear_off(make_window) -> None:
    """移動できたときは、新しいウィンドウを作らない。"""
    win_a = make_positioned_window(make_window, 0, 0)
    win_b = make_positioned_window(make_window, 2000, 0)
    win_a.new_file()

    editor = win_a.editors()[0]
    drag_tab(win_a, editor)
    win_b.accept_dropped_tab()
    MultiRowTabBar.set_drag_source(None)
    before = len(MainWindow.open_windows())

    win_a.tabs.tabBar().finish_cross_window_drag(0, Qt.DropAction.MoveAction, QPoint())

    assert len(MainWindow.open_windows()) == before


def test_closing_last_tab_closes_the_window(make_window) -> None:
    """最後の 1 つのタブを閉じると、空のウィンドウが残らずウィンドウごと閉じる
    （実機フィードバックにより追加。ドラッグでタブが 0 個になったときと同じ挙動）。
    """
    win = make_window()
    assert win.tabs.count() == 1

    win.close_tab(0)

    assert win.tabs.count() == 0
    assert win not in MainWindow.open_windows()


def test_closing_a_tab_keeps_window_open_if_tabs_remain(make_window) -> None:
    """まだタブが残っていれば、ウィンドウは閉じない。"""
    win = make_window()
    win.new_file()
    assert win.tabs.count() == 2

    win.close_tab(0)

    assert win.tabs.count() == 1
    assert win in MainWindow.open_windows()


# ----------------------------------------------------------------------
# ファイルのドラッグ&ドロップで開く
#
# ``QDropEvent`` をテストコードから直接組み立てると、PySide6 では
# ``QMimeData`` への参照が保持されずセグフォルトすることがある（実際に
# 発生を確認した）。そのため ``dropEvent`` 本体ではなく、そこから
# QDropEvent 抜きで呼べるように切り出した ``open_dropped_urls()`` を使う。
# ``dropEvent`` / ``dragEnterEvent`` 自体の実機での動作確認は
# PROGRESS.md 側の「実機での目視確認」に委ねる。
# ----------------------------------------------------------------------
def test_open_dropped_urls_single_file(window: MainWindow, tmp_path) -> None:
    path = tmp_path / "dropped.txt"
    path.write_text("D&Dで開いた内容\n", encoding="utf-8")

    window.open_dropped_urls([QUrl.fromLocalFile(str(path))])

    assert window.current_editor().toPlainText() == "D&Dで開いた内容\n"


def test_open_dropped_urls_multiple_files(window: MainWindow, tmp_path) -> None:
    path1 = tmp_path / "a.txt"
    path2 = tmp_path / "b.txt"
    path1.write_text("A\n", encoding="utf-8")
    path2.write_text("B\n", encoding="utf-8")

    window.open_dropped_urls(
        [QUrl.fromLocalFile(str(path1)), QUrl.fromLocalFile(str(path2))]
    )

    names = [e.display_name for e in window.editors()]
    assert "a.txt" in names
    assert "b.txt" in names


def test_open_dropped_urls_ignores_non_local_urls(window: MainWindow) -> None:
    before = window.tabs.count()
    opened = window.open_dropped_urls([QUrl("https://example.com/not-a-file.txt")])
    assert opened == []
    assert window.tabs.count() == before


# ----------------------------------------------------------------------
# タブのダブルクリックでの切り離し
# ----------------------------------------------------------------------
def test_tab_bar_double_click_emits_signal(window: MainWindow, qtbot) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QMouseEvent

    bar = window.tabs.tabBar()
    rect = bar.tabRect(0)
    center = rect.center()

    received: list[int] = []
    bar.tabDoubleClicked.connect(received.append)

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonDblClick,
        center,
        bar.mapToGlobal(center),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    bar.mouseDoubleClickEvent(event)

    assert received == [0]


def test_on_tab_double_clicked_tears_off_to_new_window(window: MainWindow) -> None:
    editor = window.current_editor()
    edit_text(editor, "ダブルクリックで切り離す")

    window._on_tab_double_clicked(0)

    others = [w for w in MainWindow.open_windows() if w is not window]
    assert len(others) == 1
    assert others[0].tabs.indexOf(editor) >= 0
    assert editor.toPlainText() == "ダブルクリックで切り離す"


def test_on_tab_double_clicked_closes_window_when_now_empty(
    window: MainWindow,
) -> None:
    window._on_tab_double_clicked(0)  # window の唯一のタブを切り離す
    assert window not in MainWindow.open_windows()


def test_on_tab_double_clicked_keeps_window_open_if_tabs_remain(
    window: MainWindow,
) -> None:
    window.new_file()
    window._on_tab_double_clicked(0)
    assert window in MainWindow.open_windows()
    assert window.tabs.count() == 1


# ----------------------------------------------------------------------
# タブの右クリックメニュー経由の移動・分離（Wayland でドラッグが効かない
# 場合の確実な手段。実機フィードバックにより追加）
# ----------------------------------------------------------------------
def test_move_tab_via_menu_moves_to_target_and_closes_empty_source(
    make_window,
) -> None:
    src = make_window()
    dest = make_window()
    editor = src.current_editor()  # src は唯一のタブ

    src._move_tab_via_menu(editor, dest)

    assert dest.tabs.indexOf(editor) >= 0
    assert src not in MainWindow.open_windows()  # 空になった src は閉じる


def test_move_tab_via_menu_keeps_source_open_if_tabs_remain(make_window) -> None:
    src = make_window()
    src.new_file()  # src に 2 タブ
    dest = make_window()
    editor = src.editors()[0]

    src._move_tab_via_menu(editor, dest)

    assert dest.tabs.indexOf(editor) >= 0
    assert src in MainWindow.open_windows()
    assert src.tabs.count() == 1


def test_tear_off_via_menu_creates_new_window(window: MainWindow) -> None:
    window.new_file()  # 2 タブにして、分離後も window が残るようにする
    editor = window.editors()[0]

    window._tear_off_via_menu(editor)

    others = [w for w in MainWindow.open_windows() if w is not window]
    assert len(others) == 1
    assert others[0].tabs.indexOf(editor) >= 0
    assert window.tabs.indexOf(editor) < 0


def test_tear_off_via_menu_closes_the_source_window_when_now_empty(
    window: MainWindow,
) -> None:
    """唯一のタブを分離したら、空になった元ウィンドウは閉じる
    （ダブルクリックでの切り離しと同じ後始末）。"""
    window._tear_off_via_menu(window.current_editor())

    assert window not in MainWindow.open_windows()


def test_window_menu_label_reflects_tab_count(window: MainWindow) -> None:
    label_one = MainWindow._window_menu_label(window)
    assert "「" in label_one and "ほか" not in label_one  # 1 タブなら件数は付けない

    window.new_file()
    label_many = MainWindow._window_menu_label(window)
    assert "ほか 2 タブ" in label_many


# ----------------------------------------------------------------------
# 右クリックメニューそのものの組み立て
#
# 上のテストは「メニューを選んだあとの処理」(_move_tab_via_menu /
# _tear_off_via_menu) を直接呼んでいるだけなので、**その処理をメニューに
# 結びつけている _on_tab_context_menu** は素通りしていた。ここが壊れると
# Wayland でタブを動かす唯一確実な手段が黙って消えるため、
# 「どの項目が出るか」「選ぶと本当にそのウィンドウへ動くか」を固定しておく。
# ----------------------------------------------------------------------
MOVE_MENU_TITLE = "別のウィンドウへ移動"
TEAR_OFF_ITEM_TITLE = "新しいウィンドウへ分離"


def build_tab_context_menu(win: MainWindow, index: int, monkeypatch) -> QMenu | None:
    """タブの右クリックメニューを、実際には開かずに組み立てさせて返す。

    ``_on_tab_context_menu`` は最後に ``menu.exec()`` を呼ぶ。ヘッドレスで
    本物のメニューを開くと誰も閉じられず固まるので、``exec`` したメニューを
    記録するだけの QMenu に差し替える。メニューが出なかった場合は None。

    サブメニュー（「別のウィンドウへ移動」）は ``menu.addMenu()`` の戻り値を
    ``RecordingMenu._submenus`` に控えてあるので、:func:`submenu_titled` で引く。

    **サブメニューを ``QAction.menu()`` から取らないこと。** PySide6 では
    一時オブジェクトの QAction からこれを呼ぶと、その QAction が捨てられる
    ときにサブメニューの C++ 側まで道連れに消され、あとから中身を見ようと
    すると「already deleted」で落ちる（アプリ側は ``QAction.menu()`` を
    使っていないので、これはテストの書き方だけの問題）。
    """
    from editor_app import tab_transfer

    opened: list[QMenu] = []

    class RecordingMenu(QMenu):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._submenus: list[QMenu] = []

        def addMenu(self, *args, **kwargs):  # noqa: N802 (Qt 側の名前に合わせる)
            submenu = super().addMenu(*args, **kwargs)
            self._submenus.append(submenu)
            return submenu

        def exec(self, *args, **kwargs):
            opened.append(self)
            return None

    monkeypatch.setattr(tab_transfer, "QMenu", RecordingMenu)
    win._on_tab_context_menu(index, QPoint(0, 0))
    return opened[0] if opened else None


def menu_item(menu: QMenu, text: str):
    """メニューの項目を表示名で引く。無ければ None。"""
    for action in menu.actions():
        if action.text() == text:
            return action
    return None


def submenu_titled(menu: QMenu, text: str) -> QMenu | None:
    """組み立て済みメニューのサブメニューを表示名で引く。無ければ None。"""
    for submenu in menu._submenus:
        if submenu.title() == text:
            return submenu
    return None


def test_tab_context_menu_offers_move_and_tear_off(make_window, monkeypatch) -> None:
    """他のウィンドウがあるときは「移動」サブメニューと「分離」が出る。"""
    src = make_window()
    dest = make_window()

    menu = build_tab_context_menu(src, 0, monkeypatch)

    assert menu_item(menu, MOVE_MENU_TITLE) is not None
    submenu = submenu_titled(menu, MOVE_MENU_TITLE)
    assert [a.text() for a in submenu.actions()] == [
        MainWindow._window_menu_label(dest)
    ]
    assert menu_item(menu, TEAR_OFF_ITEM_TITLE) is not None


def test_tab_context_menu_omits_move_when_there_is_nowhere_to_move(
    window: MainWindow, monkeypatch
) -> None:
    """ウィンドウが 1 つしか無いなら「移動」は出さない（分離だけ）。"""
    menu = build_tab_context_menu(window, 0, monkeypatch)

    assert menu_item(menu, MOVE_MENU_TITLE) is None
    assert menu_item(menu, TEAR_OFF_ITEM_TITLE) is not None


def test_tab_context_menu_move_targets_the_chosen_window(
    make_window, monkeypatch
) -> None:
    """「移動」の項目を選ぶと、**選んだそのウィンドウ**へ移る。

    移動先ごとの項目は 1 つの for で作っているので、束縛を間違えると
    （``t=target`` の既定引数を外すなど）全部が最後のウィンドウ行きになる。
    移動先を 2 つ用意して、1 つめを選んだときに 1 つめへ行くことを確かめる。
    """
    src = make_window()
    src.new_file()  # 2 タブにして、移動後も src が残るようにする
    first = make_window()
    second = make_window()
    editor = src.editors()[0]

    menu = build_tab_context_menu(src, src.tabs.indexOf(editor), monkeypatch)
    submenu = submenu_titled(menu, MOVE_MENU_TITLE)
    assert len(submenu.actions()) == 2
    to_first = submenu.actions()[0]
    assert to_first.text() == MainWindow._window_menu_label(first)
    to_first.trigger()

    assert first.tabs.indexOf(editor) >= 0
    assert second.tabs.indexOf(editor) < 0
    assert src.tabs.indexOf(editor) < 0


def test_tab_context_menu_tear_off_creates_new_window(
    window: MainWindow, monkeypatch
) -> None:
    """「新しいウィンドウへ分離」を選ぶと、そのタブが新しいウィンドウへ移る。"""
    window.new_file()  # 2 タブにして、分離後も window が残るようにする
    editor = window.editors()[0]
    edit_text(editor, "メニューから分離")

    menu = build_tab_context_menu(window, window.tabs.indexOf(editor), monkeypatch)
    tear_off = menu_item(menu, TEAR_OFF_ITEM_TITLE)
    tear_off.trigger()

    others = [w for w in MainWindow.open_windows() if w is not window]
    assert len(others) == 1
    assert others[0].tabs.indexOf(editor) >= 0
    assert editor.toPlainText() == "メニューから分離"


def test_tab_context_menu_ignores_an_index_without_an_editor(
    window: MainWindow, monkeypatch
) -> None:
    """タブの無い位置ではメニューを出さない。"""
    assert build_tab_context_menu(window, 99, monkeypatch) is None


# ----------------------------------------------------------------------
# ドラッグ中のイベントへの応答（_handle_tab_drag_event）
#
# 「自分のウィンドウの上では受け取らない」＝ ignore() することが、
# 「タブバーの外へドラッグして離したら新しいウィンドウ」を成り立たせている
# （ignore すると QDrag.exec() が IgnoreAction を返す）。ここが取り違うと
# 切り離しが効かなくなるので、受け取り側の判断を固定しておく。
# ----------------------------------------------------------------------
class FakeDragEvent:
    """``QDragMoveEvent`` / ``QDropEvent`` の代わり。

    テストから本物のドラッグイベントを組み立てると PySide6 が落ちることが
    あるため（``open_dropped_urls()`` と同じ理由）、``_handle_tab_drag_event``
    が使う 3 つのメソッドだけを持つ器で代用する。
    """

    def __init__(self) -> None:
        self.drop_action = None
        self.accepted: bool | None = None

    def setDropAction(self, action) -> None:  # noqa: N802 (Qt 側の名前に合わせる)
        self.drop_action = action

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.accepted = False


def test_drag_over_another_window_is_accepted_without_moving_yet(make_window) -> None:
    """他ウィンドウの上を通過している間は、受け取る意思だけ示して動かさない。"""
    win_a = make_positioned_window(make_window, 0, 0)
    win_b = make_positioned_window(make_window, 2000, 0)
    editor = win_a.current_editor()
    drag_tab(win_a, editor)

    event = FakeDragEvent()
    win_b._handle_tab_drag_event(event, drop=False)

    assert event.accepted is True
    assert event.drop_action == Qt.DropAction.MoveAction
    assert win_a.tabs.indexOf(editor) >= 0  # まだ移動していない
    assert win_b.tabs.indexOf(editor) < 0


def test_drop_on_another_window_moves_the_tab(make_window) -> None:
    """他ウィンドウの上で離したら、その場でタブが移る。"""
    win_a = make_positioned_window(make_window, 0, 0)
    win_b = make_positioned_window(make_window, 2000, 0)
    win_a.new_file()
    editor = win_a.editors()[0]
    drag_tab(win_a, editor)

    event = FakeDragEvent()
    win_b._handle_tab_drag_event(event, drop=True)

    assert event.accepted is True
    assert win_b.tabs.indexOf(editor) >= 0
    assert win_a.tabs.indexOf(editor) < 0


def test_drag_over_its_own_window_is_ignored(window: MainWindow) -> None:
    """自分のウィンドウの上では ignore する（＝離せば切り離しになる）。"""
    drag_tab(window, window.current_editor())

    event = FakeDragEvent()
    window._handle_tab_drag_event(event, drop=False)

    assert event.accepted is False
    assert event.drop_action is None


# ----------------------------------------------------------------------
# 受け取れない・動かせない場面（ガード）
#
# タブの位置が指すものが無い / そのウィンドウのタブではない場合に、
# 黙って何もしないこと。ここが素通りすると、他人のタブを取り上げたり
# 空のウィンドウを作ったりしかねない。
# ----------------------------------------------------------------------
def test_dragged_tab_ignores_a_tab_bar_outside_a_main_window(window, qtbot) -> None:
    """Tuxpad のウィンドウに属さないタブバーからのドラッグは対象外。"""
    stray_bar = MultiRowTabBar()
    qtbot.addWidget(stray_bar)
    stray_bar.addTab("よその画面")
    MultiRowTabBar.set_drag_source(stray_bar, 0)

    assert window.can_accept_dropped_tab() is False
    assert window.accept_dropped_tab() is False


def test_dragged_tab_ignores_an_index_without_an_editor(make_window) -> None:
    """ドラッグ元の位置にタブが無ければ対象外。"""
    win_a = make_window()
    win_b = make_window()
    MultiRowTabBar.set_drag_source(win_a.tabs.tabBar(), 99)

    assert win_b.can_accept_dropped_tab() is False
    assert win_b.accept_dropped_tab() is False


def test_tear_off_request_ignores_an_index_without_an_editor(
    window: MainWindow,
) -> None:
    before = len(MainWindow.open_windows())

    window._on_tab_tear_off_requested(99, QPoint(300, 300))

    assert len(MainWindow.open_windows()) == before
    assert window.tabs.count() == 1


def test_double_click_ignores_an_index_without_an_editor(window: MainWindow) -> None:
    before = len(MainWindow.open_windows())

    window._on_tab_double_clicked(99)

    assert len(MainWindow.open_windows()) == before
    assert window.tabs.count() == 1


def test_move_tab_to_window_ignores_an_editor_from_another_window(
    make_window,
) -> None:
    """自分が持っていないタブは動かさない（渡し間違いで奪わない）。"""
    win_a = make_window()
    win_b = make_window()
    foreign = win_b.current_editor()

    win_a._move_tab_to_window(foreign, win_a)

    assert win_b.tabs.indexOf(foreign) >= 0
    assert win_a.tabs.indexOf(foreign) < 0


def test_tear_off_returns_none_for_an_editor_from_another_window(make_window) -> None:
    """自分が持っていないタブでは、新しいウィンドウを作らない。"""
    win_a = make_window()
    win_b = make_window()
    before = len(MainWindow.open_windows())

    assert win_a._tear_off_tab_to_new_window(win_b.current_editor(), QPoint(0, 0)) is None
    assert len(MainWindow.open_windows()) == before
