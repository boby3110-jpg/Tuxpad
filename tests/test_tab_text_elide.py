"""タブ名が入り切らないときの省き方（冒頭を残して**末尾**を … で省く）のテスト.

実機フィードバック（引き継ぎ ⑩）：実際に扱うファイル名は
``有料7日目【返信】（最低1文言Ver）.txt`` のように**冒頭から順に情報が並ぶ**。
以前の真ん中を省く方式（``ElideMiddle``）では、肝心の見分けが付く部分が
ちょうど抜け落ちていた。冒頭を残す方式に変えたので、そこを固定する。

省かれた分（拡張子など）はツールチップにフルパスが出るので失われない
（``tests/test_tab_display.py`` がツールチップ側を見張っている）。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from editor_app.tab_bar import MIN_TAB_WIDTH, MultiRowTabBar

#: 実機で報告された、省略のされ方が問題になった実物に近い名前。
LONG_NAME = "有料7日目【返信】（最低1文言Ver）.txt"


@pytest.fixture
def bar(qtbot) -> MultiRowTabBar:
    widget = MultiRowTabBar()
    widget.setTabsClosable(True)
    qtbot.addWidget(widget)
    widget.show()
    widget.resize(800, widget.height())
    QApplication.processEvents()
    return widget


def head_of(shown: str) -> str:
    """省略記号より前（＝実際に読める部分）を返す。"""
    return shown[:-1] if shown.endswith("…") else shown


# ----------------------------------------------------------------------
# 省き方そのもの
# ----------------------------------------------------------------------


def test_long_name_keeps_the_head_and_elides_the_tail(bar: MultiRowTabBar) -> None:
    """入り切らない名前は、**冒頭から続けて**読める形で省略される。

    見えている部分が元の名前の**先頭からの並びそのもの**であることを確かめる。
    真ん中を省く方式ではここで落ちる（見えている部分が飛び飛びになるため）。
    """
    bar.addTab(LONG_NAME)
    bar.set_fixed_tab_width(140)

    shown = bar.tab_display_text(0)

    assert shown != LONG_NAME  # 省略が起きている前提のテスト
    assert shown.endswith("…")
    assert LONG_NAME.startswith(head_of(shown))


def test_more_of_the_head_is_visible_than_with_the_old_middle_elide(
    bar: MultiRowTabBar,
) -> None:
    """同じ幅でも、冒頭が以前より多く見える（真ん中省略との比較）。"""
    bar.addTab(LONG_NAME)
    bar.set_fixed_tab_width(140)

    width = bar._text_rect(0).width()
    old = bar.fontMetrics().elidedText(LONG_NAME, Qt.TextElideMode.ElideMiddle, width)

    assert len(head_of(bar.tab_display_text(0))) > len(old.split("…")[0])


def test_short_name_is_shown_as_is(bar: MultiRowTabBar) -> None:
    """収まる名前には … を付けない（余計な省略をしない）。"""
    bar.addTab("a.txt")

    assert bar.tab_display_text(0) == "a.txt"


def test_modified_mark_stays_visible(bar: MultiRowTabBar) -> None:
    """未保存の印（先頭の ``*``）は省略されない。

    印は名前の**冒頭**に付くので、末尾を省く方式なら必ず残る。
    どのタブが未保存かはタブを見て判断するため、これが消えると困る。
    """
    bar.addTab(f"*{LONG_NAME}")
    bar.set_fixed_tab_width(140)

    assert bar.tab_display_text(0).startswith("*有料")


def test_shown_text_fits_in_the_tab(bar: MultiRowTabBar) -> None:
    """どの幅でも、描く文字列は文字の場所（text_rect）に収まっている。

    はみ出すと隣のタブや閉じるボタンに文字が重なる。
    """
    bar.addTab(LONG_NAME)

    for width in (MIN_TAB_WIDTH, 100, 140, 200, 240):
        bar.set_fixed_tab_width(width)
        shown = bar.tab_display_text(0)
        assert bar.fontMetrics().horizontalAdvance(shown) <= bar._text_rect(0).width()


def test_wider_tab_shows_more_of_the_same_head(bar: MultiRowTabBar) -> None:
    """タブを広げると、**同じ冒頭のまま**読める部分が増える。

    ウィンドウの大きさを変えたときに、見える文字が入れ替わったり
    途中から別の場所が見えたりしない（読む位置が動かない）ことを見る。
    """
    bar.addTab(LONG_NAME)

    bar.set_fixed_tab_width(120)
    narrow = head_of(bar.tab_display_text(0))

    bar.set_fixed_tab_width(220)
    wide = head_of(bar.tab_display_text(0))

    assert len(wide) > len(narrow)
    assert wide.startswith(narrow)


def test_display_text_is_empty_when_there_is_no_room(bar: MultiRowTabBar) -> None:
    """文字を置く幅が残っていないときは空文字を返す。

    閉じるボタンぶんの余白を引くと幅が負になることがある。``elidedText()``
    に負の幅を渡した結果に頼らず、ここで打ち切っておく。
    """
    bar.addTab(LONG_NAME)
    bar._tabs[0].rect = QRect(0, 0, 20, 24)

    assert bar._text_rect(0).width() <= 0  # このテストの前提
    assert bar.tab_display_text(0) == ""


def test_display_text_of_a_missing_tab_is_empty(bar: MultiRowTabBar) -> None:
    """無いタブ番号では空文字を返す（``IndexError`` で落ちない）。"""
    bar.addTab("a.txt")

    assert bar.tab_display_text(-1) == ""
    assert bar.tab_display_text(bar.count()) == ""


# ----------------------------------------------------------------------
# 描画がこの計算を通っているか
# ----------------------------------------------------------------------


def test_painting_uses_the_display_text(bar: MultiRowTabBar, monkeypatch) -> None:
    """タブを描くときは ``tab_display_text()`` の結果を描いている。

    描画の中で別に省略し直していると、ここでの取り決め（冒頭を残す）が
    画面に効かないまま静かに戻ってしまう。
    """
    bar.addTab(LONG_NAME)
    bar.set_fixed_tab_width(140)

    asked: list[int] = []
    original = bar.tab_display_text

    def spy(index: int) -> str:
        asked.append(index)
        return original(index)

    monkeypatch.setattr(bar, "tab_display_text", spy)

    pixmap = QPixmap(bar.size())
    bar.render(pixmap)

    assert asked == [0]
