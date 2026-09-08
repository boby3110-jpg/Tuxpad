"""タブ 1 枚の幅の決め方（``_tab_width()`` とその上限・下限）のテスト.

54 回目の申し送りで「見張りが薄いところ」として挙がっていた箇所。
タブに名前が読める形で出るかどうかは、最後は**幅の計算**で決まる
（幅が足りなければ ``tab_display_text()`` が省略する）ので、
省き方（``tests/test_tab_text_elide.py``）と折り返し
（``tests/test_multirow_tabs.py``）の間に落ちていた「幅そのもの」を固定する。

当てずっぽうで足したのではなく、**先に変異を当てて生き残ることを確かめた**
4 点だけを扱う（55 回目）。

1. ``TEXT_SLACK`` … これを外すと、収まるはずの名前が丸め誤差で
   「rea....md」のように省略される。実測では 142 個の名前のうち **65 個**が
   そうなったのに、テストは全て緑のままだった。
2. ``MIN_TAB_WIDTH`` … 下限を下げると、閉じるボタンのぶんを引いた残りが
   0 px になり、**名前の描画領域そのものが消える**（× だけのタブになる）。
   下限を 40 にしても全テストが緑のままだった。
3. ``set_fixed_tab_width(0)`` … 0 や負の値を渡したときの下駄（``max(..., 1)``）を
   外しても全テストが緑のままだった。幅 0 のタブは押すことも掴むこともできない。
4. 折り返しの境目（``x + width > available``）… ``>=`` に変えても全テストが
   緑のままだった。ぴったり収まるタブが次の段へ送られ、段の右端が空く。

なお、同時に試して**生き残ったが穴ではない**ものが 3 つある（次回また
同じ検討をしないように残しておく）。

- ``MAX_TAB_WIDTH`` の値そのもの（240 → 320）… テスト側も定数を import して
  いるので、値を変えると期待値も一緒に動く。「何 px が良いか」は好みの問題で、
  固定すべき振る舞いではない。
- ``if x > 0 and ...`` の ``x > 0``（折り返しの先頭での空回り防止）…
  ``_tab_width()`` が幅を ``available`` で頭打ちにしているので、``x == 0`` の
  ときに右辺が真になることはない（到達しない条件）。頭打ちを外したときの
  保険として残っている。
- ``tab_display_text()`` の ``if width <= 0``（描画領域が負になる場合）…
  今の Qt は負の幅でも空文字を返すので、外しても結果は同じ。
  「文書化されていない振る舞いに寄りかからない」ための保険。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from editor_app.tab_bar import (
    CLOSE_SIZE,
    CLOSE_SPACING,
    H_PADDING,
    MAX_TAB_WIDTH,
    MIN_TAB_WIDTH,
    TAB_SPACING,
    TEXT_SLACK,
    MultiRowTabBar,
)

#: 実機で扱っている実物に近い名前（冒頭から順に情報が並ぶ）。
#: この先頭 n 文字を総当たりして、幅の計算を色々な長さで試す。
REAL_NAME = "有料7日目【返信】（最低1文言Ver）abcdefghijklmnopqrstuvwxyz0123456789"

ELLIPSIS = "…"


@pytest.fixture
def bar(qtbot) -> MultiRowTabBar:
    widget = MultiRowTabBar()
    widget.setTabsClosable(True)
    qtbot.addWidget(widget)
    widget.show()
    widget.resize(1200, widget.height())
    QApplication.processEvents()
    return widget


def names_that_fit(bar: MultiRowTabBar) -> list[str]:
    """上限（``MAX_TAB_WIDTH``）に当たらない長さの名前を集める。

    1 文字の幅はフォント次第なので、長さを決め打ちにせず**実際に測って**選ぶ。
    ``_tab_width()`` は ``horizontalAdvance() + TEXT_SLACK + 2*H_PADDING
    (+ CLOSE_SIZE + CLOSE_SPACING)`` で幅を決めるので、``room`` にも
    ``TEXT_SLACK`` を含めないと、この余白ぶんだけ「収まる」と誤判定した
    名前が実際には上限ちょうどに達してしまう（2026-09-08、公開リポ CI で
    日本語名が引っかかって判明。フォント依存ではなく計算式の考慮漏れだった）。
    """
    metrics = bar.fontMetrics()
    room = MAX_TAB_WIDTH - TEXT_SLACK - 2 * H_PADDING - CLOSE_SIZE - CLOSE_SPACING
    candidates = [REAL_NAME[:n] for n in range(1, len(REAL_NAME) + 1)]
    candidates += ["あ" * n + ".txt" for n in range(1, 12)]
    candidates += ["a" * n + ".md" for n in range(1, 24)]
    candidates += ["readme" + "x" * n + ".md" for n in range(1, 12)]
    return [name for name in candidates if metrics.horizontalAdvance(name) < room]


# ----------------------------------------------------------------------
# 収まる名前は省略されない（``TEXT_SLACK``）
# ----------------------------------------------------------------------


def test_a_name_that_fits_is_shown_in_full(bar: MultiRowTabBar) -> None:
    """上限に当たらない名前は、1 文字も省かずにそのまま出る。

    ``horizontalAdvance()`` の値ちょうどで幅を決めると、``elidedText()`` が
    丸め誤差で省いてしまう（``TEXT_SLACK`` を外すと、下の名前の多くが
    「有料7日目【返信】（最低1文…」のように尻切れになる）。
    **省略が起きてよいのは上限に当たったときだけ**、という線を引く。
    """
    checked = 0
    for name in names_that_fit(bar):
        index = bar.addTab(name)
        assert bar.tabRect(index).width() < MAX_TAB_WIDTH, name
        assert bar.tab_display_text(index) == name, name
        checked += 1

    # 総当たりの範囲が狂って「1 つも試していないのに緑」になるのを防ぐ。
    assert checked >= 30


def test_the_name_area_is_wider_than_the_name_itself(bar: MultiRowTabBar) -> None:
    """名前の描画領域は、測った文字幅より**必ず広い**（ぴったりにしない）。

    1 つ上のテストが見ているのは結果（省略されないこと）で、こちらはその理由。
    等号で足りてしまうと丸め誤差に負けるので、狭義の大小で固定する。
    """
    metrics = bar.fontMetrics()
    for name in names_that_fit(bar)[:40]:
        index = bar.addTab(name)
        assert bar._text_rect(index).width() > metrics.horizontalAdvance(name), name


# ----------------------------------------------------------------------
# 下限（``MIN_TAB_WIDTH``）
# ----------------------------------------------------------------------


def test_minimum_width_leaves_room_for_the_name_too(bar: MultiRowTabBar) -> None:
    """下限まで潰れたタブでも、× のほかに**名前の場所が残る**。

    下限は「閉じるボタンと左右の余白がちょうど収まる幅」では足りない。
    それだと名前の描画領域が 0 px になり、× だけが並ぶタブバーになる
    （どれがどのファイルか全く分からない）。少なくとも省略記号は出る幅、
    という形で下限の意味を固定する。
    """
    bar.addTab(REAL_NAME)
    bar.resize(20, bar.height())
    QApplication.processEvents()
    bar._relayout()  # 実際の幅に関わらず、計算そのものを確かめる

    assert bar.tabRect(0).width() == MIN_TAB_WIDTH
    assert bar._text_rect(0).width() > 0
    assert bar.tab_display_text(0) != ""

    # フォントに依らない形（省略記号の実測幅で）でも同じことを言っておく。
    reserved = 2 * H_PADDING + CLOSE_SIZE + CLOSE_SPACING
    assert MIN_TAB_WIDTH > reserved + bar.fontMetrics().horizontalAdvance(ELLIPSIS)


def test_minimum_width_keeps_the_close_button_reachable(bar: MultiRowTabBar) -> None:
    """下限まで潰れても × の当たり判定はタブの中に収まる（押して閉じられる）。"""
    bar.addTab("a.txt")
    bar.resize(20, bar.height())
    QApplication.processEvents()
    bar._relayout()

    tab = bar.tabRect(0)
    close = bar.closeButtonRect(0)
    assert not close.isEmpty()
    assert tab.contains(close)
    # 名前の描画範囲と × が重ならない（文字が × の下に潜らない）。
    assert bar._text_rect(0).right() < close.left()


# ----------------------------------------------------------------------
# 固定幅に無茶な値を渡したとき
# ----------------------------------------------------------------------


@pytest.mark.parametrize("width", [0, -1, -1000])
def test_fixed_width_of_zero_or_less_still_leaves_a_visible_tab(
    bar: MultiRowTabBar, width: int
) -> None:
    """固定幅に 0 や負の値を渡しても、タブは消えない（幅は 1 px 以上）。

    固定幅は利用者が指定した値をそのまま使う（上限・下限でクランプしない）
    決まりだが、0 や負の値だけは別。幅 0 のタブは押すことも掴むことも
    できず、**自動幅に戻す道が画面から消える**（メニューからは戻せるが、
    タブが見えない状態を作らないに越したことはない）。
    """
    bar.addTab("a.txt")
    bar.addTab("b.txt")

    bar.set_fixed_tab_width(width)

    for index in range(bar.count()):
        assert bar.tabRect(index).width() >= 1
        assert not bar.tabRect(index).isEmpty()


def test_a_tab_that_exactly_fills_the_row_stays_on_it(qtbot) -> None:
    """残りの幅に**ちょうど**収まるタブは、その段に残る（次の段へ送らない）。

    折り返しの判定は ``x + width > available``（狭義）。ここを ``>=`` にすると、
    ぴったり収まるタブが次の段へ送られ、**その段の右端に 1 タブぶんの空白**が
    できる。境目の 1 px の話なので普段は目に見えないが、固定幅を指定して
    使っていると（幅がきれいな値なので）ちょうど当たる。

    幅はフォントに依らないよう固定幅で作る。
    ``x = k * (幅 + TAB_SPACING)`` なので、3 枚目の右端が
    ``2 * (50 + 2) + 50 = 154`` px でバーの幅と一致する。
    """
    widget = MultiRowTabBar()
    qtbot.addWidget(widget)
    widget.show()
    for name in ("a.txt", "b.txt", "c.txt"):
        widget.addTab(name)
    widget.set_fixed_tab_width(50)
    widget.resize(2 * (50 + TAB_SPACING) + 50, widget.height())
    QApplication.processEvents()

    assert widget.tabRect(2).right() + 1 == widget.width()
    assert [widget.tabRow(i) for i in range(3)] == [0, 0, 0]
    assert widget.rowCount() == 1


def test_fixed_width_of_zero_does_not_pile_tabs_on_one_spot(bar: MultiRowTabBar) -> None:
    """幅を潰しても、タブ同士は重ならない（``tabAt()`` が別々に当たる）。"""
    bar.addTab("a.txt")
    bar.addTab("b.txt")

    bar.set_fixed_tab_width(0)

    first, second = bar.tabRect(0), bar.tabRect(1)
    assert not first.intersects(second)
    assert bar.tabAt(first.center()) == 0
    assert bar.tabAt(second.center()) == 1
