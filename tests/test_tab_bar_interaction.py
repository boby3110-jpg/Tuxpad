"""独自タブバー（機能 5）の、マウス操作まわりの細かい振る舞いのテスト.

``tests/test_multirow_tabs.py`` は「折り返しのレイアウト」と
「クリック・並べ替えの主な流れ」を見ている。こちらはそこから漏れていた

* ちょっとした操作ミス（数 px の滑り・× のダブルクリック）への耐性
* ホバー表示・ツールチップ・右クリック
* ウィンドウ間ドラッグの開始処理（``start_cross_window_drag``）
* 並べ替えたときの「選択中のタブ」の追随（``_shift_index``）
* 無効な位置を渡されたときに黙って何もしないこと

を受け持つ。どれも「壊れても他のテストが赤くならない」場所だったので、
ここで固定しておく。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QFont, QHelpEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from editor_app.tab_bar import MIN_TAB_WIDTH, TAB_MIME_TYPE, MultiRowTabBar

# ----------------------------------------------------------------------
# 補助
# ----------------------------------------------------------------------


@pytest.fixture
def bar(qtbot) -> MultiRowTabBar:
    """幅を直接指定できる、表示済みのタブバー。"""
    widget = MultiRowTabBar()
    widget.setTabsClosable(True)
    widget.setMovable(True)
    qtbot.addWidget(widget)
    widget.show()
    widget.resize(400, widget.height())
    QApplication.processEvents()
    assert widget.width() == 400
    return widget


def fill(bar: MultiRowTabBar, count: int, prefix: str = "ファイル") -> None:
    for i in range(count):
        bar.addTab(f"{prefix}{i}.txt")


def send_mouse(widget, event_type, pos: QPoint, button, buttons) -> None:
    """ボタンの押下状態を指定してマウスイベントを送る。"""
    event = QMouseEvent(
        event_type,
        QPointF(pos),
        QPointF(widget.mapToGlobal(pos)),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def press(widget, pos: QPoint, button=Qt.MouseButton.LeftButton) -> None:
    send_mouse(widget, QEvent.Type.MouseButtonPress, pos, button, button)


def move(widget, pos: QPoint, buttons=Qt.MouseButton.LeftButton) -> None:
    send_mouse(widget, QEvent.Type.MouseMove, pos, Qt.MouseButton.NoButton, buttons)


def release(widget, pos: QPoint, button=Qt.MouseButton.LeftButton) -> None:
    send_mouse(widget, QEvent.Type.MouseButtonRelease, pos, button, Qt.MouseButton.NoButton)


def empty_spot(bar: MultiRowTabBar) -> QPoint:
    """タブバーの中で、どのタブにも乗っていない座標を返す。"""
    last = bar.tabRect(bar.count() - 1)
    pos = QPoint(last.right() + 20, last.center().y())
    assert bar.rect().contains(pos)
    assert bar.tabAt(pos) == -1
    return pos


class FakeDrag:
    """``QDrag`` の代わり。``exec()`` は入れ子のイベントループなので本物は使えない。

    ヘッドレスで本物の ``QDrag.exec()`` を呼ぶと、誰もドロップしないまま
    戻ってこない。ここでは「何を積んで」「いつドラッグ元を覚え／忘れるか」
    だけを見たいので、``exec()`` の中で呼ぶ処理 (``recorder.during``) を
    差し込めるようにしておく。
    """

    def __init__(self, source, recorder: "DragRecorder") -> None:
        self.source = source
        self.recorder = recorder
        self.mime = None
        self.pixmap = None
        self.hot_spot = None
        self.action = None

    def setMimeData(self, mime) -> None:  # noqa: N802 - Qt に合わせた命名
        self.mime = mime

    def setPixmap(self, pixmap) -> None:  # noqa: N802
        self.pixmap = pixmap

    def setHotSpot(self, point) -> None:  # noqa: N802
        self.hot_spot = QPoint(point)

    def exec(self, action):
        self.action = action
        if self.recorder.during is not None:
            self.recorder.during(self)
        return Qt.DropAction.MoveAction


class DragRecorder:
    """``QDrag`` の代わりに呼ばれ、作られた偽ドラッグを覚えておく。

    ``QDrag`` は ``start_cross_window_drag()`` の中で作られるので、
    「ドラッグ中に起きること」はここ (``during``) に前もって仕掛けておく。
    """

    def __init__(self) -> None:
        self.created: list[FakeDrag] = []
        self.during = None

    def __call__(self, source) -> FakeDrag:
        drag = FakeDrag(source, self)
        self.created.append(drag)
        return drag


@pytest.fixture
def fake_drag(monkeypatch) -> DragRecorder:
    """``QDrag`` を差し替え、作られた偽ドラッグを取り出せるようにする。"""
    recorder = DragRecorder()
    monkeypatch.setattr("editor_app.tab_bar.QDrag", recorder)
    return recorder


# ----------------------------------------------------------------------
# 手が滑ったときの耐性（クリックのつもりの小さな動き）
# ----------------------------------------------------------------------
def test_tiny_slip_does_not_start_a_cross_window_drag(bar: MultiRowTabBar) -> None:
    """数 px 滑っただけでは切り離し（ウィンドウ間ドラッグ）を始めない。

    タブの高さはタブバーの高さそのものなので、タブを押した指がほんの少し
    下へずれるだけで座標はタブバーの外に出る。ここで即ドラッグを始めると、
    ただクリックしたつもりがタブが別ウィンドウへ飛んでいってしまう。
    """
    fill(bar, 3, prefix="滑り")
    started: list[int] = []
    bar.start_cross_window_drag = started.append

    slip = QApplication.startDragDistance() - 1
    assert slip >= 1
    rect = bar.tabRect(0)
    press(bar, QPoint(rect.center().x(), rect.bottom()))
    outside = QPoint(rect.center().x(), rect.bottom() + slip)
    assert not bar.rect().contains(outside)  # タブバーの外ではある
    move(bar, outside)

    assert started == []


def test_slip_beyond_the_threshold_does_start_a_drag(bar: MultiRowTabBar) -> None:
    """しきい値を超えて動かせば、同じ方向でもドラッグは始まる（対の確認）。"""
    fill(bar, 3, prefix="滑り")
    started: list[int] = []
    bar.start_cross_window_drag = started.append

    rect = bar.tabRect(0)
    press(bar, QPoint(rect.center().x(), rect.bottom()))
    move(bar, QPoint(rect.center().x(), rect.bottom() + QApplication.startDragDistance() + 5))

    assert started == [0]


def test_double_click_on_the_close_button_does_not_tear_off(bar: MultiRowTabBar) -> None:
    """× を素早く 2 回押しても、切り離し（新しいウィンドウ）にはしない。

    × の連打で複数のタブを閉じるのはよくある操作で、そのとき 2 回目が
    ダブルクリック扱いになる。ここで切り離してしまうと、閉じたつもりが
    別ウィンドウが開くことになる。
    """
    fill(bar, 3, prefix="連打")
    torn: list[int] = []
    bar.tabDoubleClicked.connect(torn.append)

    close_rect = bar.closeButtonRect(0)
    assert not close_rect.isEmpty()
    send_mouse(
        bar,
        QEvent.Type.MouseButtonDblClick,
        close_rect.center(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
    )

    assert torn == []


def test_double_click_on_empty_area_does_not_tear_off(bar: MultiRowTabBar) -> None:
    """タブの無いところをダブルクリックしても何も起きない。"""
    fill(bar, 2, prefix="余白")
    torn: list[int] = []
    bar.tabDoubleClicked.connect(torn.append)

    send_mouse(
        bar,
        QEvent.Type.MouseButtonDblClick,
        empty_spot(bar),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
    )

    assert torn == []


def test_right_double_click_does_not_tear_off(bar: MultiRowTabBar) -> None:
    """右ボタンのダブルクリックでは切り離さない（左ボタンだけの操作）。"""
    fill(bar, 2, prefix="右")
    torn: list[int] = []
    bar.tabDoubleClicked.connect(torn.append)

    send_mouse(
        bar,
        QEvent.Type.MouseButtonDblClick,
        bar.tabRect(0).center(),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
    )

    assert torn == []


def test_double_click_on_a_tab_tears_off(bar: MultiRowTabBar) -> None:
    """タブ本体のダブルクリックはこれまで通り切り離しを要求する（対の確認）。"""
    fill(bar, 2, prefix="切り離し")
    torn: list[int] = []
    bar.tabDoubleClicked.connect(torn.append)

    send_mouse(
        bar,
        QEvent.Type.MouseButtonDblClick,
        bar.tabRect(1).center(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
    )

    assert torn == [1]


# ----------------------------------------------------------------------
# ドラッグ中のカーソル
# ----------------------------------------------------------------------
def test_dragging_over_a_gap_shows_the_grab_cursor(bar: MultiRowTabBar) -> None:
    """段の端など「タブの無い場所」へ持っていったら掴んでいる形にする。"""
    fill(bar, 3, prefix="掴み")
    assert bar.cursor().shape() == Qt.CursorShape.ArrowCursor

    press(bar, bar.tabRect(0).center())
    move(bar, empty_spot(bar))

    assert bar.cursor().shape() == Qt.CursorShape.ClosedHandCursor


def test_releasing_restores_the_cursor(bar: MultiRowTabBar) -> None:
    """離したらカーソルは元に戻る（掴んだ形のまま残らない）。"""
    fill(bar, 3, prefix="掴み")
    gap = empty_spot(bar)

    press(bar, bar.tabRect(0).center())
    move(bar, gap)
    release(bar, gap)

    assert bar.cursor().shape() == Qt.CursorShape.ArrowCursor


# ----------------------------------------------------------------------
# ホバーの見た目
# ----------------------------------------------------------------------
def test_hovering_the_close_button_changes_the_look(bar: MultiRowTabBar) -> None:
    """× にカーソルを乗せると見た目が変わる（押せる場所だと分かる）。"""
    fill(bar, 3, prefix="ホバー")
    bar.setCurrentIndex(0)
    plain = bar.grab().toImage()

    move(bar, bar.closeButtonRect(1).center(), buttons=Qt.MouseButton.NoButton)

    assert bar.grab().toImage() != plain


def test_leaving_the_bar_clears_the_hover(bar: MultiRowTabBar) -> None:
    """カーソルがタブバーから出たら、ホバーの見た目を消す。

    消し忘れると「マウスが別の場所にあるのに × が光ったまま」になる。
    """
    fill(bar, 3, prefix="ホバー")
    bar.setCurrentIndex(0)
    plain = bar.grab().toImage()

    move(bar, bar.closeButtonRect(1).center(), buttons=Qt.MouseButton.NoButton)
    assert bar.grab().toImage() != plain

    QApplication.sendEvent(bar, QEvent(QEvent.Type.Leave))

    assert bar.grab().toImage() == plain


# ----------------------------------------------------------------------
# ツールチップ（フルパス）
# ----------------------------------------------------------------------
class FakeToolTip:
    """``QToolTip`` の代わり（ヘッドレスでは本物の表示を確かめられない）。"""

    shown: list[tuple[QPoint, str]] = []
    hidden = 0

    @classmethod
    def reset(cls) -> None:
        cls.shown = []
        cls.hidden = 0

    @classmethod
    def showText(cls, pos, text, widget=None) -> None:  # noqa: N802
        cls.shown.append((QPoint(pos), text))

    @classmethod
    def hideText(cls) -> None:  # noqa: N802
        cls.hidden += 1


@pytest.fixture
def tooltips(monkeypatch):
    FakeToolTip.reset()
    monkeypatch.setattr("editor_app.tab_bar.QToolTip", FakeToolTip)
    return FakeToolTip


def send_tooltip_request(bar: MultiRowTabBar, pos: QPoint) -> bool:
    event = QHelpEvent(QEvent.Type.ToolTip, pos, bar.mapToGlobal(pos))
    return QApplication.sendEvent(bar, event)


def test_tooltip_shows_the_tab_tooltip(bar: MultiRowTabBar, tooltips) -> None:
    """タブの上ではそのタブのツールチップ（フルパス）を出す。"""
    fill(bar, 3, prefix="説明")
    bar.setTabToolTip(1, "/home/user/文書/説明1.txt")

    handled = send_tooltip_request(bar, bar.tabRect(1).center())

    assert handled
    assert [text for _pos, text in tooltips.shown] == ["/home/user/文書/説明1.txt"]


def test_tooltip_is_hidden_on_a_tab_without_one(bar: MultiRowTabBar, tooltips) -> None:
    """ツールチップの無いタブ（未保存の無題など）では、前の表示を消す。"""
    fill(bar, 3, prefix="説明")
    bar.setTabToolTip(1, "/home/user/文書/説明1.txt")
    send_tooltip_request(bar, bar.tabRect(1).center())

    send_tooltip_request(bar, bar.tabRect(2).center())

    assert len(tooltips.shown) == 1  # 増えていない
    assert tooltips.hidden == 1


def test_tooltip_is_hidden_on_empty_area(bar: MultiRowTabBar, tooltips) -> None:
    """タブの無い場所ではツールチップを出さない。"""
    fill(bar, 2, prefix="説明")
    bar.setTabToolTip(0, "/home/user/文書/説明0.txt")

    send_tooltip_request(bar, empty_spot(bar))

    assert tooltips.shown == []
    assert tooltips.hidden == 1


# ----------------------------------------------------------------------
# 右クリック（Wayland でも確実に効く移動手段の入り口）
# ----------------------------------------------------------------------
def test_right_click_on_a_tab_requests_the_menu(bar: MultiRowTabBar) -> None:
    fill(bar, 3, prefix="メニュー")
    requested: list[tuple[int, QPoint]] = []
    bar.tabContextMenuRequested.connect(lambda i, p: requested.append((i, QPoint(p))))

    pos = bar.tabRect(2).center()
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, bar.mapToGlobal(pos))
    QApplication.sendEvent(bar, event)

    assert [index for index, _pos in requested] == [2]
    # メニューを出す位置は（ウィジェット内ではなく）画面の座標で渡すこと。
    assert requested[0][1] == bar.mapToGlobal(pos)


def test_right_click_on_empty_area_requests_nothing(bar: MultiRowTabBar) -> None:
    """タブの無い場所の右クリックでは、タブのメニューを出さない。"""
    fill(bar, 2, prefix="メニュー")
    requested: list[int] = []
    bar.tabContextMenuRequested.connect(lambda i, _p: requested.append(i))

    pos = empty_spot(bar)
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, bar.mapToGlobal(pos))
    QApplication.sendEvent(bar, event)

    assert requested == []


# ----------------------------------------------------------------------
# ウィンドウ間ドラッグの開始
# ----------------------------------------------------------------------
def test_start_drag_carries_the_tab_mime_type(bar: MultiRowTabBar, fake_drag) -> None:
    """ドラッグには「Tuxpad のタブ」の目印を載せる（他アプリと区別する）。"""
    fill(bar, 2, prefix="持ち出す")

    bar.start_cross_window_drag(1)

    assert len(fake_drag.created) == 1
    assert fake_drag.created[0].mime.hasFormat(TAB_MIME_TYPE)
    assert fake_drag.created[0].action == Qt.DropAction.MoveAction


def test_start_drag_remembers_the_source_only_during_the_drag(
    bar: MultiRowTabBar, fake_drag
) -> None:
    """ドラッグ中だけドラッグ元を覚え、終わったら必ず忘れる。

    受け手は MIME ではなくここからタブを引く（Python オブジェクトを MIME に
    載せないため）。消し忘れると、次のドラッグが前のタブを動かしてしまう。
    """
    fill(bar, 3, prefix="持ち出す")
    seen: list[object] = []
    fake_drag.during = lambda _drag: seen.append(MultiRowTabBar.drag_source())

    bar.start_cross_window_drag(2)

    assert seen == [(bar, 2)]
    assert MultiRowTabBar.drag_source() is None


def test_start_drag_forgets_the_source_even_if_the_drag_raises(
    bar: MultiRowTabBar, fake_drag
) -> None:
    """ドラッグ中に例外が飛んでも、ドラッグ元は残さない。"""
    fill(bar, 2, prefix="例外")

    def boom(_drag):
        raise RuntimeError("ドラッグが壊れた")

    fake_drag.during = boom
    with pytest.raises(RuntimeError):
        bar.start_cross_window_drag(0)

    assert MultiRowTabBar.drag_source() is None


def test_start_drag_hot_spot_is_in_logical_pixels(bar: MultiRowTabBar, fake_drag) -> None:
    """カーソルに付く絵の掴み位置は、論理座標（HiDPI 倍率をかけない）で渡す。

    デバイスピクセルで渡すと、拡大表示（2 倍）の画面で絵がカーソルから
    タブ半分ぶんずれる。ヘッドレスの画面は等倍なので、倍率だけ 2 倍の
    画面のふりをさせて確かめる。
    """
    fill(bar, 2, prefix="絵")
    rect = bar.tabRect(1)
    bar.devicePixelRatioF = lambda: 2.0

    bar.start_cross_window_drag(1)

    drag = fake_drag.created[0]
    assert drag.pixmap is not None
    # 絵そのものは 2 倍の解像度で描く（拡大画面でぼやけないように）。
    assert drag.pixmap.devicePixelRatio() == 2.0
    assert drag.pixmap.size() == rect.size() * 2
    # 掴み位置は倍率をかけない。
    assert drag.hot_spot == QPoint(rect.width() // 2, rect.height() // 2)


def test_start_drag_stops_the_own_mouse_tracking(bar: MultiRowTabBar, fake_drag) -> None:
    """Qt にドラッグを任せた後は、自前のマウス追跡で並べ替えを続けない。

    押下中の状態を残したままだと、ドラッグ中に届くマウスイベントで元の
    ウィンドウのタブが勝手に並べ替わる。
    """
    fill(bar, 3, prefix="追跡")
    moved: list[tuple[int, int]] = []
    bar.tabMoveRequested.connect(lambda f, t: moved.append((f, t)))

    press(bar, bar.tabRect(0).center())

    # ドラッグ中に届いたことにするマウス移動。
    fake_drag.during = lambda _drag: move(bar, bar.tabRect(2).center())

    bar.start_cross_window_drag(0)

    assert moved == []


def test_start_drag_tears_off_when_nobody_took_the_tab(bar: MultiRowTabBar, fake_drag) -> None:
    """誰も引き取らなければ、ドラッグ終了後に切り離しを要求する。"""
    fill(bar, 2, prefix="切り離し")
    torn: list[int] = []
    finished: list[bool] = []
    bar.tabTearOffRequested.connect(lambda index, _pos: torn.append(index))
    bar.tabDragFinished.connect(lambda: finished.append(True))

    bar.start_cross_window_drag(1)

    assert torn == [1]
    assert finished == [True]


def test_start_drag_does_not_tear_off_when_a_window_took_the_tab(
    bar: MultiRowTabBar, fake_drag
) -> None:
    """受け手が引き取れていれば切り離さない（合体できた場合）。"""
    fill(bar, 2, prefix="合体")
    torn: list[int] = []
    finished: list[bool] = []
    bar.tabTearOffRequested.connect(lambda index, _pos: torn.append(index))
    bar.tabDragFinished.connect(lambda: finished.append(True))

    fake_drag.during = lambda _drag: MultiRowTabBar.mark_drag_accepted()

    bar.start_cross_window_drag(1)

    assert torn == []
    assert finished == [True]


def test_drag_pixmap_of_a_missing_tab_is_none(bar: MultiRowTabBar) -> None:
    """絵を作れないタブ番号では ``None`` を返す（描こうとして落ちない）。

    ``_drag_pixmap()`` は「その位置の矩形」を元に絵を作るので、もう無い
    タブ番号では大きさゼロの矩形が返ってくる。ここで見送らないと、
    大きさゼロの ``QPixmap`` に ``QPainter`` を当てて ``self._tabs[index]``
    を読みに行き、``IndexError`` で **ドラッグを始めた瞬間にアプリが落ちる**。
    """
    fill(bar, 2, prefix="絵")

    assert bar._drag_pixmap(bar.count()) is None
    assert bar._drag_pixmap(-1) is None


def test_start_drag_without_a_pixmap_still_carries_the_tab(
    bar: MultiRowTabBar, fake_drag, monkeypatch
) -> None:
    """絵が作れなくても、ドラッグ自体は普通に始まる（見た目だけの機能）。"""
    fill(bar, 2, prefix="絵無し")
    monkeypatch.setattr(MultiRowTabBar, "_drag_pixmap", lambda self, index: None)

    bar.start_cross_window_drag(1)

    assert len(fake_drag.created) == 1
    assert fake_drag.created[0].mime.hasFormat(TAB_MIME_TYPE)
    assert fake_drag.created[0].pixmap is None


def test_start_drag_ignores_an_invalid_index(bar: MultiRowTabBar, fake_drag) -> None:
    """無効な位置ではドラッグを始めない（タブが減った直後など）。"""
    fill(bar, 2, prefix="無効")
    finished: list[bool] = []
    bar.tabDragFinished.connect(lambda: finished.append(True))

    bar.start_cross_window_drag(5)

    assert fake_drag.created == []
    assert finished == []


# ----------------------------------------------------------------------
# 並べ替えたときの「選択中のタブ」の追随
# ----------------------------------------------------------------------
def test_moving_a_tab_from_the_right_shifts_the_selection_right(bar: MultiRowTabBar) -> None:
    """選択より右のタブを選択より左へ動かすと、選択は 1 つ右へずれる。"""
    fill(bar, 4, prefix="追随")
    bar.setCurrentIndex(1)

    bar.moveTab(3, 0)

    assert bar.currentIndex() == 2
    assert bar.tabText(2) == "追随1.txt"


def test_moving_unrelated_tabs_keeps_the_selection(bar: MultiRowTabBar) -> None:
    """選択の外どうしの入れ替えでは、選択位置は動かない。"""
    fill(bar, 5, prefix="無関係")
    bar.setCurrentIndex(0)

    bar.moveTab(3, 4)

    assert bar.currentIndex() == 0
    assert bar.tabText(0) == "無関係0.txt"


def test_moving_a_tab_without_any_selection_is_safe(bar: MultiRowTabBar) -> None:
    """まだ何も選ばれていないタブバーでも、並べ替えで落ちない。"""
    fill(bar, 3, prefix="未選択")
    assert bar.currentIndex() == -1

    bar.moveTab(0, 2)

    assert bar.currentIndex() == -1
    assert bar.tabText(2) == "未選択0.txt"


def test_inserting_before_the_selection_shifts_it(bar: MultiRowTabBar) -> None:
    """選択中のタブより前に挿入すると、選択は同じタブを指したままずれる。"""
    fill(bar, 2, prefix="挿入")
    bar.setCurrentIndex(1)

    bar.insertTab(0, "割り込み.txt")

    assert bar.currentIndex() == 2
    assert bar.tabText(2) == "挿入1.txt"


# ----------------------------------------------------------------------
# 無効な位置を渡されたとき（黙って何もしない）
# ----------------------------------------------------------------------
def test_remove_tab_ignores_an_invalid_index(bar: MultiRowTabBar) -> None:
    fill(bar, 2, prefix="無効")
    bar.setCurrentIndex(1)
    changed: list[int] = []
    bar.currentChanged.connect(changed.append)

    bar.removeTab(7)
    bar.removeTab(-1)

    assert bar.count() == 2
    assert bar.currentIndex() == 1
    assert changed == []


def test_move_tab_ignores_an_invalid_index(bar: MultiRowTabBar) -> None:
    fill(bar, 3, prefix="無効")

    bar.moveTab(0, 9)
    bar.moveTab(-1, 0)

    assert [bar.tabText(i) for i in range(3)] == [f"無効{i}.txt" for i in range(3)]


def test_move_tab_to_the_same_place_does_nothing(bar: MultiRowTabBar) -> None:
    fill(bar, 3, prefix="同じ")
    bar.setCurrentIndex(1)

    bar.moveTab(1, 1)

    assert [bar.tabText(i) for i in range(3)] == [f"同じ{i}.txt" for i in range(3)]
    assert bar.currentIndex() == 1


def test_set_current_index_ignores_an_invalid_index(bar: MultiRowTabBar) -> None:
    fill(bar, 2, prefix="無効")
    bar.setCurrentIndex(0)
    changed: list[int] = []
    bar.currentChanged.connect(changed.append)

    bar.setCurrentIndex(5)

    assert bar.currentIndex() == 0
    assert changed == []


def test_set_current_index_on_an_empty_bar_is_safe(bar: MultiRowTabBar) -> None:
    """タブが 1 つも無いときに選択を指示されても、-1 のまま何も出さない。"""
    changed: list[int] = []
    bar.currentChanged.connect(changed.append)

    bar.setCurrentIndex(0)

    assert bar.currentIndex() == -1
    assert changed == []


# ----------------------------------------------------------------------
# 表示に関わるその他
# ----------------------------------------------------------------------
def test_very_narrow_tabs_have_no_close_button(bar: MultiRowTabBar) -> None:
    """固定幅を極端に狭くしたときは × を出さない（誤って閉じないように）。"""
    fill(bar, 3, prefix="狭い")
    bar.set_fixed_tab_width(MIN_TAB_WIDTH - 20)
    requested: list[int] = []
    bar.tabCloseRequested.connect(requested.append)

    assert bar.tabRect(0).width() < MIN_TAB_WIDTH
    assert bar.closeButtonRect(0).isEmpty()

    # × があったはずの右端を押しても閉じない（タブの選択になる）。
    right_edge = QPoint(bar.tabRect(0).right() - 2, bar.tabRect(0).center().y())
    press(bar, right_edge)
    release(bar, right_edge)

    assert requested == []
    assert bar.currentIndex() == 0


def test_changing_the_font_relayouts(bar: MultiRowTabBar) -> None:
    """フォントを変えるとタブの高さも配置も付いてくる（設定の文字サイズ変更）。"""
    fill(bar, 3, prefix="文字")
    before_height = bar.height()
    before_rect = bar.tabRect(0)

    font = QFont(bar.font())
    font.setPointSize(font.pointSize() + 12)
    bar.setFont(font)
    QApplication.processEvents()

    assert bar.height() > before_height
    assert bar.tabRect(0).height() > before_rect.height()
