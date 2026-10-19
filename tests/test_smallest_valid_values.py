"""「ちょうど下限の値」が黙って既定値に化けないことのテスト.

設定から読んだ数値には、どれも「正気でない値なら既定値に戻す」見張りが
入っている（``if column < 1:`` など）。壊れた設定ファイルでアプリが
起動しなくなる・本文や検索欄が表示できなくなるのを避けるためで、
**0 や負の数・読めない文字列が既定値へ倒れること**は
``test_settings_fallback.py`` で固定済み。

**見ていなかったのは、その見張りの「境目」の側**——つまり
**利用者がわざわざ指定した、通っていいはずのいちばん小さい値 (1)**。
``< 1`` を ``<= 1`` と書き間違えても、**既にあるテストは 1 件も赤く
ならない**（0 も -3 も既定値に戻るのは変わらないため）。そうなると:

* 1 文字で折り返す指定が、黙って既定の桁数に戻る
* 検索欄を 1 行に縮める指定が、黙って既定の行数に戻る
* フォントサイズ 1pt の指定が、黙って既定のサイズに戻る

どれも「設定したはずなのに効かない」という形でしか現れず、設定ファイルを
見ても値は入っているので、利用者からは原因が分からない。1pt や 1 文字
折り返しを実際に使うかどうかではなく、**利用者が指定した値をアプリが
勝手に書き換えないこと**を縛るためのテストで、この種の書き間違いは
**下限そのものを踏まないと見つからない**。

2026-10-11（73 回目）に変異テストで見つけた穴。下記 5 か所はどれも、
``< 1`` を ``<= 1`` に変えても、``1`` を ``2`` に変えても、
**1887 件のテストが全部通ったまま**だった。

設定ファイルの中身を直接書いてから読ませているのは
``test_settings_fallback.py`` と同じ理由（``setValue`` で Python の値を
入れると、INI を経由したときの型が再現できない）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFontDialog

from editor_app import dialogs
from editor_app.editor import (
    DEFAULT_FONT_SIZE,
    DEFAULT_WRAP_COLUMN,
)
from editor_app.main_window import MainWindow
from editor_app.settings import (
    DEFAULT_SEARCH_INPUT_LINES,
    load_font_settings,
    load_search_input_lines,
    load_wrap_settings,
)


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch):
    """設定ファイルの中身を直接書いてから読ませるためのフィクスチャ。"""

    def write(body: str) -> None:
        ini_path = tmp_path / "settings.ini"
        ini_path.write_text(body, encoding="utf-8")
        monkeypatch.setattr(
            "editor_app.settings.QSettings",
            lambda *args, **kwargs: QSettings(str(ini_path), QSettings.Format.IniFormat),
        )

    return write


# --- 設定ファイルから読む値 ------------------------------------------
#
# どのテストも「既定値と違う値であること」を先に確かめる。既定値が将来 1 に
# なると、assert が通ってもそれは「指定が通った」証拠にならない（既定値へ
# 倒れていても同じ値になる）ため、その場で気づけるようにしておく。


def test_wrap_column_of_one_is_kept(settings_file) -> None:
    """1 文字で折り返す指定が、既定の桁数に戻されないこと。"""
    assert DEFAULT_WRAP_COLUMN != 1
    settings_file("[editor]\nwrap_column=1\n")
    assert load_wrap_settings()[1] == 1


def test_search_input_lines_of_one_is_kept(settings_file) -> None:
    """検索欄・置換欄を 1 行に縮める指定が、既定の行数に戻されないこと。"""
    assert DEFAULT_SEARCH_INPUT_LINES != 1
    settings_file("[search]\ninput_visible_lines=1\n")
    assert load_search_input_lines() == 1


def test_font_size_of_one_is_kept(settings_file) -> None:
    """1pt の指定が、既定のサイズに戻されないこと。"""
    assert DEFAULT_FONT_SIZE != 1
    settings_file("[editor]\nfont_size=1\n")
    assert load_font_settings()[1] == 1


# --- 読んだ値を実際に本文へ適用するところ ----------------------------


def test_editor_keeps_a_font_size_of_one(window: MainWindow) -> None:
    """``set_font_settings()`` 側にも同じ下限の見張りがあるので、両方縛る。

    設定ファイルから 1pt が読めても、適用する側が 1pt を弾けば
    結局は既定のサイズになる（利用者から見れば同じ「効かない」）。
    """
    assert DEFAULT_FONT_SIZE != 1
    editor = window.current_editor()

    editor.set_font_settings("Monospace", 1)

    assert editor.font_size() == 1
    # 画面に効いているかどうかまで見る（覚えているだけでは意味がない）。
    assert editor.font().pointSize() == 1


def test_prompt_font_keeps_a_one_point_choice(window: MainWindow, monkeypatch) -> None:
    """フォントダイアログが 1pt を返したら、今までのサイズで上書きしないこと。

    ``prompt_font()`` が今までのサイズを使うのは、**ピクセル指定の
    フォントで ``pointSize()`` が -1 になったときだけ**（``test_dialogs.py``）。
    1pt は ``pointSize()`` がちゃんと答えている＝利用者の選択なので、
    そのまま返さなければならない。
    """
    assert DEFAULT_FONT_SIZE != 1
    monkeypatch.setattr(
        QFontDialog, "getFont", staticmethod(lambda *a, **k: (True, QFont("Courier", 1)))
    )

    assert dialogs.prompt_font(window, "Monospace", DEFAULT_FONT_SIZE) == ("Courier", 1)
