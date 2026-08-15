"""機能 5「タブをウィンドウ幅に応じて複数段に折り返し表示する」のテスト.

タブバー単体 (MultiRowTabBar) のレイアウト計算と、
MainWindow に組み込んだ状態での動作の両方を確認する。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from editor_app.main_window import MainWindow
from editor_app.tab_bar import MAX_TAB_WIDTH, MIN_TAB_WIDTH, MultiRowTabBar
from editor_app.tab_widget import MultiRowTabWidget

# ----------------------------------------------------------------------
# 補助
# ----------------------------------------------------------------------


@pytest.fixture
def bar(qtbot) -> MultiRowTabBar:
    """レイアウトに入れず、幅を直接指定できるタブバー。

    幅を変えたときの再配置を試すため、表示しておく必要がある
    (隠れたウィジェットには resizeEvent が届かない)。
    """
    widget = MultiRowTabBar()
    widget.setTabsClosable(True)
    widget.setMovable(True)
    qtbot.addWidget(widget)
    widget.show()
    set_width(widget, 400)
    return widget


def set_width(bar: MultiRowTabBar, width: int) -> None:
    """タブバーの幅を変え、再配置が終わるまで待つ。"""
    bar.resize(width, bar.height())
    QApplication.processEvents()
    assert bar.width() == width


def fill(bar: MultiRowTabBar, count: int, prefix: str = "ファイル") -> None:
    for i in range(count):
        bar.addTab(f"{prefix}{i}.txt")


def rows_of(bar: MultiRowTabBar) -> list[int]:
    return [bar.tabRow(i) for i in range(bar.count())]


def send_mouse(widget, event_type, pos: QPoint, button, buttons) -> None:
    """ボタンの押下状態を指定してマウスイベントを送る。

    ``QTest.mouseMove()`` はボタンを押しっぱなしの状態を再現できないため、
    ドラッグの検証ではイベントを自分で組み立てて送る。
    """
    event = QMouseEvent(
        event_type,
        QPointF(pos),
        QPointF(widget.mapToGlobal(pos)),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def click(widget, pos: QPoint, button=Qt.MouseButton.LeftButton) -> None:
    send_mouse(widget, QEvent.Type.MouseButtonPress, pos, button, button)
    send_mouse(widget, QEvent.Type.MouseButtonRelease, pos, button, Qt.MouseButton.NoButton)


# ----------------------------------------------------------------------
# 折り返しのレイアウト
# ----------------------------------------------------------------------
def test_single_tab_is_one_row(bar: MultiRowTabBar) -> None:
    """タブが 1 つなら 1 段。"""
    bar.addTab("無題-1")
    assert bar.rowCount() == 1
    assert rows_of(bar) == [0]


def test_no_tabs_still_has_one_row_height(bar: MultiRowTabBar) -> None:
    """タブが 0 個でも 1 段ぶんの高さを保つ (レイアウトが跳ねないように)。"""
    assert bar.count() == 0
    assert bar.rowCount() == 1
    assert bar.height() > 0


def test_tabs_wrap_to_multiple_rows(bar: MultiRowTabBar) -> None:
    """幅に入り切らないタブは次の段へ折り返される。"""
    fill(bar, 20)
    assert bar.rowCount() > 1


def test_rows_never_decrease_along_tab_order(bar: MultiRowTabBar) -> None:
    """タブは左上から順に並ぶ (段番号がタブ順に戻ることはない)。"""
    fill(bar, 20)
    rows = rows_of(bar)
    assert rows == sorted(rows)
    assert rows[0] == 0
    # 段番号は飛ばさずに 1 ずつ増える。
    assert set(rows) == set(range(bar.rowCount()))


def test_every_tab_fits_inside_the_width(bar: MultiRowTabBar) -> None:
    """どのタブも幅からはみ出さない (= 全タブが見えている)。"""
    fill(bar, 20, prefix="とても長い名前のファイル")
    for i in range(bar.count()):
        rect = bar.tabRect(i)
        assert rect.left() >= 0
        assert rect.right() <= bar.width()


def test_tabs_in_the_same_row_do_not_overlap(bar: MultiRowTabBar) -> None:
    """同じ段のタブ同士が重ならない。"""
    fill(bar, 20)
    previous_right = {}
    for i in range(bar.count()):
        rect = bar.tabRect(i)
        row = bar.tabRow(i)
        if row in previous_right:
            assert rect.left() > previous_right[row]
        previous_right[row] = rect.right()


def test_narrower_width_means_more_rows(bar: MultiRowTabBar) -> None:
    """幅を狭くすると段が増える。"""
    fill(bar, 12)
    wide_rows = bar.rowCount()

    set_width(bar, 200)
    assert bar.rowCount() > wide_rows


def test_wider_width_means_fewer_rows(bar: MultiRowTabBar) -> None:
    """幅を広げると段が減る (再配置される)。"""
    set_width(bar, 200)
    fill(bar, 12)
    narrow_rows = bar.rowCount()

    set_width(bar, 1200)
    assert bar.rowCount() < narrow_rows


def test_height_grows_with_rows(bar: MultiRowTabBar) -> None:
    """段が増えるとタブバー自身の高さも増える。"""
    bar.addTab("無題-1")
    one_row_height = bar.height()

    fill(bar, 20)
    assert bar.rowCount() > 1
    assert bar.height() > one_row_height
    # 段数にほぼ比例した高さになっている。
    assert bar.height() >= one_row_height * bar.rowCount()


def test_long_name_is_capped_at_max_width(bar: MultiRowTabBar) -> None:
    """名前が長くてもタブ幅は上限で頭打ちになる (省略表示する)。"""
    set_width(bar, 1200)
    bar.addTab("非常に長いファイル名" * 10)
    assert bar.tabRect(0).width() <= MAX_TAB_WIDTH


def test_short_name_has_minimum_width(bar: MultiRowTabBar) -> None:
    """名前が短くても押しやすい最小幅を確保する。"""
    bar.addTab("a")
    assert bar.tabRect(0).width() >= MIN_TAB_WIDTH


def test_set_tab_text_relayouts(bar: MultiRowTabBar) -> None:
    """タブ名を変えると幅が変わり、配置し直される。"""
    bar.addTab("a.txt")
    bar.addTab("b.txt")
    before = bar.tabRect(0).width()

    bar.setTabText(0, "とても長くなったファイル名.txt")
    after = bar.tabRect(0).width()
    assert after > before
    # 後ろのタブも押し出されて動く。
    assert bar.tabRect(1).left() > before


# ----------------------------------------------------------------------
# 選択・閉じる・並べ替え
# ----------------------------------------------------------------------
def test_click_selects_tab_in_second_row(bar: MultiRowTabBar) -> None:
    """2 段目のタブをクリックしても選択できる。"""
    fill(bar, 20)
    bar.setCurrentIndex(0)
    second_row = next(i for i in range(bar.count()) if bar.tabRow(i) == 1)

    click(bar, bar.tabRect(second_row).center())
    assert bar.currentIndex() == second_row


def test_click_emits_current_changed(bar: MultiRowTabBar, qtbot) -> None:
    """クリックで currentChanged が出る。"""
    fill(bar, 3)
    bar.setCurrentIndex(0)
    with qtbot.waitSignal(bar.currentChanged, timeout=1000) as blocker:
        click(bar, bar.tabRect(2).center())
    assert blocker.args == [2]


def test_click_on_empty_area_keeps_selection(bar: MultiRowTabBar) -> None:
    """タブの無い余白をクリックしても選択は変わらない。"""
    bar.addTab("a.txt")
    bar.setCurrentIndex(0)
    click(bar, QPoint(bar.width() - 5, bar.height() - 2))
    assert bar.currentIndex() == 0


def test_close_button_click_requests_close(bar: MultiRowTabBar, qtbot) -> None:
    """閉じるボタンを押すと tabCloseRequested が出る。"""
    fill(bar, 3)
    with qtbot.waitSignal(bar.tabCloseRequested, timeout=1000) as blocker:
        click(bar, bar.closeButtonRect(1).center())
    assert blocker.args == [1]


def test_close_button_click_does_not_switch_tab(bar: MultiRowTabBar) -> None:
    """閉じるボタンを押しただけではタブは切り替わらない。"""
    fill(bar, 3)
    bar.setCurrentIndex(0)
    click(bar, bar.closeButtonRect(2).center())
    assert bar.currentIndex() == 0


def test_middle_click_requests_close(bar: MultiRowTabBar, qtbot) -> None:
    """中クリックでもタブを閉じられる。"""
    fill(bar, 3)
    with qtbot.waitSignal(bar.tabCloseRequested, timeout=1000) as blocker:
        click(bar, bar.tabRect(2).center(), button=Qt.MouseButton.MiddleButton)
    assert blocker.args == [2]


def test_close_button_is_absent_when_not_closable(qtbot) -> None:
    """閉じるボタンを出さない設定なら当たり判定も無い。"""
    plain = MultiRowTabBar()
    qtbot.addWidget(plain)
    plain.resize(400, 30)
    plain.addTab("a.txt")
    assert plain.closeButtonRect(0).isEmpty()


def test_remove_tab_shifts_selection(bar: MultiRowTabBar) -> None:
    """選択中のタブを取り除くと、隣のタブが選択される。"""
    fill(bar, 3)
    bar.setCurrentIndex(2)
    bar.removeTab(2)
    assert bar.currentIndex() == 1

    bar.setCurrentIndex(0)
    bar.removeTab(0)
    assert bar.currentIndex() == 0
    assert bar.count() == 1


def test_remove_last_tab_clears_selection(bar: MultiRowTabBar) -> None:
    """最後のタブを取り除くと選択なし (-1) になる。"""
    bar.addTab("a.txt")
    bar.removeTab(0)
    assert bar.currentIndex() == -1


def test_removing_current_tab_emits_current_changed(bar: MultiRowTabBar) -> None:
    """選択中のタブを取り除いたら、番号が変わらなくても currentChanged が出る。

    タブが 3 つあって真ん中を選んでいるときにそれを消すと、選択番号は 1 の
    ままでも中身は別の文書に変わる。番号だけを見て握りつぶすと、表示中の
    文書が変わったことが誰にも伝わらない（ウィンドウタイトルが消したタブの
    名前のまま残る不具合があった）。
    """
    fill(bar, 3)
    bar.setCurrentIndex(1)

    emitted: list[int] = []
    bar.currentChanged.connect(emitted.append)
    bar.removeTab(1)

    assert bar.currentIndex() == 1
    assert bar.tabText(1) == "ファイル2.txt"
    assert emitted == [1]


def test_removing_other_tab_does_not_emit_current_changed(bar: MultiRowTabBar) -> None:
    """選択していないタブを取り除いただけなら currentChanged は出さない。"""
    fill(bar, 3)
    bar.setCurrentIndex(0)

    emitted: list[int] = []
    bar.currentChanged.connect(emitted.append)
    bar.removeTab(2)

    assert bar.currentIndex() == 0
    assert emitted == []


def test_move_tab_keeps_the_selected_tab(bar: MultiRowTabBar) -> None:
    """並べ替えても「選択されているタブ」自体は変わらない。"""
    fill(bar, 4)
    bar.setCurrentIndex(0)
    bar.moveTab(0, 2)
    assert bar.currentIndex() == 2
    assert bar.tabText(2) == "ファイル0.txt"


def test_drag_reorders_tabs(bar: MultiRowTabBar) -> None:
    """ドラッグでタブを並べ替えられる。"""
    moved: list[tuple[int, int]] = []
    bar.tabMoveRequested.connect(lambda f, t: (moved.append((f, t)), bar.moveTab(f, t)))
    fill(bar, 4, prefix="ドラッグ対象")

    start = bar.tabRect(0).center()
    goal = bar.tabRect(1).center()
    send_mouse(
        bar,
        QEvent.Type.MouseButtonPress,
        start,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
    )
    send_mouse(
        bar,
        QEvent.Type.MouseMove,
        goal,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )
    send_mouse(
        bar,
        QEvent.Type.MouseButtonRelease,
        goal,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
    )

    assert moved == [(0, 1)]
    assert bar.tabText(1) == "ドラッグ対象0.txt"


def test_drag_is_ignored_when_not_movable(qtbot) -> None:
    """並べ替え禁止のときはドラッグしても動かない。"""
    fixed = MultiRowTabBar()
    qtbot.addWidget(fixed)
    fixed.resize(400, 30)
    fill(fixed, 3, prefix="固定")
    moved: list[tuple[int, int]] = []
    fixed.tabMoveRequested.connect(lambda f, t: moved.append((f, t)))

    send_mouse(
        fixed,
        QEvent.Type.MouseButtonPress,
        fixed.tabRect(0).center(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
    )
    send_mouse(
        fixed,
        QEvent.Type.MouseMove,
        fixed.tabRect(2).center(),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )
    assert moved == []


def test_tab_at_returns_minus_one_outside(bar: MultiRowTabBar) -> None:
    """タブが無い場所では tabAt() が -1 を返す。"""
    bar.addTab("a.txt")
    assert bar.tabAt(bar.tabRect(0).center()) == 0
    assert bar.tabAt(QPoint(bar.width() - 2, bar.height() - 1)) == -1


# ----------------------------------------------------------------------
# MultiRowTabWidget (タブバー + ページ) の同期
# ----------------------------------------------------------------------
@pytest.fixture
def tab_widget(qtbot) -> MultiRowTabWidget:
    widget = MultiRowTabWidget()
    widget.setTabsClosable(True)
    widget.setMovable(True)
    qtbot.addWidget(widget)
    widget.resize(400, 300)
    return widget


def add_page(tab_widget: MultiRowTabWidget, name: str):
    from PySide6.QtWidgets import QLabel

    page = QLabel(name)
    page.setObjectName(name)
    tab_widget.addTab(page, name)
    return page


def test_first_tab_becomes_current(tab_widget: MultiRowTabWidget) -> None:
    """最初のタブを追加すると、それが表示される。"""
    page = add_page(tab_widget, "1つ目")
    assert tab_widget.currentIndex() == 0
    assert tab_widget.currentWidget() is page


def test_adding_tab_does_not_switch(tab_widget: MultiRowTabWidget) -> None:
    """タブを追加しただけでは表示は切り替わらない (QTabWidget と同じ)。"""
    first = add_page(tab_widget, "1つ目")
    add_page(tab_widget, "2つ目")
    assert tab_widget.currentWidget() is first


def test_set_current_widget_switches_page(tab_widget: MultiRowTabWidget) -> None:
    """setCurrentWidget() でページとタブの選択が揃って動く。"""
    add_page(tab_widget, "1つ目")
    second = add_page(tab_widget, "2つ目")

    tab_widget.setCurrentWidget(second)
    assert tab_widget.currentIndex() == 1
    assert tab_widget.stack().currentWidget() is second
    assert tab_widget.tabBar().currentIndex() == 1


def test_bar_click_switches_page(tab_widget: MultiRowTabWidget) -> None:
    """タブバーをクリックすると、対応するページが表示される。"""
    add_page(tab_widget, "1つ目")
    second = add_page(tab_widget, "2つ目")

    click(tab_widget.tabBar(), tab_widget.tabBar().tabRect(1).center())
    assert tab_widget.currentWidget() is second
    assert tab_widget.stack().currentWidget() is second


def test_remove_tab_keeps_bar_and_stack_in_sync(tab_widget: MultiRowTabWidget) -> None:
    """タブを取り除いてもタブバーとページの並びがずれない。"""
    for name in ["1つ目", "2つ目", "3つ目"]:
        add_page(tab_widget, name)

    tab_widget.removeTab(1)
    assert tab_widget.count() == 2
    assert [tab_widget.tabText(i) for i in range(2)] == ["1つ目", "3つ目"]
    assert [tab_widget.widget(i).objectName() for i in range(2)] == ["1つ目", "3つ目"]


def test_move_tab_moves_the_page_too(tab_widget: MultiRowTabWidget) -> None:
    """並べ替えるとページも一緒に動き、表示中のページは変わらない。"""
    for name in ["1つ目", "2つ目", "3つ目"]:
        add_page(tab_widget, name)
    current = tab_widget.currentWidget()

    tab_widget.moveTab(0, 2)
    assert [tab_widget.tabText(i) for i in range(3)] == ["2つ目", "3つ目", "1つ目"]
    assert [tab_widget.widget(i).objectName() for i in range(3)] == ["2つ目", "3つ目", "1つ目"]
    assert tab_widget.currentWidget() is current
    assert tab_widget.currentIndex() == 2


def test_insert_tab_out_of_range_keeps_bar_and_stack_in_sync(
    tab_widget: MultiRowTabWidget,
) -> None:
    """範囲外の位置に挿入されても、タブの名前と中身がずれない。

    タブバー側 (:class:`MultiRowTabBar`) と ``QStackedWidget`` は**範囲外の
    丸め方が違う**ため、``MultiRowTabWidget`` 側で先に丸めておかないと
    並びがずれる。実測すると ``QStackedWidget.insertWidget(-1, w)`` は
    **末尾に足す**のに対し、タブバー側は ``max(0, ...)`` で**先頭**に足す
    （素の Python の ``list.insert(-1, x)`` なら「最後の 1 つ手前」)。
    つまり負の位置で挿入すると、**タブの名前と表示される中身が入れ替わる**。
    """
    from PySide6.QtWidgets import QLabel

    for name in ["1つ目", "2つ目"]:
        add_page(tab_widget, name)

    too_small = QLabel("負の位置")
    too_small.setObjectName("負の位置")
    assert tab_widget.insertTab(-5, too_small, "負の位置") == 0

    too_large = QLabel("大きすぎる位置")
    too_large.setObjectName("大きすぎる位置")
    assert tab_widget.insertTab(99, too_large, "大きすぎる位置") == 3

    expected = ["負の位置", "1つ目", "2つ目", "大きすぎる位置"]
    assert [tab_widget.tabText(i) for i in range(4)] == expected
    assert [tab_widget.widget(i).objectName() for i in range(4)] == expected


def test_remove_tab_with_stale_index_does_nothing(
    tab_widget: MultiRowTabWidget,
) -> None:
    """もう無いタブ番号で閉じる要求が来ても、別のタブを巻き込まない。

    閉じる要求 (``tabCloseRequested``) はタブ番号で飛んでくるが、その番号は
    **要求を出した時点**のもの。中クリックや × を押したあとに、そのタブが
    別のウィンドウへ移された・先に閉じられた、という順番になると、
    存在しない番号のまま届く。ここで黙って見送らないと、**利用者が閉じる
    つもりの無かったタブが消える**（＝未保存の確認も出ないまま消えうる）。
    """
    for name in ["1つ目", "2つ目"]:
        add_page(tab_widget, name)

    tab_widget.removeTab(5)  # 既に無い番号
    tab_widget.removeTab(-1)

    assert tab_widget.count() == 2
    assert [tab_widget.tabText(i) for i in range(2)] == ["1つ目", "2つ目"]


def test_move_tab_with_stale_index_does_nothing(
    tab_widget: MultiRowTabWidget,
) -> None:
    """もう無いタブ番号での並べ替えは、並びを変えずに何もしない。

    並べ替えの番号はドラッグ中のマウス位置から計算するので、途中でタブが
    増減すると範囲外になりうる。ここで見送らないと、タブバーとページの
    並びがずれて**タブの名前と中身が食い違う**（別のファイルを編集して
    しまう）。しかも移動元が無い場合は ``QStackedWidget.removeWidget(None)``
    を呼ぶことになり、実測では**例外ではなくプロセスごと落ちる**
    （Segmentation fault。この確認を書くにあたって、判定を外して実際に
    確かめた）＝編集中のタブが全部消える。
    """
    for name in ["1つ目", "2つ目", "3つ目"]:
        add_page(tab_widget, name)
    moved: list[tuple[int, int]] = []
    tab_widget.tabMoved.connect(lambda a, b: moved.append((a, b)))

    tab_widget.moveTab(0, 9)  # 移動先が無い
    tab_widget.moveTab(9, 0)  # 移動元が無い

    assert [tab_widget.tabText(i) for i in range(3)] == ["1つ目", "2つ目", "3つ目"]
    assert [tab_widget.widget(i).objectName() for i in range(3)] == [
        "1つ目",
        "2つ目",
        "3つ目",
    ]
    assert moved == []


def test_move_tab_to_the_same_place_is_a_no_op(
    tab_widget: MultiRowTabWidget,
) -> None:
    """元の位置に落としただけのドラッグでは、移動したことにしない。

    ``tabMoved`` は「タブの並びが変わった」合図で、``MainWindow`` はこれを
    受けて**開いている全タブを検索し直す**（検索結果はタブの順番で位置を
    覚えているため）。つかんで戻しただけのドラッグは日常的に起きるので、
    ここで区別しないと**そのたびに全タブの再検索が走る**。
    """
    for name in ["1つ目", "2つ目", "3つ目"]:
        add_page(tab_widget, name)
    moved: list[tuple[int, int]] = []
    tab_widget.tabMoved.connect(lambda a, b: moved.append((a, b)))

    tab_widget.moveTab(1, 1)

    assert [tab_widget.tabText(i) for i in range(3)] == ["1つ目", "2つ目", "3つ目"]
    assert moved == []


# ----------------------------------------------------------------------
# MainWindow に組み込んだ状態
# ----------------------------------------------------------------------
def test_window_uses_multirow_tab_bar(window: MainWindow) -> None:
    """MainWindow のタブバーは独自の多段タブバーになっている。"""
    assert isinstance(window.tabs, MultiRowTabWidget)
    assert isinstance(window.tabs.tabBar(), MultiRowTabBar)
    assert window.tabs.tabsClosable() is True
    assert window.tabs.isMovable() is True


def test_many_tabs_wrap_in_window(window: MainWindow) -> None:
    """タブをたくさん開くとウィンドウ内で複数段になる。"""
    for _ in range(30):
        window.new_file()
    assert window.tabs.count() == 31
    assert window.tabs.rowCount() > 1


def test_close_button_closes_tab_in_window(window: MainWindow) -> None:
    """タブバーの × をクリックすると、そのタブが閉じる。"""
    window.new_file()
    window.new_file()
    assert window.tabs.count() == 3

    bar = window.tabs.tabBar()
    click(bar, bar.closeButtonRect(1).center())
    assert window.tabs.count() == 2
    assert [e.display_name for e in window.editors()] == ["無題-1", "無題-3"]


def test_middle_click_closes_tab_in_window(window: MainWindow) -> None:
    """中クリックでもタブが閉じる。"""
    window.new_file()
    bar = window.tabs.tabBar()
    click(bar, bar.tabRect(0).center(), button=Qt.MouseButton.MiddleButton)
    assert [e.display_name for e in window.editors()] == ["無題-2"]


def test_clicking_tab_updates_window_title(window: MainWindow) -> None:
    """タブをクリックするとウィンドウタイトルも切り替わる。"""
    window.new_file()  # 無題-2 が現在のタブ
    assert window.windowTitle().startswith("無題-2")

    bar = window.tabs.tabBar()
    click(bar, bar.tabRect(0).center())
    assert window.current_editor().display_name == "無題-1"
    assert window.windowTitle().startswith("無題-1")


def test_tab_bar_row_count_follows_window_width(window: MainWindow) -> None:
    """ウィンドウを狭くするとタブの段数が増える (タブバーは幅に追従する)。"""
    for _ in range(15):
        window.new_file()
    bar = window.tabs.tabBar()

    window.resize(1200, 700)
    QApplication.processEvents()
    wide_rows = bar.rowCount()

    window.resize(400, 700)
    QApplication.processEvents()
    assert bar.width() < 1200  # タブバーはウィンドウ幅いっぱいに広がる
    assert bar.rowCount() > wide_rows


# ----------------------------------------------------------------------
# ウィンドウ間ドラッグ（Qt 標準の D&D への引き継ぎ）
#
# 本物のクロスウィンドウ・ドラッグはヘッドレスでは再現できないので、
# 「タブバーの外へ出たら Qt 標準のドラッグを始めるか」「ドラッグの結果を
# どう解釈するか」の 2 点に分けて確認する。実機での実ドラッグ確認は
# 利用者に委ねる（PROGRESS.md 参照）。
# ----------------------------------------------------------------------
def test_drag_out_of_the_bar_starts_a_qt_drag(bar: MultiRowTabBar) -> None:
    """タブをタブバーの外へ動かしたら、Qt 標準のドラッグへ引き継ぐ。"""
    fill(bar, 3, prefix="持ち出す")
    started: list[int] = []
    bar.start_cross_window_drag = started.append  # 本物のドラッグは起こさない

    send_mouse(
        bar,
        QEvent.Type.MouseButtonPress,
        bar.tabRect(1).center(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
    )
    send_mouse(
        bar,
        QEvent.Type.MouseMove,
        QPoint(bar.tabRect(1).center().x(), bar.height() + 50),  # バーの外（下）
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )

    assert started == [1]


def test_drag_inside_the_bar_does_not_start_a_qt_drag(bar: MultiRowTabBar) -> None:
    """タブバーの中で動かしている間は、今までどおりの並べ替えのまま。"""
    fill(bar, 3, prefix="並べ替え")
    started: list[int] = []
    bar.start_cross_window_drag = started.append
    moved: list[tuple[int, int]] = []
    bar.tabMoveRequested.connect(lambda f, t: (moved.append((f, t)), bar.moveTab(f, t)))

    send_mouse(
        bar,
        QEvent.Type.MouseButtonPress,
        bar.tabRect(0).center(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
    )
    send_mouse(
        bar,
        QEvent.Type.MouseMove,
        bar.tabRect(1).center(),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )

    assert started == []
    assert moved == [(0, 1)]


def test_drag_out_of_the_bar_is_ignored_when_not_movable(qtbot) -> None:
    """並べ替え禁止のタブバーからは、ウィンドウ間ドラッグも始めない。"""
    fixed = MultiRowTabBar()
    qtbot.addWidget(fixed)
    fixed.show()
    set_width(fixed, 400)
    fill(fixed, 2, prefix="固定")
    started: list[int] = []
    fixed.start_cross_window_drag = started.append

    send_mouse(
        fixed,
        QEvent.Type.MouseButtonPress,
        fixed.tabRect(0).center(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
    )
    send_mouse(
        fixed,
        QEvent.Type.MouseMove,
        QPoint(fixed.tabRect(0).center().x(), fixed.height() + 50),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )

    assert started == []


def test_finish_drag_requests_tear_off_when_dropped_nowhere(bar: MultiRowTabBar) -> None:
    fill(bar, 2)
    tear_off: list[int] = []
    finished: list[bool] = []
    bar.tabTearOffRequested.connect(lambda index, _pos: tear_off.append(index))
    bar.tabDragFinished.connect(lambda: finished.append(True))

    bar.finish_cross_window_drag(1, Qt.DropAction.IgnoreAction, QPoint(10, 20))

    assert tear_off == [1]
    assert finished == [True]


def test_finish_drag_after_a_move_does_not_request_tear_off(bar: MultiRowTabBar) -> None:
    fill(bar, 2)
    tear_off: list[int] = []
    finished: list[bool] = []
    bar.tabTearOffRequested.connect(lambda index, _pos: tear_off.append(index))
    bar.tabDragFinished.connect(lambda: finished.append(True))

    # 受け手が引き取れた状況を再現する（合体できた）。切り離しの判断は
    # QDrag.exec() の戻り値ではなくこのフラグで行う（Wayland 対策）。
    MultiRowTabBar.mark_drag_accepted()
    bar.finish_cross_window_drag(1, Qt.DropAction.MoveAction, QPoint(10, 20))

    assert tear_off == []
    assert finished == [True]  # 移動できた場合も「終わった」ことは伝える


def test_finish_drag_tears_off_even_when_action_is_not_ignore(bar: MultiRowTabBar) -> None:
    """Wayland ではウィンドウの外へ落としても IgnoreAction が返らない。

    実機報告:「ウィンドウ外への D&D では（分離が）動作しない」。戻り値だけを
    見ていると、外へ落としたのに合体扱いになり切り離しが起きなかった。
    どのウィンドウにも引き取られていない（フラグが立っていない）なら、
    戻り値が MoveAction でも切り離すこと。
    """
    fill(bar, 2)
    tear_off: list[int] = []
    bar.tabTearOffRequested.connect(lambda index, _pos: tear_off.append(index))

    # 誰も引き取っていない（mark_drag_accepted を呼んでいない）状態。
    bar.finish_cross_window_drag(1, Qt.DropAction.MoveAction, QPoint(10, 20))

    assert tear_off == [1]


def test_drag_source_is_remembered_only_while_dragging(bar: MultiRowTabBar) -> None:
    fill(bar, 2)
    assert MultiRowTabBar.drag_source() is None

    MultiRowTabBar.set_drag_source(bar, 1)
    assert MultiRowTabBar.drag_source() == (bar, 1)

    MultiRowTabBar.set_drag_source(None)
    assert MultiRowTabBar.drag_source() is None
