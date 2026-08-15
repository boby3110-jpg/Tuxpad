"""タブをウィンドウ間で移動・分離するためのミックスイン.

``MainWindow`` から「タブを別のウィンドウへ移す／新しいウィンドウへ切り離す」
だけを切り出したもの（`view_settings.py` / `file_watch.py` / `search_replace.py`
と同じ切り出し方）。**タブのウィンドウ間移動を触るときは、`main_window.py`
ではなくこのモジュールに書くこと。**

タブを動かす経路は 3 つあり、いずれも最後は
:meth:`TabTransferMixin._move_tab_to_window`（別ウィンドウへ移す）か
:meth:`TabTransferMixin._tear_off_tab_to_new_window`（新しいウィンドウを
作って移す）のどちらかに合流する。

1. **ドラッグ&ドロップ** … 他ウィンドウの上で離せば移動
   (:meth:`accept_dropped_tab`)、どのウィンドウでもない場所で離せば切り離し
   (:meth:`_on_tab_tear_off_requested`)
2. **タブのダブルクリック** … 新しいウィンドウへ切り離し
   (:meth:`_on_tab_double_clicked`)
3. **タブの右クリックメニュー** … 「別のウィンドウへ移動」「新しいウィンドウ
   へ分離」(:meth:`_on_tab_context_menu`)

**このモジュールを触るときの注意**（過去に実機で不具合として出たもの）:

- **ドラッグ中（``QDrag.exec()`` の入れ子ループの中）にウィンドウを閉じない
  こと。** ドラッグ元のタブバーごと片付けられてしまう。空になったウィンドウ
  を閉じるのは、ドラッグが終わってからの :meth:`_on_tab_drag_finished`。
- **Wayland では、ウィンドウの外へ落としても ``QDrag.exec()`` が
  ``IgnoreAction`` を返さない。** 「引き取られたかどうか」は戻り値ではなく
  :meth:`MultiRowTabBar.mark_drag_accepted` の明示フラグで判断すること。
- 右クリックメニューからの移動・分離は、ドラッグが座標の制約で効かない環境
  （Wayland）でも確実に動く逃げ道として残してある。消さないこと。

``MainWindow`` にミックスインとして混ぜて使う。``self.tabs``・
``self.editors()``・``self.current_editor()``・``self._adopt_editor()``・
``self._on_tab_set_changed()``・``self.open_windows()`` に依存する。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QMenu

from .editor import EditorWidget
from .tab_bar import MultiRowTabBar


class TabTransferMixin:
    """タブのウィンドウ間移動・切り離しを受け持つミックスイン."""

    # ------------------------------------------------------------------
    # タブのウィンドウ間ドラッグ&ドロップ（複数ウィンドウ対応）
    # ------------------------------------------------------------------
    def _on_tab_tear_off_requested(self, index: int, global_pos: QPoint) -> None:
        """ドラッグしたタブがどのウィンドウにも受け取られなかった
        （＝何も無い場所で離された）ので、新しいウィンドウへ切り離す。

        タブが 0 個になった場合の後始末は :meth:`_on_tab_drag_finished` が行う。
        """
        editor = self.tabs.widget(index)
        if not isinstance(editor, EditorWidget):
            return
        self._tear_off_tab_to_new_window(editor, global_pos)

    def _on_tab_drag_finished(self) -> None:
        """ウィンドウ間ドラッグが終わった。タブが無くなっていたら閉じる。

        ドラッグ中（``QDrag.exec()`` の入れ子ループの中）にウィンドウを閉じると、
        ドラッグ元のタブバーごと片付けられてしまい危ないので、必ずドラッグが
        終わってからこのハンドラで閉じる。
        """
        if self.tabs.count() == 0:
            self.close()

    # -- ドロップ側（他のウィンドウからタブを受け取る） -------------------
    @classmethod
    def _dragged_tab(cls) -> "tuple[TabTransferMixin, EditorWidget] | None":
        """いまドラッグ中のタブ (元のウィンドウ, エディタ)。無ければ None。"""
        source = MultiRowTabBar.drag_source()
        if source is None:
            return None
        bar, index = source
        window = bar.window()
        # このミックスインを持つウィンドウ（＝ MainWindow）でなければ対象外。
        # ここで MainWindow を直接 import すると循環 import になるため、
        # ミックスイン自身で判定している。
        if not isinstance(window, TabTransferMixin):
            return None
        editor = window.tabs.widget(index)
        if not isinstance(editor, EditorWidget):
            return None
        return window, editor

    def can_accept_dropped_tab(self) -> bool:
        """このウィンドウが、ドラッグ中のタブを受け取れるか。

        自分自身のウィンドウへのドロップは受け取らない。従来どおり
        「タブバーの外へドラッグして離したら新しいウィンドウへ切り離し」に
        なるようにするため（``IgnoreAction`` で返る）。
        """
        dragged = self._dragged_tab()
        return dragged is not None and dragged[0] is not self

    def accept_dropped_tab(self) -> bool:
        """ドラッグ中のタブをこのウィンドウへ引き取る。受け取れたら True。

        ``QDropEvent`` に依存しない形にしてあるのは、``open_dropped_urls()``
        と同じ理由（PySide6 ではテストから ``QDropEvent`` を組み立てると
        落ちることがある）。
        """
        if not self.can_accept_dropped_tab():
            return False
        source, editor = self._dragged_tab()
        source._move_tab_to_window(editor, self)
        # 引き取れたことを記録する。これが立っていないと、ドラッグ終了後に
        # 「切り離し（新しいウィンドウ）」と判断される（Wayland 対策）。
        MultiRowTabBar.mark_drag_accepted()
        # 空になったドラッグ元を閉じるのはドラッグが終わってから
        # （_on_tab_drag_finished）。ここはまだ QDrag.exec() の中。
        return True

    def _handle_tab_drag_event(self, event, drop: bool) -> None:
        """タブのドラッグ（独自 MIME）に対する応答。

        受け取れないとき（自分自身のウィンドウ）は ``ignore()`` する。すると
        ``QDrag.exec()`` が ``IgnoreAction`` を返すので、従来どおり
        「新しいウィンドウへ切り離し」になる。
        """
        accepted = self.accept_dropped_tab() if drop else self.can_accept_dropped_tab()
        if accepted:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def _on_tab_double_clicked(self, index: int) -> None:
        """タブのダブルクリックで、そのタブを新しいウィンドウへ切り離す。"""
        editor = self.tabs.widget(index)
        if not isinstance(editor, EditorWidget):
            return
        # 現在のウィンドウから少し右下にずらした位置に出す。
        # _tear_off_tab_to_new_window は「渡した座標から左上寄りに配置する」
        # ので、その分を足し戻して逆算する。
        target_top_left = QPoint(self.x() + 60, self.y() + 60)
        self._tear_off_tab_to_new_window(editor, target_top_left + QPoint(80, 20))

        if self.tabs.count() == 0:
            self.close()

    def _move_tab_to_window(
        self, editor: EditorWidget, target: "TabTransferMixin"
    ) -> None:
        """タブ（とその中身）を、このウィンドウから別の Tuxpad ウィンドウへ移す。"""
        index = self.tabs.indexOf(editor)
        if index < 0:
            return
        self.tabs.removeTab(index)
        self._on_tab_set_changed()
        target._adopt_editor(editor)
        target.tabs.setCurrentWidget(editor)
        target.raise_()
        target.activateWindow()

    def _tear_off_tab_to_new_window(
        self, editor: EditorWidget, global_pos: QPoint
    ) -> "TabTransferMixin | None":
        """タブを、このウィンドウから切り離して新しいウィンドウとして表示する。"""
        index = self.tabs.indexOf(editor)
        if index < 0:
            return None
        self.tabs.removeTab(index)
        self._on_tab_set_changed()

        # 循環 import を避けるため、MainWindow を名指しせず自分と同じ型を作る。
        new_window = type(self)()
        # 新規ウィンドウは起動時に空の無題タブを 1 つ作るので、まずそれを消す。
        for stray in new_window.editors():
            stray_index = new_window.tabs.indexOf(stray)
            new_window.tabs.removeTab(stray_index)
            stray.deleteLater()

        new_window._adopt_editor(editor)
        new_window.tabs.setCurrentWidget(editor)
        # ドロップした位置の近くに出す（カーソルの少し左上をウィンドウの左上にする）。
        new_window.move(max(0, global_pos.x() - 80), max(0, global_pos.y() - 20))
        new_window.show()
        return new_window

    # ------------------------------------------------------------------
    # タブの右クリックメニュー（実機フィードバックにより追加）
    #
    # Wayland ではドラッグでのウィンドウ間移動が座標の制約で効かないことが
    # あるため、確実に効く移動手段としてメニューからも移動・分離できるように
    # してある。
    # ------------------------------------------------------------------
    def _on_tab_context_menu(self, index: int, global_pos: QPoint) -> None:
        editor = self.tabs.widget(index)
        if not isinstance(editor, EditorWidget):
            return

        menu = QMenu(self)
        others = [w for w in self.open_windows() if w is not self]
        if others:
            move_menu = menu.addMenu("別のウィンドウへ移動")
            for target in others:
                action = move_menu.addAction(self._window_menu_label(target))
                action.triggered.connect(
                    lambda _checked=False, t=target, e=editor: self._move_tab_via_menu(e, t)
                )
        tear = menu.addAction("新しいウィンドウへ分離")
        tear.triggered.connect(
            lambda _checked=False, e=editor: self._tear_off_via_menu(e)
        )
        menu.exec(global_pos)

    @staticmethod
    def _window_menu_label(window: "TabTransferMixin") -> str:
        """「別のウィンドウへ移動」メニューに出す、そのウィンドウの見分け方。"""
        editor = window.current_editor()
        name = editor.display_name if editor is not None else "（空）"
        count = window.tabs.count()
        return f"「{name}」ほか {count} タブ" if count > 1 else f"「{name}」"

    def _move_tab_via_menu(
        self, editor: EditorWidget, target: "TabTransferMixin"
    ) -> None:
        """メニューからの「別のウィンドウへ移動」。移動後に空になったら閉じる。"""
        self._move_tab_to_window(editor, target)
        if self.tabs.count() == 0:
            self.close()

    def _tear_off_via_menu(self, editor: EditorWidget) -> None:
        """メニューからの「新しいウィンドウへ分離」。分離後に空になったら閉じる。"""
        # 現在のウィンドウから少し右下にずらした位置に出す
        # （_on_tab_double_clicked と同じ考え方）。
        target_top_left = QPoint(self.x() + 60, self.y() + 60)
        self._tear_off_tab_to_new_window(editor, target_top_left + QPoint(80, 20))
        if self.tabs.count() == 0:
            self.close()
