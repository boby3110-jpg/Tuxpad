"""ウィンドウ幅に応じて複数段に折り返す独自タブバー.

Qt 標準の ``QTabBar`` はタブが入り切らないとスクロール（あるいは省略）になり、
段を増やして折り返すことができない。EmEditor のように「開いているタブが常に
全部見えている」状態にしたいので、タブバーをフルスクラッチで実装する。

このウィジェットはタブの**見た目と操作**だけを担当し、中身（ページ）は持たない。
ページの切り替えは :class:`~editor_app.tab_widget.MultiRowTabWidget` が行う。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QDrag, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QSizePolicy, QToolTip, QWidget

#: タブをウィンドウ間でドラッグするときの MIME タイプ（アプリ独自）。
#: 中身は「Tuxpad のタブをドラッグしている」という目印だけで、実際に動かす
#: タブは :meth:`MultiRowTabBar.drag_source` から引く（Python オブジェクトを
#: MIME データに載せない）。
TAB_MIME_TYPE = "application/x-tuxpad-tab"

#: タブ文字列の左右の余白
H_PADDING = 10

#: タブ文字列の上下の余白
V_PADDING = 5

#: 閉じるボタンの一辺
CLOSE_SIZE = 14

#: 文字列と閉じるボタンの間隔
CLOSE_SPACING = 6

#: タブの最小幅 / 最大幅（最大を超える名前は … で省略する）
MIN_TAB_WIDTH = 56
MAX_TAB_WIDTH = 240

#: 文字列用に少し余分に取る幅.
#: ``horizontalAdvance()`` の値ちょうどだと ``elidedText()`` が丸め誤差で
#: 省略してしまい、収まるはずの名前が「rea....md」のようになる。
TEXT_SLACK = 4

#: タブ同士・段同士の隙間
TAB_SPACING = 2
ROW_SPACING = 2


@dataclass
class _Tab:
    """タブ 1 つぶんの表示情報。``rect`` と ``row`` はレイアウト時に計算する。"""

    text: str = ""
    tooltip: str = ""
    rect: QRect = field(default_factory=QRect)
    row: int = 0


class MultiRowTabBar(QWidget):
    """タブを複数段に折り返して表示するタブバー。

    幅が変わるたびに全タブを配置し直し、必要な段数に合わせて自分の高さを変える。
    そのため縦方向のサイズポリシーは Fixed で、高さは ``setFixedHeight()`` で自分で決める。
    """

    #: 選択中のタブが変わった（タブが無くなった場合は -1）
    currentChanged = Signal(int)

    #: 閉じるボタン、または中クリックで閉じるよう要求された
    tabCloseRequested = Signal(int)

    #: ドラッグでタブの並べ替えを要求された (移動元, 移動先)
    tabMoveRequested = Signal(int, int)

    #: ドラッグしたタブが、どのウィンドウにも受け取られずに離された
    #: （＝何も無い場所でドロップされた）。引数は
    #: (タブの位置, 離した位置のグローバル座標)。呼び出し側は新しい
    #: ウィンドウへの切り離し（tear-off）を行う。
    tabTearOffRequested = Signal(int, QPoint)

    #: ウィンドウ間ドラッグが終わった（移動できた／できなかったに関わらず）。
    #: タブが 0 個になったウィンドウを閉じる判断は呼び出し側で行う。
    #: ドラッグ中（``QDrag.exec()`` の入れ子ループの中）にウィンドウを
    #: 閉じると危ないので、必ずドラッグ終了後のこのシグナルで行うこと。
    tabDragFinished = Signal()

    #: タブがダブルクリックされた（新しいウィンドウへの切り離しに使う）。
    tabDoubleClicked = Signal(int)

    #: タブが右クリックされた（コンテキストメニュー用）。引数は
    #: (タブの位置, 表示位置のグローバル座標)。Wayland ではドラッグでの
    #: ウィンドウ間移動が座標の制約で効かないことがあるため、確実に効く
    #: 移動手段としてメニューを出す（実機フィードバックにより追加）。
    tabContextMenuRequested = Signal(int, QPoint)

    #: ウィンドウ間ドラッグの最中だけ、ドラッグ元 (タブバー, タブの位置) を
    #: クラス変数で覚えておく。同一プロセス内の移動なので、受け手はここから
    #: 「どのタブか」を引く（MIME データには目印しか入れない）。
    _drag_source: "tuple[MultiRowTabBar, int] | None" = None

    #: 今のドラッグが、いずれかの Tuxpad ウィンドウに引き取られた（＝合体できた）か。
    #: 切り離すべきかの判断は本来 ``QDrag.exec()`` の戻り値で分かるはずだが、
    #: **Wayland ではウィンドウの外へ落としても ``IgnoreAction`` が返らない**
    #: ことがあり、戻り値だけを見ていると「外へドラッグして切り離し」が効かない。
    #: そこで受け手が引き取れたときにこのフラグを立て、ドラッグ終了後に
    #: 「引き取られていなければ切り離す」と判断する（プラットフォーム非依存）。
    _drag_accepted: bool = False

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tabs: list[_Tab] = []
        self._current = -1
        self._rows = 1
        self._closable = False
        self._movable = False
        # None なら従来通り内容に応じた自動幅。数値ならその px で全タブを揃える
        # （実機フィードバックにより追加。ファイル名の長さでタブ幅がバラバラに
        # なるのを避けたい、という要望への対応）。
        self._fixed_width: int | None = None
        # タブの配色。None なら従来通りパレット任せ（実機フィードバックで追加）。
        self._active_color: QColor | None = None
        self._inactive_color: QColor | None = None

        # マウスの状態（ホバー表示とクリック判定に使う）
        self._hover_index = -1
        self._hover_close = False
        self._press_index = -1
        self._press_button = Qt.MouseButton.NoButton
        self._press_on_close = False
        self._press_pos = QPoint()
        self._dragging = False

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._update_height()

    # ------------------------------------------------------------------
    # タブの追加・削除・並べ替え
    # ------------------------------------------------------------------
    def count(self) -> int:
        return len(self._tabs)

    def addTab(self, text: str) -> int:  # noqa: N802 - QTabBar に合わせた命名
        """末尾にタブを追加し、その位置を返す。"""
        return self.insertTab(len(self._tabs), text)

    def insertTab(self, index: int, text: str) -> int:  # noqa: N802
        """指定位置にタブを挿入し、実際に挿入された位置を返す。"""
        index = max(0, min(index, len(self._tabs)))
        self._tabs.insert(index, _Tab(text=text))
        if self._current >= index:
            self._current += 1
        self._reset_mouse_state()
        self._relayout()
        return index

    def removeTab(self, index: int) -> None:  # noqa: N802
        """タブを取り除く。選択中のタブが消えた場合は隣のタブを選び直す。

        **選択中のタブを消した場合は、番号が変わらなくても
        ``currentChanged`` を出す**（``QTabBar`` と同じ振る舞い）。例えば
        タブが 3 つあって真ん中を選んでいるときにそれを消すと、番号は 1 の
        ままでも中身は別の文書に変わる。番号だけを見て「変わっていない」と
        判断すると、表示中の文書が変わったことが誰にも伝わらない
        （タブをドラッグで別ウィンドウへ移したあと、元のウィンドウの
        タイトルバーが移したタブの名前のままになる不具合があった）。
        """
        if not self._is_valid(index):
            return
        del self._tabs[index]

        previous = self._current
        removed_current = index == self._current
        if index < self._current:
            self._current -= 1
        elif removed_current:
            self._current = min(self._current, len(self._tabs) - 1)
        self._reset_mouse_state()
        self._relayout()
        if removed_current or self._current != previous:
            self.currentChanged.emit(self._current)

    def moveTab(self, from_index: int, to_index: int) -> None:  # noqa: N802
        """タブの並び順を変える。選択中の「タブ」は変わらない（位置だけ変わる）。

        選択中のタブが移動しても ``currentChanged`` は出さない。表示している
        文書そのものは変わっていないため。
        """
        if not self._is_valid(from_index) or not self._is_valid(to_index):
            return
        if from_index == to_index:
            return
        self._tabs.insert(to_index, self._tabs.pop(from_index))
        self._current = _shift_index(self._current, from_index, to_index)
        # ドラッグの途中で呼ばれるので、押下中の状態は消さずにホバーだけ解除する。
        self._hover_index = -1
        self._hover_close = False
        self._relayout()

    def _is_valid(self, index: int) -> bool:
        return 0 <= index < len(self._tabs)

    # ------------------------------------------------------------------
    # タブの内容
    # ------------------------------------------------------------------
    def tabText(self, index: int) -> str:  # noqa: N802
        return self._tabs[index].text if self._is_valid(index) else ""

    def setTabText(self, index: int, text: str) -> None:  # noqa: N802
        if self._is_valid(index) and self._tabs[index].text != text:
            self._tabs[index].text = text
            # 文字数が変わると幅が変わり、折り返し位置も変わりうる。
            self._relayout()

    def tabToolTip(self, index: int) -> str:  # noqa: N802
        return self._tabs[index].tooltip if self._is_valid(index) else ""

    def setTabToolTip(self, index: int, tooltip: str) -> None:  # noqa: N802
        if self._is_valid(index):
            self._tabs[index].tooltip = tooltip

    # ------------------------------------------------------------------
    # 選択状態
    # ------------------------------------------------------------------
    def currentIndex(self) -> int:  # noqa: N802
        return self._current

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not self._tabs:
            index = -1
        elif not self._is_valid(index):
            return
        if index == self._current:
            return
        self._current = index
        self.update()
        self.currentChanged.emit(index)

    # ------------------------------------------------------------------
    # 表示オプション
    # ------------------------------------------------------------------
    def tabsClosable(self) -> bool:  # noqa: N802
        return self._closable

    def setTabsClosable(self, closable: bool) -> None:  # noqa: N802
        if self._closable != bool(closable):
            self._closable = bool(closable)
            self._relayout()

    def isMovable(self) -> bool:  # noqa: N802
        return self._movable

    def setMovable(self, movable: bool) -> None:  # noqa: N802
        self._movable = bool(movable)

    # ------------------------------------------------------------------
    # レイアウト（この実装の中心）
    # ------------------------------------------------------------------
    def rowCount(self) -> int:  # noqa: N802
        """現在の段数（タブが無くても 1 段ぶんの高さは確保する）。"""
        return self._rows

    def tabRect(self, index: int) -> QRect:  # noqa: N802
        """タブの矩形（このウィジェット内の座標）。無効な位置なら空の矩形。"""
        return QRect(self._tabs[index].rect) if self._is_valid(index) else QRect()

    def tabRow(self, index: int) -> int:  # noqa: N802
        """タブが何段目にあるか（0 始まり）。"""
        return self._tabs[index].row if self._is_valid(index) else -1

    def tabAt(self, pos: QPoint) -> int:  # noqa: N802
        """座標の下にあるタブの位置。無ければ -1。"""
        for index, tab in enumerate(self._tabs):
            if tab.rect.contains(pos):
                return index
        return -1

    def _row_height(self) -> int:
        return self.fontMetrics().height() + 2 * V_PADDING

    def fixed_tab_width(self) -> int | None:
        """固定幅（px）。自動幅なら None。"""
        return self._fixed_width

    def set_fixed_tab_width(self, width: int | None) -> None:
        """全タブの幅を ``width`` px に揃える。``None`` で自動幅に戻す。

        指定した値は ``MIN_TAB_WIDTH``/``MAX_TAB_WIDTH`` でクランプしない
        （利用者が指定した値をそのまま使う）。ただしウィンドウ幅より広くは
        できないので、そこだけは ``_tab_width()`` で頭打ちにする。
        """
        self._fixed_width = None if width is None else max(int(width), 1)
        self._relayout()

    def _tab_width(self, tab: _Tab, available: int) -> int:
        if self._fixed_width is not None:
            return min(self._fixed_width, available)
        width = self.fontMetrics().horizontalAdvance(tab.text) + TEXT_SLACK + 2 * H_PADDING
        if self._closable:
            width += CLOSE_SIZE + CLOSE_SPACING
        width = max(MIN_TAB_WIDTH, min(width, MAX_TAB_WIDTH))
        # 1 つのタブがウィンドウ幅より広くならないようにする。
        return min(width, available)

    def _relayout(self) -> None:
        """全タブを左から並べ、幅が足りなくなったら次の段へ折り返す。"""
        available = max(self.width(), MIN_TAB_WIDTH)
        row_height = self._row_height()
        x = 0
        row = 0

        for tab in self._tabs:
            width = self._tab_width(tab, available)
            if x > 0 and x + width > available:
                # この段には入らないので次の段へ送る。
                row += 1
                x = 0
            tab.row = row
            tab.rect = QRect(x, row * (row_height + ROW_SPACING), width, row_height)
            x += width + TAB_SPACING

        self._rows = row + 1 if self._tabs else 1
        self._update_height()
        self.update()

    def _update_height(self) -> None:
        height = self._rows * self._row_height() + (self._rows - 1) * ROW_SPACING
        if self.height() != height or self.minimumHeight() != height:
            self.setFixedHeight(height)
            self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802
        width = sum(tab.rect.width() + TAB_SPACING for tab in self._tabs)
        return QSize(max(width, MIN_TAB_WIDTH), self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(MIN_TAB_WIDTH, self.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self._relayout()

    def changeEvent(self, event) -> None:  # noqa: N802
        # フォントが変わると 1 段の高さもタブの幅も変わる。
        if event.type() == QEvent.Type.FontChange:
            self._relayout()
        super().changeEvent(event)

    # ------------------------------------------------------------------
    # 描画
    # ------------------------------------------------------------------
    def closeButtonRect(self, index: int) -> QRect:  # noqa: N802
        """閉じるボタンの当たり判定の矩形。閉じるボタンが無ければ空。"""
        if not self._closable or not self._is_valid(index):
            return QRect()
        rect = self._tabs[index].rect
        if rect.width() < MIN_TAB_WIDTH:
            return QRect()
        left = rect.right() - H_PADDING - CLOSE_SIZE + 1
        top = rect.top() + (rect.height() - CLOSE_SIZE) // 2
        return QRect(left, top, CLOSE_SIZE, CLOSE_SIZE)

    def _text_rect(self, index: int) -> QRect:
        rect = QRect(self._tabs[index].rect)
        right_margin = H_PADDING
        if self._closable:
            right_margin += CLOSE_SIZE + CLOSE_SPACING
        return rect.adjusted(H_PADDING, 0, -right_margin, 0)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()

        painter.fillRect(self.rect(), palette.window())

        for index, tab in enumerate(self._tabs):
            selected = index == self._current
            hovered = index == self._hover_index
            self._paint_tab(painter, index, tab, selected, hovered)

        painter.end()

    def tab_colors(self) -> tuple[QColor | None, QColor | None]:
        """カスタム設定中のタブの色 (アクティブ, 非アクティブ)。既定なら None。"""
        return self._active_color, self._inactive_color

    def set_tab_colors(self, active: QColor | None, inactive: QColor | None) -> None:
        """タブの背景色を設定する。``None`` はパレット任せ（既定）に戻す。"""
        self._active_color = QColor(active) if active is not None else None
        self._inactive_color = QColor(inactive) if inactive is not None else None
        # 幅は変わらないので再配置は不要。再描画だけでよい。
        self.update()

    def tab_background_color(self, selected: bool, hovered: bool) -> QColor:
        """タブ 1 つぶんの背景色を決める（描画とテストの両方から使う）。"""
        palette = self.palette()
        if selected:
            if self._active_color is not None:
                return QColor(self._active_color)
            return palette.base().color()

        base = self._inactive_color
        if base is None:
            base = palette.button().color()
        return QColor(base).lighter(108) if hovered else QColor(base)

    def tab_text_color(self, selected: bool) -> QColor:
        """タブ文字列の色。

        利用者が濃い色を選んだときに文字が読めなくならないよう、**カスタム色を
        設定している場合だけ**背景の明るさから白／黒を選ぶ。既定のパレット色の
        ままなら従来通りパレット任せ。
        """
        palette = self.palette()
        custom = self._active_color if selected else self._inactive_color
        if custom is None:
            return palette.windowText().color() if selected else palette.buttonText().color()
        # HSV の明度で判定する（130 前後が中間。暗い背景なら白文字にする）。
        return QColor(Qt.GlobalColor.black) if custom.value() > 140 else QColor(Qt.GlobalColor.white)

    def _paint_tab(self, painter: QPainter, index: int, tab: _Tab, selected: bool, hovered: bool) -> None:
        palette = self.palette()
        rect = tab.rect

        background = self.tab_background_color(selected, hovered)

        painter.setPen(QPen(palette.mid().color()))
        painter.setBrush(background)
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 3, 3)

        if selected:
            # 選択中のタブは下辺を強調して分かりやすくする。
            painter.setPen(QPen(palette.highlight().color(), 2))
            painter.drawLine(
                rect.left() + 2, rect.bottom() - 1, rect.right() - 2, rect.bottom() - 1
            )

        text_rect = self._text_rect(index)
        if text_rect.width() > 0:
            painter.setPen(QPen(self.tab_text_color(selected)))
            text = self.fontMetrics().elidedText(
                tab.text, Qt.TextElideMode.ElideMiddle, text_rect.width()
            )
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)

        close_rect = self.closeButtonRect(index)
        if not close_rect.isEmpty():
            self._paint_close_button(painter, close_rect, hovered and self._hover_close)

    def _paint_close_button(self, painter: QPainter, rect: QRect, hovered: bool) -> None:
        palette = self.palette()
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(palette.highlight().color())
            painter.drawEllipse(rect)
            color = palette.highlightedText().color()
        else:
            color = QColor(palette.buttonText().color())
            color.setAlpha(180)

        painter.setPen(QPen(color, 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        mark = rect.adjusted(4, 4, -4, -4)
        painter.drawLine(mark.topLeft(), mark.bottomRight())
        painter.drawLine(mark.topRight(), mark.bottomLeft())

    # ------------------------------------------------------------------
    # ウィンドウ間ドラッグ（Qt 標準のドラッグ&ドロップ）
    #
    # 以前は自前でマウス座標を追いかけ、離した位置のグローバル座標が
    # どのウィンドウの矩形に入るかで移動先を決めていた。しかし Wayland では
    # アプリがウィンドウの絶対座標を取れないため、この方式では別ウィンドウへ
    # 合体させる操作が効かなかった（実機フィードバック）。Qt 標準の D&D なら
    # ドロップ先へのイベント配送を Qt/コンポジタが行うので、アプリが座標を
    # 知る必要がなくなる。
    # ------------------------------------------------------------------
    @classmethod
    def drag_source(cls) -> "tuple[MultiRowTabBar, int] | None":
        """ドラッグ中のタブ (タブバー, タブの位置)。ドラッグ中でなければ None。"""
        return cls._drag_source

    @classmethod
    def set_drag_source(cls, bar: "MultiRowTabBar | None", index: int = -1) -> None:
        """ドラッグ元を覚える（``bar`` が None なら忘れる）。

        テストからは、実際のドラッグを起こさずにドロップ側のロジックだけを
        検証するために使う。ドラッグの開始（``bar`` が None でない）に合わせて
        「引き取られた」フラグを倒しておく。
        """
        cls._drag_source = None if bar is None else (bar, index)
        if bar is not None:
            cls._drag_accepted = False

    @classmethod
    def mark_drag_accepted(cls) -> None:
        """受け手がドラッグ中のタブを引き取れたことを記録する。"""
        cls._drag_accepted = True

    @classmethod
    def drag_was_accepted(cls) -> bool:
        """今のドラッグが、いずれかのウィンドウに引き取られたか。"""
        return cls._drag_accepted

    def _drag_pixmap(self, index: int) -> QPixmap | None:
        """ドラッグ中にカーソルへ付いてくる、タブ 1 つぶんの絵。"""
        rect = self.tabRect(index)
        if rect.isEmpty():
            return None
        pixmap = QPixmap(rect.size() * self.devicePixelRatioF())
        pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(-rect.topLeft())
        self._paint_tab(painter, index, self._tabs[index], True, False)
        painter.end()
        return pixmap

    def start_cross_window_drag(self, index: int) -> None:
        """タブバーの外へ出たタブについて、Qt 標準のドラッグを開始する。

        ``QDrag.exec()`` は入れ子のイベントループなので、返ってくるのは
        ドロップが終わったあと。**この呼び出しの間にタブが別ウィンドウへ
        移っていることがある**点に注意（受け手が同期的にタブを引き取る）。
        """
        if not self._is_valid(index):
            return

        # ドラッグは Qt に任せるので、自前のマウス追跡はここで終わりにする。
        self._reset_mouse_state()
        self.unsetCursor()

        mime = QMimeData()
        mime.setData(TAB_MIME_TYPE, b"tuxpad-tab")
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = self._drag_pixmap(index)
        if pixmap is not None:
            rect = self.tabRect(index)
            drag.setPixmap(pixmap)
            # ホットスポットは（デバイスピクセルではなく）論理座標で指定する。
            drag.setHotSpot(QPoint(rect.width() // 2, rect.height() // 2))

        MultiRowTabBar.set_drag_source(self, index)
        try:
            action = drag.exec(Qt.DropAction.MoveAction)
        finally:
            MultiRowTabBar.set_drag_source(None)

        self.finish_cross_window_drag(index, action, QCursor.pos())

    def finish_cross_window_drag(
        self, index: int, action: Qt.DropAction, global_pos: QPoint
    ) -> None:
        """ドラッグが終わったあとの後始末。

        どのウィンドウにも引き取られなかったときは「新しいウィンドウへ
        切り離し」にする。判断は ``QDrag.exec()`` の戻り値 (``action``) では
        なく、受け手が立てる :meth:`drag_was_accepted` フラグで行う。
        **Wayland ではウィンドウの外へ落としても ``IgnoreAction`` が返らず**、
        戻り値だけを見ていると外への切り離しが効かないため
        （``action`` は診断用に受け取るだけで、判断には使わない）。
        """
        if not MultiRowTabBar.drag_was_accepted():
            self.tabTearOffRequested.emit(index, global_pos)
        self.tabDragFinished.emit()

    # ------------------------------------------------------------------
    # マウス操作
    # ------------------------------------------------------------------
    def _reset_mouse_state(self) -> None:
        self._press_index = -1
        self._press_button = Qt.MouseButton.NoButton
        self._press_on_close = False
        self._dragging = False
        self._hover_index = -1
        self._hover_close = False

    def _update_hover(self, pos: QPoint) -> None:
        index = self.tabAt(pos)
        on_close = index >= 0 and self.closeButtonRect(index).contains(pos)
        if index != self._hover_index or on_close != self._hover_close:
            self._hover_index = index
            self._hover_close = on_close
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        index = self.tabAt(pos)
        if index < 0:
            super().mousePressEvent(event)
            return

        self._press_index = index
        self._press_button = event.button()
        self._press_pos = pos
        self._press_on_close = self.closeButtonRect(index).contains(pos)
        self._dragging = False

        if event.button() == Qt.MouseButton.LeftButton and not self._press_on_close:
            # QTabBar と同じく、押した時点で切り替える。
            self.setCurrentIndex(index)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """タブのダブルクリックで、そのタブを新しいウィンドウへ切り離す。"""
        pos = event.position().toPoint()
        index = self.tabAt(pos)
        if (
            index >= 0
            and event.button() == Qt.MouseButton.LeftButton
            and not self.closeButtonRect(index).contains(pos)
        ):
            self._reset_mouse_state()
            self.tabDoubleClicked.emit(index)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        self._update_hover(pos)

        dragging_possible = (
            self._movable
            and self._press_button == Qt.MouseButton.LeftButton
            and not self._press_on_close
            and self._press_index >= 0
            and bool(event.buttons() & Qt.MouseButton.LeftButton)
        )
        if not dragging_possible:
            super().mouseMoveEvent(event)
            return

        if not self._dragging:
            moved = (pos - self._press_pos).manhattanLength()
            if moved < QApplication.startDragDistance():
                return
            self._dragging = True

        if not self.rect().contains(pos):
            # タブバーの外へ出た＝ウィンドウ間の移動（または切り離し）の
            # つもり。ここから先は Qt 標準のドラッグ&ドロップに引き継ぐ。
            index = self._press_index
            event.accept()
            self.start_cross_window_drag(index)
            return

        target = self.tabAt(pos)
        if target >= 0 and target != self._press_index:
            source = self._press_index
            # 実際の並べ替えは MultiRowTabWidget 側で行う（ページも一緒に動かすため）。
            self._press_index = target
            self.tabMoveRequested.emit(source, target)
        elif target < 0:
            # 同じ段の隙間や段の端など、タブバーの中だけど tab の上ではない場所。
            # ドラッグ中はカーソルでそれと分かるようにしておく。
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        index = self.tabAt(pos)
        button = event.button()
        press_index = self._press_index
        press_on_close = self._press_on_close
        dragging = self._dragging

        self._press_index = -1
        self._press_button = Qt.MouseButton.NoButton
        self._press_on_close = False
        self._dragging = False
        self.unsetCursor()

        if dragging:
            # タブバーの中で離された＝並べ替えだけ（もう済んでいる）。
            # タブバーの外へ出た場合は mouseMoveEvent で Qt 標準のドラッグへ
            # 引き継いでいるので、ここには来ない。
            event.accept()
            return

        if press_index < 0:
            super().mouseReleaseEvent(event)
            return

        if button == Qt.MouseButton.LeftButton and press_on_close:
            # 押した位置と離した位置が同じタブの × のときだけ閉じる。
            if index == press_index and self.closeButtonRect(index).contains(pos):
                self.tabCloseRequested.emit(index)
        elif button == Qt.MouseButton.MiddleButton and index == press_index:
            self.tabCloseRequested.emit(index)
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_index != -1 or self._hover_close:
            self._hover_index = -1
            self._hover_close = False
            self.update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """タブを右クリックしたら、そのタブの位置を添えてシグナルを出す。"""
        index = self.tabAt(event.pos())
        if index >= 0:
            self.tabContextMenuRequested.emit(index, event.globalPos())
            event.accept()
        else:
            super().contextMenuEvent(event)

    def event(self, event) -> bool:
        """タブごとのツールチップ（フルパス）を出す。"""
        if event.type() == QEvent.Type.ToolTip:
            index = self.tabAt(event.pos())
            tooltip = self.tabToolTip(index) if index >= 0 else ""
            if tooltip:
                QToolTip.showText(event.globalPos(), tooltip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)


def _shift_index(index: int, from_index: int, to_index: int) -> int:
    """タブを ``from_index`` から ``to_index`` へ動かした後の ``index`` の位置。"""
    if index < 0:
        return index
    if index == from_index:
        return to_index
    if from_index < index <= to_index:
        return index - 1
    if to_index <= index < from_index:
        return index + 1
    return index
