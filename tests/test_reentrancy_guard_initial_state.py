"""再入防止の旗（ガード）が「下りた状態」で始まることのテスト（74 回目）。

``changeEvent`` の中から ``setPalette()`` を呼ぶ、確認中にもう一度押される、
といった**自分で自分を呼び直してしまう**経路を捌くために、いくつかの
クラスは ``self._なにか = False`` という旗を ``__init__`` で用意している。

**旗の初期値が逆（``True``）だと、その旗で守っている処理が永久に
素通りする。** しかも 1893 件のテストが全部緑のまま通る穴が
``search_panel.py`` に 1 つ残っていた（2026-10-11（74 回目）に
変異で確かめた）。理由は、**旗を踏むテストがどれも「自分で立ててから
下ろしていた」**ため。たとえば ``tests/test_editor_colors.py`` の
``test_changeevent_reapplies_colors`` は、配色が「取り消されたまま」の
状態を作るためにわざと旗を立て、``finally`` で ``False`` に戻す。
そのやり方自体は正しいが、**``__init__`` が入れた値は誰も踏まない**。

ここでは 4 つの旗それぞれについて、**作ったばかりのものが
「守られている側の処理」をちゃんと実行するか**を見る（旗には触らない）。
``editor.py`` ・ ``update_check.py`` ・ ``file_watch.py`` の 3 つは、
今のところ別のテストが**たまたま**赤くなって捕まえている
（``test_theme.py`` のテーマ切替、``test_update_check.py`` の手動確認、
``test_file_reload.py`` の読めないファイル）。そちらが書き換わった拍子に
見張りが黙って消えないよう、ここで**直接**押さえておく。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette

from editor_app.main_window import MainWindow
from editor_app.search_panel import (
    _INPUT_BASE_COLOR,
    _INPUT_HIGHLIGHT_COLOR,
    _INPUT_HIGHLIGHTED_TEXT_COLOR,
    _INPUT_TEXT_COLOR,
)


def _clobber_palette(widget, base: str, text: str) -> None:
    """外から配色を塗り替える（テーマの repolish で起こることの再現）。

    ``setPalette()`` そのものが ``changeEvent`` を起こすので、**旗が
    下りていれば、この呼び出しの中で貼り直しまで終わる**。旗が初めから
    立っていると、塗り替えたままになる。
    """
    clobbered = widget.palette()
    clobbered.setColor(QPalette.ColorRole.Base, QColor(base))
    clobbered.setColor(QPalette.ColorRole.Text, QColor(text))
    widget.setPalette(clobbered)


def _assert_fixed_search_colors(widget) -> None:
    palette = widget.palette()
    assert palette.color(QPalette.ColorRole.Base) == _INPUT_BASE_COLOR
    assert palette.color(QPalette.ColorRole.Text) == _INPUT_TEXT_COLOR
    assert palette.color(QPalette.ColorRole.Highlight) == _INPUT_HIGHLIGHT_COLOR
    assert (
        palette.color(QPalette.ColorRole.HighlightedText)
        == _INPUT_HIGHLIGHTED_TEXT_COLOR
    )
    # 実際に描いているのは viewport() 側なので、そちらも戻っていること。
    assert widget.viewport().palette().color(QPalette.ColorRole.Base) == _INPUT_BASE_COLOR


# ----------------------------------------------------------------------
# search_panel.py: _reapplying_fixed_palette
# （74 回目に見つかった本物の穴。ここだけは他のどのテストも捕まえない）
# ----------------------------------------------------------------------
def test_fresh_search_field_repairs_a_clobbered_palette(window: MainWindow) -> None:
    """作ったばかりの検索欄は、塗り替えられたら白背景・黒文字へ戻すこと。"""
    field = window.search_panel._input
    _assert_fixed_search_colors(field)  # まず素の状態を確かめる

    _clobber_palette(field, "red", "green")

    _assert_fixed_search_colors(field)


def test_fresh_replace_field_repairs_a_clobbered_palette(window: MainWindow) -> None:
    """置換欄も同じ（同じ ``_QueryTextEdit`` だが、別に作られる）。"""
    field = window.search_panel._replace_input
    _assert_fixed_search_colors(field)

    _clobber_palette(field, "red", "green")

    _assert_fixed_search_colors(field)


# ----------------------------------------------------------------------
# editor.py: _reapplying_editor_colors
# ----------------------------------------------------------------------
def test_fresh_editor_repairs_a_clobbered_custom_palette(window: MainWindow) -> None:
    """作ったばかりの本文は、カスタム配色を塗り替えられたら貼り直すこと。"""
    editor = window.current_editor()
    editor.set_editor_colors(QColor("white"), QColor("black"))

    _clobber_palette(editor, "red", "green")

    assert editor.palette().color(QPalette.ColorRole.Base) == QColor("white")
    assert editor.palette().color(QPalette.ColorRole.Text) == QColor("black")


# ----------------------------------------------------------------------
# update_check.py: _update_check_running
# ----------------------------------------------------------------------
def test_fresh_window_can_start_a_manual_update_check(
    window: MainWindow, monkeypatch
) -> None:
    """作ったばかりのウィンドウで「更新を確認」が実際に走ること。

    旗が初めから立っていると、**一度も確認していないのに**
    「更新を確認しています。」と返して何もしない。
    """
    started: list[bool] = []
    shown: list[tuple] = []
    monkeypatch.setattr(
        window, "_start_update_check", lambda **kwargs: started.append(kwargs["manual"])
    )
    # 本物のモーダルを開かせない（答える人がいないヘッドレスでは固まる）。
    monkeypatch.setattr(
        window, "_show_update_message", lambda *args, **kwargs: shown.append(args)
    )
    window._update_repo_root = Path("/どこか/tuxpad")

    window.check_for_updates_manually()

    assert started == [True]
    assert shown == []  # 「確認しています」で終わらせないこと


# ----------------------------------------------------------------------
# file_watch.py: _long_operation_active
# ----------------------------------------------------------------------
def test_fresh_window_processes_pending_reloads_right_away(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    """作ったばかりのウィンドウは、溜まった再読み込みを後回しにしないこと。

    旗が初めから立っていると、外部で書き換わったことに**永久に
    気づけない**（確認が出ないまま、こちらの本文で上書きしてしまう）。
    """
    path = tmp_path / "そと.txt"
    path.write_text("最初の内容\n", encoding="utf-8")
    window.open_path(path)

    seen: list[Path] = []
    # 本物の確認ダイアログを開かせない（上と同じ理由）。
    monkeypatch.setattr(
        window, "_maybe_reload_changed_file", lambda p: seen.append(p)
    )
    window._pending_reload_paths.add(str(path.resolve()))

    window._process_pending_reloads()

    assert seen == [path.resolve()]
    assert window._pending_reload_paths == set()  # 捌いたぶんは消えること
