"""タブが増えたり減ったり動いたりしたあとの「どのタブを選んでいるか」の帳簿。

機能 5 のタブバーは ``QTabBar`` を使わないフルスクラッチなので、**タブの並びが
変わったときに選択番号を自分で繰り上げ／繰り下げしている**
（:meth:`MultiRowTabBar.insertTab` / :meth:`~MultiRowTabBar.moveTab` と
``_shift_index``）。この帳簿がずれると、**選んでいるタブが勝手に別の文書へ
移る**——他のタブを 1 枚ドラッグして並べ替えただけで、編集していた本文が
別のファイルに変わる。しかも並べ替えでは ``currentChanged`` を出さない
決まりなので、**ウィンドウのタイトルだけ元のファイル名のまま残る**
（``removeTab`` の説明にある、実際に起きた不具合と同じ形）。

取り除き（``removeTab``）の側は ``tests/test_multirow_tabs.py`` が、
「選択より**前**への挿入」と「選択が無いときの並べ替え」は
``tests/test_tab_bar_interaction.py`` が既に見ている。2026-09-27（65 回目）の
変異検査で残っていたのは**等号の側の境目**だった——**ちょうど選択中の位置**へ
割り込まれたとき（``>=``）と、**ちょうど選択中の位置**へ他のタブが動いてきた
とき（``<=``）は、選択がずれてもテストが 1 件も赤くならなかった。

マウスの見た目（ホバー）の後片付けも、同じ 3 つのメソッドの仕事なので
ここで一緒に見る。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from editor_app.tab_bar import MultiRowTabBar
from editor_app.tab_widget import MultiRowTabWidget


@pytest.fixture
def bar(qtbot) -> MultiRowTabBar:
    """幅を直接指定できるタブバー（``test_multirow_tabs.py`` と同じ作り）。"""
    widget = MultiRowTabBar()
    widget.setTabsClosable(True)
    widget.setMovable(True)
    qtbot.addWidget(widget)
    # 隠れたウィジェットには resizeEvent が届かない（＝再配置が走らない）。
    widget.show()
    widget.resize(400, widget.height())
    QApplication.processEvents()
    return widget


@pytest.fixture
def tab_widget(qtbot) -> MultiRowTabWidget:
    widget = MultiRowTabWidget()
    widget.setTabsClosable(True)
    widget.setMovable(True)
    qtbot.addWidget(widget)
    widget.resize(400, 300)
    return widget


def fill(bar: MultiRowTabBar, count: int) -> None:
    for i in range(count):
        bar.addTab(f"ファイル{i}.txt")


def add_page(tab_widget: MultiRowTabWidget, name: str) -> QLabel:
    page = QLabel(name)
    page.setObjectName(name)
    tab_widget.addTab(page, name)
    return page


def selected_text(bar: MultiRowTabBar) -> str:
    return bar.tabText(bar.currentIndex())


# ----------------------------------------------------------------------
# 割り込み（`insertTab`）
# ----------------------------------------------------------------------
def test_insert_at_the_selected_position_keeps_the_same_tab_selected(
    bar: MultiRowTabBar,
) -> None:
    """ちょうど選んでいる位置に割り込まれても、選択は元のタブに付いていく。

    ``self._current >= index`` の ``>=`` が境目で、``>`` にすると
    **割り込んできたタブが選ばれたことになる**。選択番号は 1 のままなので
    ``currentChanged`` も出ず、**誰にも伝わらないまま表示中の文書が変わる**。
    選択より**前**への挿入は
    ``test_tab_bar_interaction.test_inserting_before_the_selection_shifts_it``
    が見ているが、そちらは ``>`` でも通る（ちょうど同じ位置のときだけ違う）。

    いま ``insertTab`` を途中の位置で呼ぶ経路はアプリ側に無い（タブは常に
    末尾へ足している）が、:class:`MultiRowTabWidget` は ``QTabWidget`` の
    代わりとして「``QTabBar`` と同じ振る舞い」を約束しているので、
    落とした位置へタブを差し込むようになったときに黙って壊れないよう
    ここで固定しておく。
    """
    fill(bar, 3)
    bar.setCurrentIndex(1)

    bar.insertTab(1, "割り込み.txt")

    assert bar.currentIndex() == 2
    assert selected_text(bar) == "ファイル1.txt"


def test_inserting_a_page_at_the_current_position_keeps_it_shown(
    tab_widget: MultiRowTabWidget,
) -> None:
    """ページごと割り込まれても、映っているページは変わらない。

    タブバーの選択番号だけがずれると ``_sync_stack()`` がそれに従うので、
    **映るページまで一緒にずれる**（タブの見た目と中身は揃ったまま、
    編集していた文書から勝手に離れる）。
    """
    for name in ["1つ目", "2つ目", "3つ目"]:
        add_page(tab_widget, name)
    tab_widget.setCurrentIndex(1)
    shown = tab_widget.currentWidget()

    page = QLabel("割り込み")
    page.setObjectName("割り込み")
    tab_widget.insertTab(1, page, "割り込み")

    assert tab_widget.currentWidget() is shown
    assert tab_widget.stack().currentWidget() is shown
    assert tab_widget.tabText(tab_widget.currentIndex()) == "2つ目"


# ----------------------------------------------------------------------
# 並べ替え（`moveTab` / `_shift_index`）
# ----------------------------------------------------------------------
def test_moving_another_tab_onto_the_selected_position_keeps_the_selection(
    bar: MultiRowTabBar,
) -> None:
    """選んでいないタブを「選択中の位置」へ後ろ向きに動かしても、選択は動かない。

    ``_shift_index`` の ``from_index < index <= to_index`` の ``<=`` が境目。
    ここを ``<`` にすると、**他のタブをドラッグしただけで編集中のタブが
    隣の文書に変わる**（移動先がちょうど選択中の位置のときだけ起きるので、
    ドラッグの止め方次第で起きたり起きなかったりする）。
    """
    fill(bar, 4)
    bar.setCurrentIndex(2)

    bar.moveTab(0, 2)  # ファイル0 を、選択中のファイル2 の位置へ

    assert bar.currentIndex() == 1
    assert selected_text(bar) == "ファイル2.txt"


def test_moving_another_tab_in_front_of_the_selected_one_keeps_the_selection(
    bar: MultiRowTabBar,
) -> None:
    """前向きの並べ替えでも同じ（``to_index <= index < from_index`` の境目）。

    向きによってずれ方が逆になるので、両方向とも見ておく。
    """
    fill(bar, 4)
    bar.setCurrentIndex(1)

    bar.moveTab(3, 1)  # ファイル3 を、選択中のファイル1 の位置へ

    assert bar.currentIndex() == 2
    assert selected_text(bar) == "ファイル1.txt"


def test_moving_another_page_keeps_the_shown_page_in_step(
    tab_widget: MultiRowTabWidget,
) -> None:
    """選んでいないページを動かしても、映っているページは変わらない。

    既存の並べ替えのテスト（``test_move_tab_keeps_the_shown_page_in_step``）は
    「**動かしたタブ自身**が選ばれている」場合だけを見ている。ここでは選んで
    いないタブを、ちょうど選択中の位置へ動かして、選択とページが一緒に
    付いてくることを両方向ぶん見る。
    """
    for name in ["1つ目", "2つ目", "3つ目", "4つ目"]:
        add_page(tab_widget, name)
    tab_widget.setCurrentIndex(2)
    shown = tab_widget.currentWidget()

    tab_widget.moveTab(0, 2)  # 後ろ向き（選択は 2 → 1 へ繰り下がる）
    assert tab_widget.currentIndex() == 1
    assert tab_widget.currentWidget() is shown
    assert tab_widget.stack().currentWidget() is shown

    tab_widget.moveTab(3, 1)  # 前向き（選択は 1 → 2 へ繰り上がる）
    assert tab_widget.currentIndex() == 2
    assert tab_widget.currentWidget() is shown
    assert tab_widget.stack().currentWidget() is shown
    assert tab_widget.tabText(tab_widget.currentIndex()) == "3つ目"


# ----------------------------------------------------------------------
# マウスの見た目の後片付け
# ----------------------------------------------------------------------
def test_removing_a_tab_clears_the_hover_mark(bar: MultiRowTabBar) -> None:
    """タブを取り除いたあと、消えたタブのホバーを引きずらない。

    ホバーは**番号で覚えている**ので、消したあとも残っていると
    **詰めてきた別のタブが浮いて見え、その × が押せそうに光る**
    （マウスはそこに無いのに）。`Ctrl+W` やメニューから閉じたときは
    マウスが動かないので、次に動かすまでそのまま残る。
    """
    fill(bar, 4)
    bar._update_hover(bar.closeButtonRect(3).center())
    assert (bar._hover_index, bar._hover_close) == (3, True)

    bar.removeTab(3)

    assert bar._hover_index == -1
    assert bar._hover_close is False


def test_reordering_clears_the_hover_mark(bar: MultiRowTabBar) -> None:
    """並べ替えたあとも同じ（動いていったタブの浮きを残さない）。

    並べ替えはドラッグの途中で呼ばれるので、**押下中の状態は消さずに
    ホバーだけ**解除する（押下を消すとドラッグがそこで途切れる）。
    """
    fill(bar, 4)
    bar._update_hover(bar.tabRect(3).center())
    assert bar._hover_index == 3

    bar.moveTab(0, 2)

    assert bar._hover_index == -1
    assert bar._hover_close is False


def test_inserting_a_tab_clears_the_hover_mark(bar: MultiRowTabBar) -> None:
    """タブが増えたあとも同じ（`Ctrl+N` / `Ctrl+O` で末尾に増えたとき）。

    足す側でも番号がずれうる（先頭に割り込めば以降のタブは 1 つずつ後ろへ
    動く）ので、ここでも浮きを消してから描き直す。
    """
    fill(bar, 3)
    bar._update_hover(bar.closeButtonRect(1).center())
    assert (bar._hover_index, bar._hover_close) == (1, True)

    bar.insertTab(0, "割り込み.txt")

    assert bar._hover_index == -1
    assert bar._hover_close is False
