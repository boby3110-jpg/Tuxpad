"""メニューバーとアクションの組み立て (editor_app/menus.py) のテスト.

``menus.py`` は「どの操作がどのメニューのどこにあり、どのキーで、何を
呼ぶか」を 1 か所にまとめたモジュール。ここが壊れると

- メニューに出ていない（＝利用者から操作できない）
- ショートカットが変わった / 重複して効かなくなった
- チェック付きの項目が現在の設定と食い違って表示される

といった、テストが無いと気づきにくい壊れ方をする。実機で目視するまで
分からない類の不具合なので、構成そのものを固定しておく。
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu

from editor_app.editor import WRAP_FIXED, WRAP_NONE, WRAP_WINDOW
from editor_app.settings import (
    IPC_TARGET_ACTIVE,
    IPC_TARGET_FIRST,
    IPC_TARGET_LAST,
    load_menu_bar_visible,
    save_ipc_target_mode,
    save_theme,
    save_wrap_settings,
)
from editor_app.theme import THEME_DARK, THEME_LIGHT


def plain(title: str) -> str:
    """見出しからニーモニック表記を取り除く（``"表示(&V)"`` → ``"表示"``）。"""
    return re.sub(r"\(&.\)", "", title).replace("&", "").strip()


def child_menus(parent) -> list[QMenu]:
    """``parent`` が直接持つメニュー（追加した順）。

    ``QAction.menu()`` 越しに取ったメニューは、その ``QAction`` の Python
    側のラッパーが回収された時点で無効になり、あとから触ると
    ``RuntimeError: Internal C++ object already deleted`` になる（PySide の
    所有権の癖）。テストの中でメニューを持ち回れるよう、
    ``findChildren()`` から取り直す。見出しの無いメニューは Qt が内部で
    作るもの（メニューバーに入り切らない項目を出す拡張メニュー等）なので
    除く。
    """
    found = parent.findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly)
    return [menu for menu in found if menu.title()]


def menu_titles(window) -> list[str]:
    """メニューバーに並んでいるメニューの見出し（ニーモニックを除く）。"""
    return [plain(menu.title()) for menu in child_menus(window.menuBar())]


def find_menu(window, title: str) -> QMenu:
    """見出しが ``title``（ニーモニックを除いた比較）のメニューを返す。"""
    for menu in child_menus(window.menuBar()):
        if plain(menu.title()) == title:
            return menu
    raise AssertionError(f"メニュー {title!r} が見つからない: {menu_titles(window)}")


def find_submenu(menu, title: str) -> QMenu:
    for sub in child_menus(menu):
        if plain(sub.title()) == title:
            return sub
    raise AssertionError(f"サブメニュー {title!r} が見つからない")


def actions_in(menu) -> list[QAction]:
    """``menu`` 以下（サブメニューも含む）の、区切り線でないアクション。"""
    found: list[QAction] = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        sub = action.menu()
        if sub is not None:
            found.extend(actions_in(sub))
        else:
            found.append(action)
    return found


def all_menu_actions(window) -> list[QAction]:
    result: list[QAction] = []
    for action in window.menuBar().actions():
        menu = action.menu()
        if menu is not None:
            result.extend(actions_in(menu))
    return result


# ----------------------------------------------------------------------
# メニューに出し忘れが無いこと
# ----------------------------------------------------------------------


def test_every_action_attribute_appears_in_a_menu(window):
    """``window.action_*`` は全てどこかのメニューに出ていること。

    アクションを作ったのにメニューへ追加し忘れると、ショートカットのある
    ものは辛うじて使えるが、無いものは利用者から一切辿り着けなくなる。
    """
    in_menus = set(all_menu_actions(window))
    missing = [
        name
        for name in dir(window)
        if name.startswith("action_") and getattr(window, name) not in in_menus
    ]
    assert missing == []


def test_menu_bar_titles(window):
    assert menu_titles(window) == [
        "ファイル",
        "編集",
        "表示",
        "設定",
    ]


def test_file_menu_contents(window):
    menu = find_menu(window, "ファイル")
    assert actions_in(menu) == [
        window.action_new,
        window.action_open,
        window.action_save,
        window.action_save_as,
        window.action_close_tab,
        window.action_quit,
    ]


def test_edit_menu_contents(window):
    menu = find_menu(window, "編集")
    assert actions_in(menu) == [
        window.action_find,
        window.action_replace,
        window.action_search_input_height,
        window.action_next_tab,
        window.action_previous_tab,
    ]


def test_view_menu_contents(window):
    """「表示」には折り返し・改行記号・フォントだけが残り、サブメニューは無い。

    配色・テーマ・タブ幅などの「見た目の設定」は「設定」メニューへ移した
    （実機フィードバックによる再構成）。
    """
    menu = find_menu(window, "表示")
    assert [action.menu() for action in menu.actions() if action.menu() is not None] == []
    assert actions_in(menu) == [
        window.action_wrap_none,
        window.action_wrap_window,
        window.action_wrap_fixed,
        window.action_show_line_breaks,
        window.action_font,
    ]


def test_settings_menu_submenus(window):
    menu = find_menu(window, "設定")
    submenu_titles = [
        plain(action.menu().title())
        for action in menu.actions()
        if action.menu() is not None
    ]
    assert submenu_titles == [
        "タブの幅",
        "タブの配色",
        "テーマ",
        "エディタの配色",
        "外部から開くファイルの読み込み先",
        "更新",
    ]

    assert actions_in(find_submenu(menu, "タブの幅")) == [
        window.action_tab_width_auto,
        window.action_tab_width_fixed,
    ]
    assert actions_in(find_submenu(menu, "テーマ")) == [
        window.action_theme_light,
        window.action_theme_dark,
    ]
    assert actions_in(find_submenu(menu, "外部から開くファイルの読み込み先")) == [
        window.action_open_target_first,
        window.action_open_target_last,
        window.action_open_target_active,
    ]
    assert actions_in(find_submenu(menu, "更新")) == [
        window.action_check_updates,
        window.action_check_updates_on_startup,
    ]


def test_settings_menu_has_menu_bar_toggle(window):
    """「設定」の末尾に、メニューバーの表示/非表示トグルがあること。"""
    menu = find_menu(window, "設定")
    assert window.action_toggle_menu_bar in menu.actions()
    assert window.action_toggle_menu_bar.isCheckable()
    # メニューバーを隠しても戻せるよう Ctrl+M が割り当ててあること。
    assert window.action_toggle_menu_bar.shortcut() == QKeySequence("Ctrl+M")


# ----------------------------------------------------------------------
# ショートカット
# ----------------------------------------------------------------------


def test_close_tab_shortcut_includes_ctrl_w_without_duplicates(window):
    """Ctrl+W が必ず含まれ、かつ重複していないこと。

    重複した QKeySequence を渡すと "Ambiguous shortcut overload" になり、
    押しても何も起きなくなる（実機の KDE で StandardKey.Close が
    ['Ctrl+W', 'Close'] を返したことによる不具合）。
    """
    shortcuts = window.action_close_tab.shortcuts()
    assert QKeySequence("Ctrl+W") in shortcuts
    assert len(shortcuts) == len(set(shortcuts))


def test_replace_shortcut_is_ctrl_h(window):
    """置換は Ctrl+H。StandardKey.Replace は実機 (KDE) で Ctrl+R になる。"""
    assert window.action_replace.shortcut() == QKeySequence("Ctrl+H")


def test_quit_has_no_shortcut(window):
    """終了に Ctrl+Q を割り当てない（誤操作しやすいとの実機フィードバック）。"""
    assert window.action_quit.shortcut().isEmpty()


def test_tab_switch_shortcuts(window):
    assert window.action_next_tab.shortcut() == QKeySequence("Ctrl+Tab")
    assert window.action_previous_tab.shortcut() == QKeySequence("Ctrl+Shift+Tab")


def test_menu_bar_hidden_by_toggle_and_persisted(window):
    """トグルでメニューバーを隠すと、実際に隠れ、設定にも保存されること。"""
    assert window.menuBar().isVisible()
    assert load_menu_bar_visible() is True

    window.set_menu_bar_visible(False)

    assert not window.menuBar().isVisible()
    assert window.action_toggle_menu_bar.isChecked() is False
    assert load_menu_bar_visible() is False

    window.set_menu_bar_visible(True)
    assert window.menuBar().isVisible()
    assert load_menu_bar_visible() is True


def test_menu_bar_visibility_applies_to_all_windows(window, make_window):
    """メニューバーの表示/非表示はアプリ全体の設定（全ウィンドウに反映）。"""
    other = make_window()

    window.set_menu_bar_visible(False)

    assert not window.menuBar().isVisible()
    assert not other.menuBar().isVisible()
    assert other.action_toggle_menu_bar.isChecked() is False


def test_new_window_starts_with_saved_menu_bar_visibility(window, make_window):
    """設定で隠してあると、あとから開くウィンドウも最初から隠れていること。"""
    window.set_menu_bar_visible(False)

    later = make_window()

    assert not later.menuBar().isVisible()
    assert later.action_toggle_menu_bar.isChecked() is False


def test_no_duplicate_shortcuts_across_menu_actions(window):
    """同じショートカットが 2 つのアクションに割り当てられていないこと。"""
    seen: dict[str, str] = {}
    for action in all_menu_actions(window):
        for seq in action.shortcuts():
            key = seq.toString()
            assert key not in seen, f"{key} が {seen.get(key)} と {action.text()} で重複"
            seen[key] = action.text()


# ----------------------------------------------------------------------
# チェック付きの項目
# ----------------------------------------------------------------------


def test_checkable_groups_are_exclusive(window):
    for group in (
        window._wrap_action_group,
        window._theme_action_group,
        window._tab_width_action_group,
        window._open_target_action_group,
    ):
        assert group.isExclusive()
        assert len([a for a in group.actions() if a.isChecked()]) == 1


def test_wrap_actions_reflect_saved_setting(make_window):
    save_wrap_settings(WRAP_FIXED, 80)
    window = make_window()
    assert window.action_wrap_fixed.isChecked()
    assert not window.action_wrap_none.isChecked()
    assert not window.action_wrap_window.isChecked()


def test_theme_actions_reflect_saved_setting(make_window):
    save_theme(THEME_DARK)
    window = make_window()
    assert window.action_theme_dark.isChecked()
    assert not window.action_theme_light.isChecked()

    save_theme(THEME_LIGHT)
    other = make_window()
    assert other.action_theme_light.isChecked()


def test_open_target_actions_reflect_saved_setting(make_window):
    save_ipc_target_mode(IPC_TARGET_LAST)
    window = make_window()
    assert window.action_open_target_last.isChecked()
    assert not window.action_open_target_first.isChecked()
    assert not window.action_open_target_active.isChecked()


def test_tab_width_defaults_to_auto(window):
    assert window.action_tab_width_auto.isChecked()
    assert not window.action_tab_width_fixed.isChecked()


# ----------------------------------------------------------------------
# 結線
# ----------------------------------------------------------------------


def test_wrap_actions_are_connected(window):
    window.action_wrap_window.trigger()
    assert window._wrap_mode == WRAP_WINDOW

    window.action_wrap_none.trigger()
    assert window._wrap_mode == WRAP_NONE


def test_theme_actions_are_connected(window, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(type(window), "set_theme", lambda self, theme: called.append(theme))

    window.action_theme_dark.trigger()
    window.action_theme_light.trigger()
    assert called == [THEME_DARK, THEME_LIGHT]


def test_open_target_actions_are_connected(window, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(
        type(window), "set_ipc_target_mode", lambda self, mode: called.append(mode)
    )

    window.action_open_target_first.trigger()
    window.action_open_target_last.trigger()
    window.action_open_target_active.trigger()
    assert called == [IPC_TARGET_FIRST, IPC_TARGET_LAST, IPC_TARGET_ACTIVE]


def test_file_actions_are_connected(window, monkeypatch):
    called: list[str] = []
    for name, method in (
        ("action_new", "new_file"),
        ("action_open", "open_file_dialog"),
        ("action_save", "save_file"),
        ("action_save_as", "save_file_as"),
        ("action_close_tab", "close_current_tab"),
        ("action_find", "show_search_panel"),
        ("action_replace", "show_replace_panel"),
    ):
        monkeypatch.setattr(
            type(window), method, lambda self, _m=method: called.append(_m)
        )
        getattr(window, name).trigger()

    assert called == [
        "new_file",
        "open_file_dialog",
        "save_file",
        "save_file_as",
        "close_current_tab",
        "show_search_panel",
        "show_replace_panel",
    ]
