"""検索パネルを「組み立てるときに 1 回だけ決める印」のテスト。

機能 6・7（Ctrl+F / Ctrl+H）の土台（`search_replace._setup_search_panel`）は
`MainWindow.__init__` から **1 度だけ**呼ばれる。そこで決まるのは

* 検索パネルがどういう窓か（本体に付いて回る道具窓か、別のウィンドウか）
* 本文の編集に追随して検索し直すときの「少し待つ」タイマーの設定

——どちらも**後から誰も触らない**ので、外れたままでも他のテストは緑のまま
通る。実際 2026-09-20（63 回目）の変異検査（`tools/mutation_check.py` の
``sr-panel-not-tool-window`` / ``sr-refresh-interval-zero`` /
``sr-refresh-timer-repeating``）では、全 1774 件が緑のままだった。

**シグナルの配線そのもの**（「検索」ボタン・結果のクリック・置換ボタン・
パネルを閉じたとき・編集やタブ並べ替えへの追随）は、既存のテストが
既に縛れていることを同じ検査で確かめてある（PROGRESS.md の 63 回目の節）
ので、ここでは扱わない。
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from editor_app.main_window import MainWindow
from editor_app.search_replace import SEARCH_REFRESH_DELAY_MS


def test_search_panel_is_a_tool_window(window: MainWindow) -> None:
    """検索パネルは道具窓（``Qt.WindowType.Tool``）であること。

    普通のウィンドウにすると、**本体をクリックした拍子に後ろへ回り込んで
    隠れる**うえ、タスクバーやウィンドウ一覧に「もう 1 つの Tuxpad」として
    並ぶ。機能 6 は「結果の一覧を押して本文へジャンプする」＝本文と
    パネルを往復する使い方なので、隠れることがそのまま手戻りになる。
    """
    flags = window.search_panel.windowFlags()
    assert flags & Qt.WindowType.WindowType_Mask == Qt.WindowType.Tool


def test_refresh_timer_waits_before_searching_again(window: MainWindow) -> None:
    """検索し直しのタイマーは「少し待ってからまとめて 1 回」であること。

    待ち時間が 0 になると、**1 文字打つたびに全タブを検索し直す**
    （インクリメンタルサーチをやめた 2026-08-05 の実機フィードバックが
    そのまま戻ってくる）。`_on_editor_text_changed` はタイマーを
    ``start()`` し直すだけなので、この待ち時間だけが歯止めになっている。
    """
    assert SEARCH_REFRESH_DELAY_MS > 0
    assert window._search_refresh_timer.interval() == SEARCH_REFRESH_DELAY_MS


def test_refresh_timer_stops_after_firing_once(window: MainWindow, qtbot) -> None:
    """検索し直しのタイマーは 1 回きり (``setSingleShot``) であること。

    繰り返しになると、一度本文を編集したあと**パネルを閉じるまでずっと
    0.2 秒ごとに全タブを検索し続ける**。出てくる結果は同じなので画面では
    気づけないが、大きなファイルを開いたままのノート PC では電池を削る
    だけで誰の得にもならない。
    """
    assert window._search_refresh_timer.isSingleShot() is True

    # 実際に 1 回発火させて、止まっていることまで見る（本来の 0.2 秒を
    # 待たずに済むよう、待ち時間は 0 にしておく）。
    window._search_refresh_timer.setInterval(0)
    window._search_refresh_timer.start()
    qtbot.waitUntil(
        lambda: window._search_refresh_timer.isActive() is False, timeout=1000
    )
