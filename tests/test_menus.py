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

import ast
import re
from pathlib import Path

import pytest
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
        window.action_save_with_encoding,
        window.action_reopen_with_encoding,
        window.action_restore_backup,
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


def test_menu_bar_toggle_is_checked_while_the_menu_bar_is_visible(window, make_window):
    """メニューバーが見えている間は、メニューの印も**入っている**こと。

    `action_toggle_menu_bar` は `checkable=True` で作られるため、**初期値は
    「印なし」**。実際の表示状態に合わせているのは
    `_apply_menu_bar_visibility()` の `setChecked()` の 1 行だけ。ところが
    既存のテストは「隠したあとに印が外れていること」しか見ておらず、
    **初期値が偶然 False なので、この行を外しても全部緑のまま**だった
    （34 回目に変異 `vs-menu-bar-sync-check` が生き残って発覚）。

    印がずれると、利用者には「メニューバーは見えているのに印が付いて
    いない」「Ctrl+M を 1 回押しても何も起きない（印を入れる方向に
    動くだけ）」という形で出る。
    """
    assert window.menuBar().isVisible()
    assert window.action_toggle_menu_bar.isChecked() is True

    # 隠して戻したあとも、表示状態と印が一致していること。
    window.set_menu_bar_visible(False)
    assert window.action_toggle_menu_bar.isChecked() is False

    window.set_menu_bar_visible(True)
    assert window.menuBar().isVisible()
    assert window.action_toggle_menu_bar.isChecked() is True

    # あとから開いたウィンドウでも印が合っていること。
    later = make_window()
    assert later.menuBar().isVisible()
    assert later.action_toggle_menu_bar.isChecked() is True


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


#: 「押したら、その名前どおりの処理が 1 つだけ呼ばれる」ことを固定する組。
#: 上の ``test_file_actions_are_connected`` が見ている 7 つ以外を全部並べる。
#: ここに無いのは (1) 引数を取る結線（折り返し・テーマ・読み込み先。専用の
#: テストが上にある）と (2) ``action_quit``（結線先が ``QWidget.close`` で
#: 差し替えられないため、下で実際に閉じることを見る）。
REMAINING_WIRING = [
    ("action_save_with_encoding", "save_file_with_encoding"),
    ("action_reopen_with_encoding", "reopen_file_with_encoding"),
    ("action_restore_backup", "restore_from_backup"),
    ("action_search_input_height", "_prompt_search_input_lines"),
    ("action_next_tab", "activate_next_tab"),
    ("action_previous_tab", "activate_previous_tab"),
    ("action_wrap_fixed", "_prompt_wrap_fixed_column"),
    ("action_font", "_prompt_font"),
    ("action_tab_width_fixed", "_prompt_fixed_tab_width"),
    ("action_editor_bg_color", "_prompt_editor_background_color"),
    ("action_editor_fg_color", "_prompt_editor_text_color"),
    ("action_editor_color_reset", "_reset_editor_colors"),
    ("action_tab_active_color", "_prompt_tab_active_color"),
    ("action_tab_inactive_color", "_prompt_tab_inactive_color"),
    ("action_tab_color_reset", "_reset_tab_colors"),
    ("action_check_updates", "check_for_updates_manually"),
]


@pytest.mark.parametrize(
    ("action_name", "method"), REMAINING_WIRING, ids=[name for name, _ in REMAINING_WIRING]
)
def test_remaining_actions_call_exactly_their_own_handler(
    window, monkeypatch, action_name: str, method: str
) -> None:
    """メニューの項目が、**名前どおりの処理だけ**を呼ぶこと。

    結線は ``menus.py`` の 1 行きりなので、**書き間違えても他のテストは
    全部緑のまま**通る（2026-08-18（37 回目）に、ここに並ぶうち 6 つの
    結線を切る・すり替える変異が生き残って発覚）。利用者から見ると
    「押しても何も起きない」「押すと別のものが出る」——たとえば
    **「バックアップから復元」を押したらファイル選択が開く**——という、
    いちばん問い合わせになりやすい形の壊れ方になる。

    ``REMAINING_WIRING`` に並ぶ**全ての処理**を先に差し替えておいてから
    1 つだけ押すので、**すり替え**（別の処理につないだ）も捕まる。
    """
    called: list[str] = []
    for _, other in REMAINING_WIRING:
        monkeypatch.setattr(
            type(window), other, lambda self, *a, _m=other, **kw: called.append(_m)
        )

    getattr(window, action_name).trigger()

    assert called == [method]


def test_quit_action_closes_the_window(window) -> None:
    """「終了」で実際にウィンドウが閉じること。

    結線先が ``QWidget.close``（Qt 側の処理）なので、上の差し替えでは
    確かめられない。押した結果そのものを見る。**結線を外しても他の
    テストは全部緑のまま**だった（変異 `mn-quit-connect`）。
    """
    assert window.isVisible()

    window.action_quit.trigger()

    assert not window.isVisible()


# ----------------------------------------------------------------------
# チェック付きの項目が、自分の状態をそのまま渡すこと
# ----------------------------------------------------------------------

#: 「印の付け外し」がそのまま設定になる項目。``lambda`` で
#: ``isChecked()`` を渡しているので、**裏返して渡しても**他のテストは
#: 緑のまま通っていた（2026-08-18（37 回目）の変異 3 件）。
CHECKABLE_WIRING = [
    ("action_toggle_menu_bar", "set_menu_bar_visible"),
    ("action_show_line_breaks", "set_show_line_breaks"),
    ("action_check_updates_on_startup", "set_check_updates_on_startup"),
]


@pytest.mark.parametrize(
    ("action_name", "method"), CHECKABLE_WIRING, ids=[name for name, _ in CHECKABLE_WIRING]
)
def test_checkable_actions_pass_their_own_checked_state(
    window, monkeypatch, action_name: str, method: str
) -> None:
    """押したあとの**印の状態と同じ値**が渡ること（逆でないこと）。

    裏返っていると、利用者には「メニューの印は入ったのに、効き目は
    切れている」（あるいはその逆）という形で出る。とくに
    ``action_toggle_menu_bar`` は、印と実際の表示が食い違うと
    **Ctrl+M を 1 回押しても戻らない**ことになる。
    """
    got: list[bool] = []
    monkeypatch.setattr(type(window), method, lambda self, value: got.append(value))
    action = getattr(window, action_name)

    action.setChecked(False)
    action.trigger()
    assert action.isChecked() is True
    assert got[-1] is True

    action.trigger()
    assert action.isChecked() is False
    assert got[-1] is False


def test_menu_bar_toggle_is_reachable_when_the_menu_bar_is_hidden(window) -> None:
    """メニューバーを隠しても Ctrl+M で戻せること。

    隠すと**この項目自体もメニューごと見えなくなる**ので、ウィンドウ
    直下にも登録しておかないとショートカットが届かない（＝二度と
    メニューバーを出せない）。``window.addAction()`` の 1 行を外しても
    全テストが緑のままだった（変異 `mn-menu-bar-addaction`）。
    """
    assert window.action_toggle_menu_bar in window.actions()
    # ショートカットの届く範囲はウィンドウ全体（既定値と同じだが、
    # メニューの外から効かせるのが目的なので明示しておく）。
    assert (
        window.action_toggle_menu_bar.shortcutContext()
        == Qt.ShortcutContext.WindowShortcut
    )

    window.set_menu_bar_visible(False)

    assert not window.menuBar().isVisible()
    assert window.action_toggle_menu_bar in window.actions()


# ----------------------------------------------------------------------
# 実機と offscreen で解決結果が違うショートカット
# ----------------------------------------------------------------------

#: ``QKeySequence.StandardKey`` のうち、**この環境 (offscreen) と実機
#: (KDE/xcb) で別のキーに解決されるもの**。
#:
#: * ``Replace`` … offscreen では ``Ctrl+H`` だが、実機では ``Ctrl+R``。
#:   2026-08-07 に実機で発覚し、``QKeySequence("Ctrl+H")`` を直に書く形へ
#:   直した経緯がある。
#: * ``Quit`` … offscreen では**空**（＝割り当て無し）だが、実機では
#:   ``Ctrl+Q``。「終了に Ctrl+Q は誤操作しやすい」という実機
#:   フィードバックで外した経緯がある。
#:
#: どちらも**この環境では素通りする**——``setShortcut`` を
#: ``StandardKey`` へ戻しても ``test_replace_shortcut_is_ctrl_h`` も
#: ``test_quit_has_no_shortcut`` も緑のまま通る（2026-08-18（37 回目）に
#: 変異 `mn-replace-standard-key`・`mn-quit-shortcut` が生き残って発覚）。
#: 実行してから確かめるのでは捕まえられないので、**書いてあるかどうか**を
#: 構文木で見る。
PLATFORM_DEPENDENT_STANDARD_KEYS = {"Replace", "Quit"}

SOURCE_DIR = Path(__file__).resolve().parent.parent / "src"


def test_platform_dependent_standard_keys_are_never_used() -> None:
    """実機で別のキーになる ``StandardKey`` を使っていないこと。

    文字列ではなく構文木で見ているので、この説明文のように**名前を
    書いただけのもの**は引っかからない。
    """
    offenders: list[str] = []
    for path in sorted(SOURCE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr not in PLATFORM_DEPENDENT_STANDARD_KEYS:
                continue
            # ``QKeySequence.StandardKey.Replace`` の形だけを見る
            # （``str.replace`` のようなメソッド名と取り違えないため）。
            parent = node.value
            if isinstance(parent, ast.Attribute) and parent.attr == "StandardKey":
                offenders.append(f"{path.name}:{node.lineno} StandardKey.{node.attr}")

    assert offenders == [], (
        "実機 (KDE) で offscreen と違うキーに解決される StandardKey が使われている: "
        + ", ".join(offenders)
    )
