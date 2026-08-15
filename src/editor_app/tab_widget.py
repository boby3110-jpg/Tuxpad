"""多段タブバー + ページ切り替えを組み合わせたタブウィジェット.

``QTabWidget`` の代わりに使う。``MainWindow`` からの呼び出しを変えずに済むよう、
``QTabWidget`` と同じ名前・同じ意味のメソッドとシグナルを用意してある。

タブの見た目と操作は :class:`~editor_app.tab_bar.MultiRowTabBar` が、
ページの保持と切り替えは ``QStackedWidget`` が担当する。両者の索引が
ずれないよう、構造を変える操作はすべてこのクラスを通す。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Signal
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from .tab_bar import MultiRowTabBar


class MultiRowTabWidget(QWidget):
    """タブが複数段に折り返される QTabWidget 互換のウィジェット。"""

    #: 表示中のページが変わった（タブが無くなったときは -1）
    currentChanged = Signal(int)

    #: タブを閉じるよう要求された（× / 中クリック）
    tabCloseRequested = Signal(int)

    #: ドラッグでタブが並べ替えられた (移動元, 移動先)
    tabMoved = Signal(int, int)

    #: ドラッグしたタブがどのウィンドウにも受け取られなかった
    #: (タブの位置, 離した位置のグローバル座標)。新しいウィンドウへ切り離す。
    tabTearOffRequested = Signal(int, QPoint)

    #: ウィンドウ間ドラッグが終わった（移動できた／できなかったに関わらず）。
    tabDragFinished = Signal()

    #: タブがダブルクリックされた（新しいウィンドウへの切り離しに使う）。
    tabDoubleClicked = Signal(int)

    #: タブが右クリックされた (タブの位置, 表示位置のグローバル座標)。
    tabContextMenuRequested = Signal(int, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._bar = MultiRowTabBar(self)
        self._stack = QStackedWidget(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._bar)
        layout.addWidget(self._stack, 1)

        self._bar.currentChanged.connect(self._on_bar_current_changed)
        self._bar.tabCloseRequested.connect(self.tabCloseRequested)
        self._bar.tabMoveRequested.connect(self.moveTab)
        self._bar.tabTearOffRequested.connect(self.tabTearOffRequested)
        self._bar.tabDragFinished.connect(self.tabDragFinished)
        self._bar.tabDoubleClicked.connect(self.tabDoubleClicked)
        self._bar.tabContextMenuRequested.connect(self.tabContextMenuRequested)

    # ------------------------------------------------------------------
    # 内部の同期
    # ------------------------------------------------------------------
    def _on_bar_current_changed(self, index: int) -> None:
        self._sync_stack()
        self.currentChanged.emit(index)

    def _sync_stack(self) -> None:
        """スタックの表示ページをタブバーの選択に合わせる。"""
        index = self._bar.currentIndex()
        if 0 <= index < self._stack.count():
            self._stack.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # タブの追加・削除・並べ替え
    # ------------------------------------------------------------------
    def addTab(self, widget: QWidget, text: str) -> int:  # noqa: N802
        return self.insertTab(self._stack.count(), widget, text)

    def insertTab(self, index: int, widget: QWidget, text: str) -> int:  # noqa: N802
        index = max(0, min(index, self._stack.count()))
        # ページを先に入れておく。タブバーが選択を変えたときに
        # すぐスタックを同期できるようにするため。
        self._stack.insertWidget(index, widget)
        self._bar.insertTab(index, text)
        if self._bar.currentIndex() < 0:
            self._bar.setCurrentIndex(index)
        else:
            self._sync_stack()
        return index

    def removeTab(self, index: int) -> None:  # noqa: N802
        """タブとページを取り除く（ページのウィジェット自体は破棄しない）。"""
        widget = self._stack.widget(index)
        if widget is None:
            return
        self._stack.removeWidget(widget)
        widget.setParent(None)
        # 選択の付け替えは MultiRowTabBar が行い、currentChanged 経由で
        # スタックが追従する。
        self._bar.removeTab(index)
        self._sync_stack()

    def moveTab(self, from_index: int, to_index: int) -> None:  # noqa: N802
        """タブとページをまとめて並べ替える。"""
        widget = self._stack.widget(from_index)
        if widget is None or self._stack.widget(to_index) is None:
            return
        if from_index == to_index:
            return

        self._stack.removeWidget(widget)
        self._stack.insertWidget(to_index, widget)
        self._bar.moveTab(from_index, to_index)
        self._sync_stack()
        self.tabMoved.emit(from_index, to_index)

    def clear(self) -> None:
        while self.count():
            self.removeTab(0)

    # ------------------------------------------------------------------
    # 参照
    # ------------------------------------------------------------------
    def count(self) -> int:
        return self._stack.count()

    def widget(self, index: int) -> QWidget | None:
        return self._stack.widget(index)

    def indexOf(self, widget: QWidget) -> int:  # noqa: N802
        return self._stack.indexOf(widget)

    def currentIndex(self) -> int:  # noqa: N802
        return self._bar.currentIndex()

    def currentWidget(self) -> QWidget | None:  # noqa: N802
        return self._stack.widget(self._bar.currentIndex())

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        self._bar.setCurrentIndex(index)

    def setCurrentWidget(self, widget: QWidget) -> None:  # noqa: N802
        index = self.indexOf(widget)
        if index >= 0:
            self.setCurrentIndex(index)

    def tabBar(self) -> MultiRowTabBar:  # noqa: N802
        return self._bar

    def stack(self) -> QStackedWidget:
        return self._stack

    # ------------------------------------------------------------------
    # タブの内容・表示オプション（QTabWidget と同名で用意する）
    # ------------------------------------------------------------------
    def tabText(self, index: int) -> str:  # noqa: N802
        return self._bar.tabText(index)

    def setTabText(self, index: int, text: str) -> None:  # noqa: N802
        self._bar.setTabText(index, text)

    def tabToolTip(self, index: int) -> str:  # noqa: N802
        return self._bar.tabToolTip(index)

    def setTabToolTip(self, index: int, tooltip: str) -> None:  # noqa: N802
        self._bar.setTabToolTip(index, tooltip)

    def tabsClosable(self) -> bool:  # noqa: N802
        return self._bar.tabsClosable()

    def setTabsClosable(self, closable: bool) -> None:  # noqa: N802
        self._bar.setTabsClosable(closable)

    def isMovable(self) -> bool:  # noqa: N802
        return self._bar.isMovable()

    def setMovable(self, movable: bool) -> None:  # noqa: N802
        self._bar.setMovable(movable)

    def setDocumentMode(self, _enabled: bool) -> None:  # noqa: N802
        """QTabWidget 互換のための空実装（この実装では常に document 風の見た目）。"""

    # ------------------------------------------------------------------
    # 多段表示
    # ------------------------------------------------------------------
    def rowCount(self) -> int:  # noqa: N802
        """タブバーが現在何段になっているか。"""
        return self._bar.rowCount()

    # ------------------------------------------------------------------
    # タブ幅（ウィンドウ単位の設定。MainWindow から使う）
    # ------------------------------------------------------------------
    def fixed_tab_width(self) -> int | None:
        """固定幅（px）。自動幅なら None。"""
        return self._bar.fixed_tab_width()

    def set_fixed_tab_width(self, width: int | None) -> None:
        """全タブの幅を ``width`` px に揃える。``None`` で自動幅に戻す。"""
        self._bar.set_fixed_tab_width(width)

    # ------------------------------------------------------------------
    # タブの配色（アプリ全体で共有する設定。MainWindow から使う）
    # ------------------------------------------------------------------
    def tab_colors(self):
        """カスタム設定中のタブの色 (アクティブ, 非アクティブ)。既定なら None。"""
        return self._bar.tab_colors()

    def set_tab_colors(self, active, inactive) -> None:
        """タブの背景色を設定する。``None`` はパレット任せ（既定）に戻す。"""
        self._bar.set_tab_colors(active, inactive)
