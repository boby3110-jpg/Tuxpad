"""メニューバーとアクションの組み立て.

**メニューに出る項目は必ずここで作ること**（``MainWindow`` 側には置かない）。
``QAction`` は「メニューの文言」「ショートカット」「ステータスバーの説明」
「押したときに呼ぶ処理」の 4 つをまとめて持つため、ここを読めば
「どの操作がどのメニューのどこにあり、どのキーで、何を呼ぶか」が
1 か所で分かるようにしてある。

作った ``QAction`` は ``window.action_xxx`` として ``MainWindow`` に生やす。
チェック状態の同期（``MainWindow._sync_*_action_checked()``）や
実際の処理は今まで通り ``MainWindow`` 側の役目で、このモジュールは
「組み立てと結線」だけを行う。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtCore import Qt

from .editor import WRAP_NONE, WRAP_WINDOW
from .settings import IPC_TARGET_ACTIVE, IPC_TARGET_FIRST, IPC_TARGET_LAST
from .theme import THEME_DARK, THEME_LIGHT

if TYPE_CHECKING:  # pragma: no cover - 型注釈のためだけの import
    from .main_window import MainWindow


def create_actions(window: "MainWindow") -> None:
    """``window`` の ``action_*`` 属性を作り、処理に結線する。

    チェック付きアクションの初期状態はここでは決めない。呼び出し側が
    このあと ``MainWindow._sync_*_action_checked()`` を呼ぶこと。
    """
    _create_file_actions(window)
    _create_edit_actions(window)
    _create_view_actions(window)
    _create_settings_actions(window)


def _create_file_actions(window: "MainWindow") -> None:
    """「ファイル」メニューのアクション（機能 1・2・3）。"""
    window.action_new = QAction("新規作成(&N)", window)
    window.action_new.setShortcut(QKeySequence.StandardKey.New)
    window.action_new.setStatusTip("新しい空のタブを作成します")
    window.action_new.triggered.connect(window.new_file)

    window.action_open = QAction("開く(&O)...", window)
    window.action_open.setShortcut(QKeySequence.StandardKey.Open)
    window.action_open.setStatusTip("既存のファイルをタブで開きます")
    window.action_open.triggered.connect(window.open_file_dialog)

    window.action_save = QAction("上書き保存(&S)", window)
    window.action_save.setShortcut(QKeySequence.StandardKey.Save)
    window.action_save.setStatusTip("現在のタブをファイルに保存します")
    window.action_save.triggered.connect(window.save_file)

    window.action_save_as = QAction("名前を付けて保存(&A)...", window)
    window.action_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
    window.action_save_as.setStatusTip("現在のタブを別名で保存します")
    window.action_save_as.triggered.connect(window.save_file_as)

    # 文字コードを選び直して保存する（実機フィードバックにより追加）。
    # Shift-JIS のファイルに絵文字などを貼ると保存できなくなるため、
    # UTF-8 へ移す逃げ道がここになる。ショートカットは割り当てない
    # （普段使う操作ではなく、Ctrl+Shift+S の押し間違いも避けたいため）。
    window.action_save_with_encoding = QAction("文字コードを指定して保存(&E)...", window)
    window.action_save_with_encoding.setStatusTip(
        "文字コードを選び直して現在のタブを保存します"
    )
    window.action_save_with_encoding.triggered.connect(window.save_file_with_encoding)

    # 文字コードを選び直して「開き直す」。ステータスバーに出る拮抗の印
    # （別の文字コードでも読めてしまったファイル）を見た利用者が、
    # その場で正しい読み方に切り替えられるようにするための逃げ道。
    # 保存の対になるので、「文字コードを指定して保存」の隣に置く。
    window.action_reopen_with_encoding = QAction("文字コードを指定して開き直す(&R)...", window)
    window.action_reopen_with_encoding.setStatusTip(
        "現在のタブを、選んだ文字コードでファイルから読み直します"
    )
    window.action_reopen_with_encoding.triggered.connect(window.reopen_file_with_encoding)

    # 上書き前の控えからの復元（引き継ぎ ⑦）。**手作業のコピー&ペーストに
    # させず、アプリの中だけで完結させる**のがこの項目の存在理由なので、
    # 「バックアップフォルダを開く」ではなくここから復元まで行う。
    window.action_restore_backup = QAction("バックアップから復元(&B)...", window)
    window.action_restore_backup.setStatusTip(
        "保存で上書きされる前の内容に、このタブのファイルを戻します"
    )
    window.action_restore_backup.triggered.connect(window.restore_from_backup)

    window.action_close_tab = QAction("タブを閉じる(&W)", window)
    # StandardKey.Close は環境によって内容が変わる（例えば実機の KDE (xcb)
    # では ['Ctrl+W', 'Close'] を返す）。テキストエディタで一般的な
    # Ctrl+W を必ず含めつつ、重複した QKeySequence を渡すと
    # "Ambiguous shortcut overload" になり何も起きなくなるため、
    # 同じ内容は取り除いてから設定する。
    close_shortcuts = [QKeySequence("Ctrl+W")]
    for seq in QKeySequence.keyBindings(QKeySequence.StandardKey.Close):
        if seq not in close_shortcuts:
            close_shortcuts.append(seq)
    window.action_close_tab.setShortcuts(close_shortcuts)
    window.action_close_tab.setStatusTip("現在のタブを閉じます")
    window.action_close_tab.triggered.connect(window.close_current_tab)

    window.action_quit = QAction("終了(&X)", window)
    # Ctrl+Q は誤操作しやすいとの実機フィードバックにより、ショートカットは
    # 割り当てない（メニューからのみ終了できる）。
    window.action_quit.triggered.connect(window.close)


def _create_edit_actions(window: "MainWindow") -> None:
    """「編集」メニューのアクション（機能 6・7 とタブ切り替え）。"""
    window.action_find = QAction("検索(&F)...", window)
    window.action_find.setShortcut(QKeySequence.StandardKey.Find)
    window.action_find.setStatusTip("このウィンドウの全タブを横断して検索します")
    window.action_find.triggered.connect(window.show_search_panel)

    window.action_replace = QAction("置換(&H)...", window)
    # QKeySequence.StandardKey.Replace は実機の KDE (xcb) だと "Ctrl+R" に
    # 解決される（offscreen プラットフォームでは "Ctrl+H" になり、
    # テストではこれに気づけなかった）。EmEditor 等の一般的な慣習に
    # 合わせて "Ctrl+H" を明示的に指定する。
    window.action_replace.setShortcut(QKeySequence("Ctrl+H"))
    window.action_replace.setStatusTip("このウィンドウの全タブを横断して検索・置換します")
    window.action_replace.triggered.connect(window.show_replace_panel)

    # タブ切り替え（実機フィードバックにより追加）。要望は Ctrl+Tab の
    # 「次のタブへ」だけだったが、対になる操作として自然なので
    # Ctrl+Shift+Tab の「前のタブへ」も併せて用意した。
    window.action_next_tab = QAction("次のタブ(&X)", window)
    window.action_next_tab.setShortcut(QKeySequence("Ctrl+Tab"))
    window.action_next_tab.setStatusTip("このウィンドウの次のタブに切り替えます")
    window.action_next_tab.triggered.connect(window.activate_next_tab)

    window.action_previous_tab = QAction("前のタブ(&P)", window)
    window.action_previous_tab.setShortcut(QKeySequence("Ctrl+Shift+Tab"))
    window.action_previous_tab.setStatusTip("このウィンドウの前のタブに切り替えます")
    window.action_previous_tab.triggered.connect(window.activate_previous_tab)

    # 検索欄・置換欄の表示行数（実機フィードバックにより追加）。
    window.action_search_input_height = QAction("検索欄の高さ(&H)...", window)
    window.action_search_input_height.setStatusTip(
        "検索・置換パネルの入力欄の表示行数を変更します"
    )
    window.action_search_input_height.triggered.connect(
        window._prompt_search_input_lines
    )


def _create_view_actions(window: "MainWindow") -> None:
    """「表示」メニューのアクション（折り返し・フォント・配色などの設定）。"""
    window.action_wrap_none = QAction("折り返さない(&N)", window, checkable=True)
    window.action_wrap_window = QAction("ウィンドウ幅で折り返す(&W)", window, checkable=True)
    window.action_wrap_fixed = QAction("指定文字数で折り返す(&C)...", window, checkable=True)

    window._wrap_action_group = _exclusive_group(
        window,
        window.action_wrap_none,
        window.action_wrap_window,
        window.action_wrap_fixed,
    )

    window.action_wrap_none.triggered.connect(lambda: window.set_wrap_mode(WRAP_NONE))
    window.action_wrap_window.triggered.connect(lambda: window.set_wrap_mode(WRAP_WINDOW))
    window.action_wrap_fixed.triggered.connect(window._prompt_wrap_fixed_column)

    # 改行記号の表示（EmEditor 相当、実機フィードバックにより追加）。
    # 排他選択ではない単独のチェック付きアクションなので、他の項目のように
    # QActionGroup は使わない。
    window.action_show_line_breaks = QAction("改行記号を表示(&L)", window, checkable=True)
    window.action_show_line_breaks.setStatusTip("行末に改行記号（↓）を表示します")
    window.action_show_line_breaks.triggered.connect(
        lambda: window.set_show_line_breaks(window.action_show_line_breaks.isChecked())
    )

    # 本文フォント（実機フィードバックにより追加）。折り返しモードと
    # 同じく、このウィンドウの全タブに適用して QSettings に保存する。
    window.action_font = QAction("フォント(&F)...", window)
    window.action_font.setStatusTip("本文のフォントとサイズを変更します")
    window.action_font.triggered.connect(window._prompt_font)

    # アプリ全体のテーマ（実機フィードバックにより追加）。QApplication
    # 単位の設定なので、切り替えるとすべてのウィンドウに即座に反映される。
    window.action_theme_light = QAction("ライト(&L)", window, checkable=True)
    window.action_theme_dark = QAction("ダーク(&D)", window, checkable=True)

    window._theme_action_group = _exclusive_group(
        window, window.action_theme_light, window.action_theme_dark
    )

    window.action_theme_light.triggered.connect(lambda: window.set_theme(THEME_LIGHT))
    window.action_theme_dark.triggered.connect(lambda: window.set_theme(THEME_DARK))

    # タブの幅（実機フィードバックにより追加）。ファイル名の長さで
    # タブ幅がバラバラになるのを避けたいという要望への対応。
    # **この設定はウィンドウごとに独立**で、QSettings には保存しない
    # （次回起動時・新しいウィンドウには引き継がない）。
    window.action_tab_width_auto = QAction("自動(&A)", window, checkable=True)
    window.action_tab_width_fixed = QAction("固定幅を指定(&F)...", window, checkable=True)

    window._tab_width_action_group = _exclusive_group(
        window, window.action_tab_width_auto, window.action_tab_width_fixed
    )

    window.action_tab_width_auto.triggered.connect(lambda: window.set_fixed_tab_width(None))
    window.action_tab_width_fixed.triggered.connect(window._prompt_fixed_tab_width)

    # エディタ本文の配色（実機フィードバックにより追加）。アプリの
    # テーマとは独立に、背景色・文字色を固定できる。
    window.action_editor_bg_color = QAction("背景色を選択(&B)...", window)
    window.action_editor_bg_color.triggered.connect(window._prompt_editor_background_color)

    window.action_editor_fg_color = QAction("文字色を選択(&T)...", window)
    window.action_editor_fg_color.triggered.connect(window._prompt_editor_text_color)

    window.action_editor_color_reset = QAction("テーマに合わせる（リセット）(&R)", window)
    window.action_editor_color_reset.triggered.connect(window._reset_editor_colors)

    # タブの配色（実機フィードバックにより追加）。
    window.action_tab_active_color = QAction("アクティブなタブの色(&A)...", window)
    window.action_tab_active_color.triggered.connect(window._prompt_tab_active_color)

    window.action_tab_inactive_color = QAction("非アクティブなタブの色(&I)...", window)
    window.action_tab_inactive_color.triggered.connect(window._prompt_tab_inactive_color)

    window.action_tab_color_reset = QAction("既定に戻す(&R)", window)
    window.action_tab_color_reset.triggered.connect(window._reset_tab_colors)

    # ファイルマネージャー等から別のファイルを開いたときに、起動中の
    # どのウィンドウに読み込むか（実機フィードバックにより追加）。
    # アプリ全体で共有する設定として扱う（テーマと同じ考え方）。
    window.action_open_target_first = QAction("最初のウィンドウ(&F)", window, checkable=True)
    window.action_open_target_last = QAction("最後のウィンドウ(&L)", window, checkable=True)
    window.action_open_target_active = QAction(
        "最後にアクティブにしたウィンドウ(&A)", window, checkable=True
    )

    window._open_target_action_group = _exclusive_group(
        window,
        window.action_open_target_first,
        window.action_open_target_last,
        window.action_open_target_active,
    )

    window.action_open_target_first.triggered.connect(
        lambda: window.set_ipc_target_mode(IPC_TARGET_FIRST)
    )
    window.action_open_target_last.triggered.connect(
        lambda: window.set_ipc_target_mode(IPC_TARGET_LAST)
    )
    window.action_open_target_active.triggered.connect(
        lambda: window.set_ipc_target_mode(IPC_TARGET_ACTIVE)
    )


def _create_settings_actions(window: "MainWindow") -> None:
    """「設定」メニューのアクション（実機フィードバックにより追加）。

    配色・テーマ・タブ幅などの「見た目の設定」は「表示」から独立させて
    ここへ集約した。加えて、メニューバー自体の表示/非表示を切り替える。
    """
    window.action_toggle_menu_bar = QAction("メニューバーを表示(&M)", window, checkable=True)
    # **メニューバーを隠すと、隠す/戻すの項目もろとも見えなくなる**ため、
    # メニューに頼らず戻せるよう Ctrl+M を割り当てる。アクションを
    # ウィンドウ直下にも登録しておくことで、メニューバーが隠れていても
    # ショートカットが効く（ショートカットの文脈はウィンドウ全体）。
    window.action_toggle_menu_bar.setShortcut(QKeySequence("Ctrl+M"))
    window.action_toggle_menu_bar.setShortcutContext(
        Qt.ShortcutContext.WindowShortcut
    )
    window.action_toggle_menu_bar.setStatusTip(
        "メニューバーの表示/非表示を切り替えます（隠しても Ctrl+M で戻せます）"
    )
    window.action_toggle_menu_bar.triggered.connect(
        lambda: window.set_menu_bar_visible(window.action_toggle_menu_bar.isChecked())
    )
    window.addAction(window.action_toggle_menu_bar)

    # アプリ内アップデート確認（git clone で導入した利用PCが、開発PCの
    # 更新を取り込むためのもの）。中身は update_check.py が受け持つ。
    window.action_check_updates = QAction("更新を確認(&C)...", window)
    window.action_check_updates.setStatusTip(
        "新しいバージョンが公開されていないか確認します"
    )
    window.action_check_updates.triggered.connect(window.check_for_updates_manually)

    window.action_check_updates_on_startup = QAction(
        "起動時に更新を確認する(&S)", window, checkable=True
    )
    window.action_check_updates_on_startup.setStatusTip(
        "起動するたびに、新しいバージョンがないか自動で確認します"
    )
    window.action_check_updates_on_startup.triggered.connect(
        lambda: window.set_check_updates_on_startup(
            window.action_check_updates_on_startup.isChecked()
        )
    )


def _exclusive_group(window: "MainWindow", *actions: QAction) -> QActionGroup:
    """``actions`` を「同時に 1 つだけ選べる」ひとまとまりにする。

    ``QActionGroup`` は ``window`` の子にしておく（ウィンドウと寿命を揃える）。
    """
    group = QActionGroup(window)
    group.setExclusive(True)
    for action in actions:
        group.addAction(action)
    return group


def create_menus(window: "MainWindow") -> None:
    """``create_actions()`` で作ったアクションをメニューバーに並べる。"""
    file_menu = window.menuBar().addMenu("ファイル(&F)")
    file_menu.addAction(window.action_new)
    file_menu.addAction(window.action_open)
    file_menu.addSeparator()
    file_menu.addAction(window.action_save)
    file_menu.addAction(window.action_save_as)
    file_menu.addAction(window.action_save_with_encoding)
    file_menu.addAction(window.action_reopen_with_encoding)
    file_menu.addSeparator()
    file_menu.addAction(window.action_restore_backup)
    file_menu.addSeparator()
    file_menu.addAction(window.action_close_tab)
    file_menu.addAction(window.action_quit)

    edit_menu = window.menuBar().addMenu("編集(&E)")
    edit_menu.addAction(window.action_find)
    edit_menu.addAction(window.action_replace)
    edit_menu.addAction(window.action_search_input_height)
    edit_menu.addSeparator()
    edit_menu.addAction(window.action_next_tab)
    edit_menu.addAction(window.action_previous_tab)

    view_menu = window.menuBar().addMenu("表示(&V)")
    view_menu.addAction(window.action_wrap_none)
    view_menu.addAction(window.action_wrap_window)
    view_menu.addAction(window.action_wrap_fixed)
    view_menu.addSeparator()
    view_menu.addAction(window.action_show_line_breaks)
    view_menu.addSeparator()
    view_menu.addAction(window.action_font)

    # 「設定」メニュー（実機フィードバックにより新設）。配色・テーマ・
    # タブ幅などの「見た目の設定」を「表示」から移してここへ集約する。
    settings_menu = window.menuBar().addMenu("設定(&S)")

    tab_width_menu = settings_menu.addMenu("タブの幅(&B)")
    tab_width_menu.addAction(window.action_tab_width_auto)
    tab_width_menu.addAction(window.action_tab_width_fixed)

    tab_color_menu = settings_menu.addMenu("タブの配色(&K)")
    tab_color_menu.addAction(window.action_tab_active_color)
    tab_color_menu.addAction(window.action_tab_inactive_color)
    tab_color_menu.addSeparator()
    tab_color_menu.addAction(window.action_tab_color_reset)

    theme_menu = settings_menu.addMenu("テーマ(&T)")
    theme_menu.addAction(window.action_theme_light)
    theme_menu.addAction(window.action_theme_dark)

    editor_color_menu = settings_menu.addMenu("エディタの配色(&C)")
    editor_color_menu.addAction(window.action_editor_bg_color)
    editor_color_menu.addAction(window.action_editor_fg_color)
    editor_color_menu.addSeparator()
    editor_color_menu.addAction(window.action_editor_color_reset)

    open_target_menu = settings_menu.addMenu("外部から開くファイルの読み込み先(&O)")
    open_target_menu.addAction(window.action_open_target_first)
    open_target_menu.addAction(window.action_open_target_last)
    open_target_menu.addAction(window.action_open_target_active)

    update_menu = settings_menu.addMenu("更新(&U)")
    update_menu.addAction(window.action_check_updates)
    update_menu.addSeparator()
    update_menu.addAction(window.action_check_updates_on_startup)

    settings_menu.addSeparator()
    settings_menu.addAction(window.action_toggle_menu_bar)
