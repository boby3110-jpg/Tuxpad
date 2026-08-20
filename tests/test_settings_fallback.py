"""壊れた設定ファイルを読んだときのフォールバックのテスト.

設定の読み込み（``load_*``）は**全て起動時に通る**——``app.main`` の
``apply_theme(app, load_theme())``、``MainWindow.__init__`` の
``apply_theme_to_window``、``ViewSettings`` の ``load_wrap_settings`` など。
つまりここで例外が漏れると **ウィンドウが 1 枚も出ないまま終わる**（しかも
利用者から見ると「昨日まで動いていたのに今日は起動しない」になる）ので、
どの ``load_*`` も「読めない値なら既定値」で必ず返ること、を固定しておく。

壊れ方は 2 通りを想定している:

* 値が読めない（``wrap_column=abc`` のような手編集、途中で切れた保存、
  別の版が書いた値の残り）
* **値にカンマが入っている**——このとき ``QSettings.value()`` は ``str``
  ではなく **``list``** を返す。集合との ``in`` 判定に list を渡すと
  ``TypeError: unhashable type: 'list'`` になるため、ここを素通りさせると
  起動しなくなる（2026-08-13 に実際に見つけた不具合。``type=str`` で修正）。

設定ファイルそのものを書いてから読ませているのは、``setValue`` で
Python の値を入れると **INI を経由したときの型（全部 str か list）が
再現できない**ため。実機で壊れるのはファイルの側なので、ファイルで縛る。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor

from editor_app.editor import (
    DEFAULT_FONT_FAMILY,
    DEFAULT_FONT_SIZE,
    DEFAULT_WRAP_COLUMN,
    DEFAULT_WRAP_MODE,
)
from editor_app.settings import (
    DEFAULT_IPC_TARGET_MODE,
    DEFAULT_SEARCH_INPUT_LINES,
    load_check_updates_on_startup,
    load_editor_colors,
    load_font_settings,
    load_ipc_target_mode,
    load_menu_bar_visible,
    load_search_input_lines,
    load_show_line_breaks,
    load_tab_colors,
    load_theme,
    load_wrap_settings,
)
from editor_app.theme import DEFAULT_THEME


@pytest.fixture
def broken_settings(tmp_path: Path, monkeypatch):
    """設定ファイルの中身を直接書いてから読ませるためのフィクスチャ。

    ``conftest.py`` の ``_isolated_settings`` と同じく
    ``editor_app.settings.QSettings`` を差し替えるが、こちらは
    **こちらで用意した INI の本文**を読ませる（autouse の方は後から
    適用されるので、この fixture の差し替えが後勝ちになる）。
    """

    def write(body: str) -> None:
        ini_path = tmp_path / "broken-settings.ini"
        ini_path.write_text(body, encoding="utf-8")
        monkeypatch.setattr(
            "editor_app.settings.QSettings",
            lambda *args, **kwargs: QSettings(str(ini_path), QSettings.Format.IniFormat),
        )

    return write


# --- 値が読めないとき -------------------------------------------------


def test_unknown_wrap_mode_falls_back_to_default(broken_settings) -> None:
    """知らない折り返しモード（別の版が書いた値の残り等）は既定値へ。"""
    broken_settings("[editor]\nwrap_mode=banana\n")
    assert load_wrap_settings()[0] == DEFAULT_WRAP_MODE


def test_non_numeric_wrap_column_falls_back_to_default(broken_settings) -> None:
    broken_settings("[editor]\nwrap_column=abc\n")
    assert load_wrap_settings()[1] == DEFAULT_WRAP_COLUMN


def test_empty_wrap_column_falls_back_to_default(broken_settings) -> None:
    """値が空（``wrap_column=``）——保存が途中で切れると実際にこうなる。"""
    broken_settings("[editor]\nwrap_column=\n")
    assert load_wrap_settings()[1] == DEFAULT_WRAP_COLUMN


@pytest.mark.parametrize("column", ["0", "-40"])
def test_non_positive_wrap_column_falls_back_to_default(broken_settings, column: str) -> None:
    """0 文字・負の文字数で折り返そうとすると本文が表示できなくなる。"""
    broken_settings(f"[editor]\nwrap_column={column}\n")
    assert load_wrap_settings()[1] == DEFAULT_WRAP_COLUMN


def test_non_numeric_search_input_lines_falls_back_to_default(broken_settings) -> None:
    broken_settings("[search]\ninput_visible_lines=abc\n")
    assert load_search_input_lines() == DEFAULT_SEARCH_INPUT_LINES


@pytest.mark.parametrize("lines", ["0", "-3"])
def test_non_positive_search_input_lines_falls_back_to_default(
    broken_settings, lines: str
) -> None:
    """0 行だと検索欄が潰れて、Ctrl+F の入力欄に何も打てなくなる。"""
    broken_settings(f"[search]\ninput_visible_lines={lines}\n")
    assert load_search_input_lines() == DEFAULT_SEARCH_INPUT_LINES


def test_empty_font_family_falls_back_to_default(broken_settings) -> None:
    """フォント名が空のまま使うと、本文が Qt 任せの見た目に化ける。"""
    broken_settings("[editor]\nfont_family=\n")
    assert load_font_settings()[0] == DEFAULT_FONT_FAMILY


def test_non_numeric_font_size_falls_back_to_default(broken_settings) -> None:
    broken_settings("[editor]\nfont_size=abc\n")
    assert load_font_settings()[1] == DEFAULT_FONT_SIZE


@pytest.mark.parametrize("size", ["0", "-12"])
def test_non_positive_font_size_falls_back_to_default(broken_settings, size: str) -> None:
    """0pt・負の pt を Qt に渡すと本文が読めない大きさになる。"""
    broken_settings(f"[editor]\nfont_size={size}\n")
    assert load_font_settings()[1] == DEFAULT_FONT_SIZE


def test_invalid_editor_colors_fall_back_to_theme(broken_settings) -> None:
    """色名として解釈できない値は「テーマに合わせる」(None) 扱いにする。

    ここを素通りさせると**無効な QColor**（黒扱い）がそのまま本文に
    適用され、ダークテーマで黒地に黒文字＝何も見えない、になり得る。
    """
    broken_settings(
        "[editor]\ncustom_background=zzz\ncustom_foreground=#nothex\n",
    )
    assert load_editor_colors() == (None, None)


def test_valid_editor_colors_still_load(broken_settings) -> None:
    """上の裏返し。正しい色まで捨てていないこと。"""
    broken_settings(
        "[editor]\ncustom_background=#ffffff\ncustom_foreground=#000000\n",
    )
    assert load_editor_colors() == (QColor("#ffffff"), QColor("#000000"))


def test_invalid_tab_colors_fall_back_to_none(broken_settings) -> None:
    """タブの色も、色名として解釈できない値は未設定 (None) 扱いにする。

    エディタ本文の配色（1 つ上）と同じ守りがタブの側にも要る。素通り
    させると**無効な QColor**（Qt では黒扱い）がそのままタブに塗られ、
    ダークテーマだと「タブが真っ黒で、どれが開いているか分からない」に
    なり得る。実際 ``_color_or_none`` の ``isValid()`` を外しても
    2026-08-18（37 回目）まで全テストが緑のままだった。
    """
    broken_settings("[appearance]\ntab_active_color=zzz\ntab_inactive_color=#nothex\n")
    assert load_tab_colors() == (None, None)


def test_valid_tab_colors_still_load(broken_settings) -> None:
    """上の裏返し。正しい色まで捨てていないこと。"""
    broken_settings("[appearance]\ntab_active_color=#ffffff\ntab_inactive_color=#000000\n")
    assert load_tab_colors() == (QColor("#ffffff"), QColor("#000000"))


# --- 「切ったはずの設定」が入り直さないこと（type=bool） ----------------
#
# INI では真偽値は ``false`` という**文字列**で保存される。``type=bool``
# を付けずに読むと ``QSettings.value()`` はその文字列を返し、
# ``bool("false")`` は **True** なので「切ったはずの設定が、再起動すると
# 入り直している」形の不具合になる。``type=str`` を落としたときの
# 「起動しない」と違って**静かに元へ戻るだけ**なので、利用者は
# 「設定が保存されない」としか分からない。


@pytest.mark.parametrize(
    ("body", "loader"),
    [
        ("[editor]\nshow_line_breaks=false\n", load_show_line_breaks),
        ("[appearance]\nmenu_bar_visible=false\n", load_menu_bar_visible),
        ("[update]\ncheck_on_startup=false\n", load_check_updates_on_startup),
    ],
    ids=["show_line_breaks", "menu_bar_visible", "check_updates_on_startup"],
)
def test_false_saved_in_the_ini_file_stays_off(broken_settings, body: str, loader) -> None:
    """設定ファイルに ``false`` と書いてあれば、必ず False で返ること。"""
    broken_settings(body)
    assert loader() is False


@pytest.mark.parametrize(
    ("body", "loader"),
    [
        ("[editor]\nshow_line_breaks=true\n", load_show_line_breaks),
        ("[appearance]\nmenu_bar_visible=true\n", load_menu_bar_visible),
        ("[update]\ncheck_on_startup=true\n", load_check_updates_on_startup),
    ],
    ids=["show_line_breaks", "menu_bar_visible", "check_updates_on_startup"],
)
def test_true_saved_in_the_ini_file_stays_on(broken_settings, body: str, loader) -> None:
    """上の裏返し。``true`` を False に倒していないこと。"""
    broken_settings(body)
    assert loader() is True


# --- 値にカンマが入っているとき（list が返る） ------------------------
#
# ここは「既定値になること」よりも **例外を投げずに返ること** が本題。
# 投げるとアプリが起動しない。


def test_comma_in_wrap_mode_does_not_crash(broken_settings) -> None:
    broken_settings("[editor]\nwrap_mode=none, window\n")
    assert load_wrap_settings() == (DEFAULT_WRAP_MODE, DEFAULT_WRAP_COLUMN)


def test_comma_in_wrap_column_does_not_crash(broken_settings) -> None:
    broken_settings("[editor]\nwrap_column=80, 100\n")
    assert load_wrap_settings()[1] == DEFAULT_WRAP_COLUMN


def test_comma_in_theme_does_not_crash(broken_settings) -> None:
    broken_settings("[appearance]\ntheme=light, dark\n")
    assert load_theme() == DEFAULT_THEME


def test_comma_in_ipc_target_mode_does_not_crash(broken_settings) -> None:
    broken_settings("[ipc]\nopen_target_mode=first, active\n")
    assert load_ipc_target_mode() == DEFAULT_IPC_TARGET_MODE


def test_comma_in_font_settings_does_not_crash(broken_settings) -> None:
    broken_settings("[editor]\nfont_family=A, B\nfont_size=10, 20\n")
    assert load_font_settings() == (DEFAULT_FONT_FAMILY, DEFAULT_FONT_SIZE)


def test_comma_in_search_input_lines_does_not_crash(broken_settings) -> None:
    broken_settings("[search]\ninput_visible_lines=6, 8\n")
    assert load_search_input_lines() == DEFAULT_SEARCH_INPUT_LINES


def test_comma_in_editor_colors_does_not_crash(broken_settings) -> None:
    broken_settings("[editor]\ncustom_background=#fff, #000\ncustom_foreground=a, b\n")
    assert load_editor_colors() == (None, None)


def test_whole_broken_settings_file_still_starts_a_window(broken_settings, make_window) -> None:
    """全部まとめて壊れていても、ウィンドウが 1 枚出るところまで行くこと。

    個々の ``load_*`` を通すだけでなく、**起動の経路**（``MainWindow``
    の生成 → テーマ適用 → 折り返し・フォントの復元）を実際に通す。
    これが赤くなるときは「利用者のアプリが起動しない」と同じ状態。
    """
    broken_settings(
        "[editor]\n"
        "wrap_mode=none, window\n"
        "wrap_column=abc\n"
        "font_family=\n"
        "font_size=0\n"
        "custom_background=zzz\n"
        "custom_foreground=zzz\n"
        "[appearance]\n"
        "theme=light, dark\n"
        "[ipc]\n"
        "open_target_mode=first, active\n"
        "[search]\n"
        "input_visible_lines=-1\n"
    )

    win = make_window()

    assert win.isVisible()
    editor = win.current_editor()
    assert editor.wrap_mode() == DEFAULT_WRAP_MODE
    assert editor.wrap_column() == DEFAULT_WRAP_COLUMN
    assert editor.font_family() == DEFAULT_FONT_FAMILY
    assert editor.font_size() == DEFAULT_FONT_SIZE
