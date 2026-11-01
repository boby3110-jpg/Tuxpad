"""設定ファイル（``~/.config/Tuxpad/Tuxpad.conf``）に書き出す文字列の約束。

**なぜ必要か**（2026-10-20（80 回目）で見つけた穴）。

``settings.py`` の 16 個のキー文字列（``"editor/wrap_mode"`` など）と、
設定ファイルに書き出す値の文字列（``"window"``・``"dark"``・``"last"``）は、
**保存する側と読み込む側が同じ定数を見ている**ため、定数を書き換えても
1 つのプロセスの中では辻褄が合ってしまう。実際に 79 回目までのテストでは、
``_WRAP_MODE_KEY`` を ``"editor/wrap-mode"`` に変えても **2040 件すべて緑**
だった。それでいて実機では、

* 前回までに保存した折り返しモード・テーマ・フォント等が**読めなくなり**、
* 「設定したのに次の起動で元へ戻っている」という形でだけ現れる

（設定ファイルは利用者のディスクに残り続ける＝**版をまたいだ約束**なので、
同じプロセス内の辻褄だけでは足りない）。

そこでここでは **ファイルに書かれた本文そのもの**（``[節]`` と ``名前=値``）を
固定する。保存する関数を通し、INI を ``configparser``（Qt を通さない別の
読み手）で読み直して、節・名前・値の文字列を 1 件ずつ突き合わせる。

**値は必ず既定値と違うものを使うこと。** 既定値のままだと、キーを改名して
読めなくなっても「既定値が返る」だけで、素通りしてしまう。
"""

from __future__ import annotations

import configparser
import inspect
from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtGui import QColor

from editor_app import settings as settings_mod
from editor_app.editor import WRAP_FIXED, WRAP_NONE, WRAP_WINDOW
from editor_app.settings import (
    IPC_TARGET_ACTIVE,
    IPC_TARGET_FIRST,
    IPC_TARGET_LAST,
)
from editor_app.theme import THEME_DARK, THEME_LIGHT


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch) -> Path:
    """本物の保存経路を、こちらで中身を読める ini へ向ける。

    ``conftest.py`` の autouse の ``_isolated_settings`` と同じ差し替えだが、
    こちらは**パスを返す**（後から適用されるので、こちらが後勝ちになる）。
    """
    ini_path = tmp_path / "contract-settings.ini"
    monkeypatch.setattr(
        "editor_app.settings.QSettings",
        lambda *args, **kwargs: QSettings(str(ini_path), QSettings.Format.IniFormat),
    )
    return ini_path


def _read_ini(ini_path: Path) -> configparser.ConfigParser:
    """書かれた INI を Qt を通さずに読む（名前と値を文字列のまま見る）。"""
    # Qt は値をそのまま書くので、既定の補間（``%`` の解釈）は切っておく。
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(ini_path.read_text(encoding="utf-8"))
    return parser


#: 「この保存関数を、この引数で呼ぶと、設定ファイルのこの節のこの名前に
#: この文字列が書かれる」という一覧。**値はすべて既定値と違うもの**。
#:
#: 書き方: (保存関数名, 引数, [(節, 名前, 書かれる値), ...], 読み込み関数名,
#:          読み戻したときに期待する値)
CONTRACT = [
    (
        "save_wrap_settings",
        (WRAP_WINDOW, 123),
        [("editor", "wrap_mode", "window"), ("editor", "wrap_column", "123")],
        "load_wrap_settings",
        (WRAP_WINDOW, 123),
    ),
    (
        "save_font_settings",
        ("Tuxpad Contract Mono", 13),
        [("editor", "font_family", "Tuxpad Contract Mono"), ("editor", "font_size", "13")],
        "load_font_settings",
        ("Tuxpad Contract Mono", 13),
    ),
    (
        "save_show_line_breaks",
        (True,),
        [("editor", "show_line_breaks", "true")],
        "load_show_line_breaks",
        True,
    ),
    (
        "save_menu_bar_visible",
        (False,),
        [("appearance", "menu_bar_visible", "false")],
        "load_menu_bar_visible",
        False,
    ),
    (
        "save_check_updates_on_startup",
        (False,),
        [("update", "check_on_startup", "false")],
        "load_check_updates_on_startup",
        False,
    ),
    (
        "save_search_input_lines",
        (9,),
        [("search", "input_visible_lines", "9")],
        "load_search_input_lines",
        9,
    ),
    (
        "save_wheel_scroll_lines",
        (7,),
        [("editor", "wheel_scroll_lines", "7")],
        "load_wheel_scroll_lines",
        7,
    ),
    (
        "save_beep_on_no_match",
        (False,),
        [("search", "beep_on_no_match", "false")],
        "load_beep_on_no_match",
        False,
    ),
    (
        "save_theme",
        (THEME_DARK,),
        [("appearance", "theme", "dark")],
        "load_theme",
        THEME_DARK,
    ),
    (
        "save_ipc_target_mode",
        (IPC_TARGET_LAST,),
        [("ipc", "open_target_mode", "last")],
        "load_ipc_target_mode",
        IPC_TARGET_LAST,
    ),
    (
        "save_editor_colors",
        (QColor("#112233"), QColor("#445566")),
        [
            ("editor", "custom_background", "#112233"),
            ("editor", "custom_foreground", "#445566"),
        ],
        None,  # 読み戻しは下の専用のテストで見る（QColor の比較になるため）
        None,
    ),
    (
        "save_tab_colors",
        (QColor("#778899"), QColor("#aabbcc")),
        [
            ("appearance", "tab_active_color", "#778899"),
            ("appearance", "tab_inactive_color", "#aabbcc"),
        ],
        None,
        None,
    ),
    (
        "save_window_geometry",
        (QByteArray(b"contract-geometry"),),
        [("window", "geometry", "@ByteArray(contract-geometry)")],
        None,
        None,
    ),
]

#: 設定ファイルに現れてよい (節, 名前) の全体。これ以外が増えていたら、
#: 新しい設定を足したときにこの一覧を更新し忘れている。
EXPECTED_ENTRIES = {
    (section, option) for _f, _a, rows, _l, _e in CONTRACT for section, option, _v in rows
}


def _ids(rows):
    return [row[0] for row in rows]


# --- 1 件ずつ：書かれる節・名前・値 -----------------------------------


@pytest.mark.parametrize("saver, args, rows, loader, expected", CONTRACT, ids=_ids(CONTRACT))
def test_setting_is_written_under_its_documented_key(
    settings_file: Path, saver: str, args, rows, loader, expected
) -> None:
    """保存すると、約束どおりの節・名前・値が設定ファイルに書かれること。"""
    getattr(settings_mod, saver)(*args)

    parser = _read_ini(settings_file)
    for section, option, value in rows:
        assert parser.has_section(section), (
            f"{saver} が書いた設定ファイルに [{section}] が無い（節名を変えた？）"
        )
        assert parser.has_option(section, option), (
            f"[{section}] に {option} が無い（キーを改名すると、"
            f"前回までに保存した設定が読めなくなる）"
        )
        assert parser.get(section, option) == value


#: 上の一覧のうち、読み戻しを単純な ``==`` で見られるもの。配色と
#: ウィンドウ位置は QColor / QByteArray になるので、下の専用のテストで見る。
#: （``pytest.skip`` を使わないのは、**skip は「見ていない」ことが
#: 一覧から読み取りにくい**ため。）
LOADABLE_CONTRACT = [row for row in CONTRACT if row[3] is not None]


@pytest.mark.parametrize(
    "saver, args, rows, loader, expected", LOADABLE_CONTRACT, ids=_ids(LOADABLE_CONTRACT)
)
def test_saved_setting_is_read_back_from_the_file(
    settings_file: Path, saver: str, args, rows, loader, expected
) -> None:
    """保存した値が、**ファイルを読み直して**同じ値で返ること。

    読み込み側が別のキーを見ていると（＝書く側と読む側で改名が片方だけ）、
    既定値が返ってここで赤くなる。
    """
    getattr(settings_mod, saver)(*args)

    assert getattr(settings_mod, loader)() == expected


def test_editor_and_tab_colors_are_read_back_from_the_file(settings_file: Path) -> None:
    """配色は QColor で返るので、名前（#rrggbb）で突き合わせる。"""
    settings_mod.save_editor_colors(QColor("#112233"), QColor("#445566"))
    settings_mod.save_tab_colors(QColor("#778899"), QColor("#aabbcc"))

    background, foreground = settings_mod.load_editor_colors()
    active, inactive = settings_mod.load_tab_colors()

    assert (background.name(), foreground.name()) == ("#112233", "#445566")
    assert (active.name(), inactive.name()) == ("#778899", "#aabbcc")


def test_window_geometry_is_read_back_from_the_file(settings_file: Path) -> None:
    settings_mod.save_window_geometry(QByteArray(b"contract-geometry"))

    assert settings_mod.load_window_geometry() == QByteArray(b"contract-geometry")


# --- 全体：余計なキーが増えていないこと -------------------------------


def test_settings_file_contains_exactly_the_documented_keys(settings_file: Path) -> None:
    """全部保存したとき、設定ファイルの中身がこの一覧と**一致**すること。

    増えていたら、新しい設定を足したときにこのテストの一覧を更新し忘れている
    （＝その設定のキーは誰にも縛られていない）。
    """
    for saver, args, _rows, _loader, _expected in CONTRACT:
        getattr(settings_mod, saver)(*args)

    parser = _read_ini(settings_file)
    written = {
        (section, option) for section in parser.sections() for option in parser.options(section)
    }

    assert written == EXPECTED_ENTRIES


def test_contract_covers_every_save_function_in_settings_module() -> None:
    """``settings.py`` の ``save_*`` が全部この一覧に入っていること。

    実物（モジュールの中身）から導いているので、**新しい設定を足したときに
    この一覧が古びたことに気づける**（固定の写しだけだと気づけない）。
    """
    savers_in_module = {
        name
        for name, value in vars(settings_mod).items()
        if name.startswith("save_") and inspect.isfunction(value)
    }
    savers_in_contract = {row[0] for row in CONTRACT}

    assert savers_in_module, "settings.py から save_* が 1 つも見つからない（探し方が古い）"
    assert savers_in_module == savers_in_contract


def test_contract_is_not_empty() -> None:
    """一覧が空（または短く）なると、上の parametrize が黙って skip される。

    pytest は**空の parametrize を既定で skip** するだけなので、
    ``filterwarnings = error`` にも引っかからない（73 回目に実際にあった穴）。
    """
    assert len(CONTRACT) == 13
    assert len(EXPECTED_ENTRIES) == 17
    assert len(LOADABLE_CONTRACT) == 10


# --- 設定ファイルに書き出す「値」の文字列 -----------------------------
#
# 折り返しモード・テーマ・転送先モードは、**値そのものが設定ファイルに
# 残る文字列**なので、定数の中身を変えると前回保存した値が「知らない値」に
# なって既定値へ落ちる（＝設定が黙って元へ戻る）。


@pytest.mark.parametrize(
    "mode, written",
    [(WRAP_NONE, "none"), (WRAP_WINDOW, "window"), (WRAP_FIXED, "fixed")],
    ids=["none", "window", "fixed"],
)
def test_wrap_mode_is_written_verbatim(settings_file: Path, mode: str, written: str) -> None:
    settings_mod.save_wrap_settings(mode, 80)

    assert _read_ini(settings_file).get("editor", "wrap_mode") == written
    assert settings_mod.load_wrap_settings()[0] == mode


@pytest.mark.parametrize(
    "theme, written", [(THEME_LIGHT, "light"), (THEME_DARK, "dark")], ids=["light", "dark"]
)
def test_theme_is_written_verbatim(settings_file: Path, theme: str, written: str) -> None:
    settings_mod.save_theme(theme)

    assert _read_ini(settings_file).get("appearance", "theme") == written
    assert settings_mod.load_theme() == theme


@pytest.mark.parametrize(
    "mode, written",
    [(IPC_TARGET_FIRST, "first"), (IPC_TARGET_LAST, "last"), (IPC_TARGET_ACTIVE, "active")],
    ids=["first", "last", "active"],
)
def test_ipc_target_mode_is_written_verbatim(
    settings_file: Path, mode: str, written: str
) -> None:
    settings_mod.save_ipc_target_mode(mode)

    assert _read_ini(settings_file).get("ipc", "open_target_mode") == written
    assert settings_mod.load_ipc_target_mode() == mode


# --- 旧名称からの引き継ぎ ---------------------------------------------


def test_legacy_app_name_is_the_name_actually_used_before_the_rename() -> None:
    """旧名称は ``~/.config/Tabula/Tabula.conf`` という**実在のパス**の一部。

    ここを打ち間違えると、改名前から使っている利用者の設定（配色・フォント）が
    引き継がれず、黙って既定値で立ち上がる。``test_settings_migration.py`` は
    この定数を経由して ini を振り分けているので、**定数の中身は見ていない**。
    """
    assert settings_mod._LEGACY_APP_NAME == "Tabula"
