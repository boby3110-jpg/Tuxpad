"""ウィンドウの表示設定（折り返し・フォント・配色・テーマ等）をまとめたミックスイン.

``MainWindow`` から「見た目に関する設定」だけを切り出したもの。
ここに入るのは次の 3 つが揃った処理だけで、ファイル操作や検索・置換は
:mod:`editor_app.main_window` に残してある。

1. 設定値を保持する（``_wrap_mode`` などの属性）
2. その設定を実際に反映する（全タブ / 全ウィンドウへ適用する）
3. 反映した内容を ``QSettings`` に保存し、メニューのチェック状態を同期する

設定の効く範囲は 3 種類あり、**新しい表示設定を足すときはまずどれに
当たるかを決めること**（`PROGRESS.md` の「プロジェクト構成の方針」と同じ分類）。

- アプリ全体で共有: テーマ・エディタ本文の配色・タブの配色・外部から開く
  ファイルの読み込み先。変更したら開いている全ウィンドウへ即座に反映し、
  ``QSettings`` にも保存する。
- ウィンドウ単位で永続化: 折り返し・改行記号の表示・本文フォント。
  そのウィンドウの全タブへ適用し、``QSettings`` に保存して次回起動時に復元する。
- ウィンドウ単位で永続化しない: タブの固定幅。そのウィンドウだけの一時的な設定。

ウィンドウ単位の設定は、新しいタブにも他ウィンドウから移ってきたタブにも
効かないといけない。適用は :meth:`ViewSettingsMixin._apply_window_settings`
の 1 か所に集約してあるので、**設定を増やすときはそこに 1 行足すこと**
（``tests/test_tab_window_settings.py`` が「全部のタブに効くこと」を固定している）。

``MainWindow`` にミックスインとして混ぜて使う。``self.tabs``・``self.editors()``・
``self.action_*``（:mod:`editor_app.menus` が生やす）に依存する。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .dialogs import prompt_color, prompt_font, prompt_int
from .editor import DEFAULT_FONT_FAMILY, WRAP_FIXED, WRAP_NONE, WRAP_WINDOW
from .settings import (
    IPC_TARGET_ACTIVE,
    IPC_TARGET_FIRST,
    IPC_TARGET_LAST,
    load_editor_colors,
    load_font_settings,
    load_ipc_target_mode,
    load_menu_bar_visible,
    load_show_line_breaks,
    load_tab_colors,
    load_theme,
    load_wrap_settings,
    save_editor_colors,
    save_font_settings,
    save_ipc_target_mode,
    save_menu_bar_visible,
    save_show_line_breaks,
    save_tab_colors,
    save_theme,
    save_wrap_settings,
)
from .tab_bar import MAX_TAB_WIDTH, MIN_TAB_WIDTH
from .theme import THEME_DARK, THEME_LIGHT, apply_theme


class ViewSettingsMixin:
    """表示設定の保持・適用・保存・メニュー同期をまとめたミックスイン。"""

    # ------------------------------------------------------------------
    # 起動時の読み込みと、タブへの適用
    # ------------------------------------------------------------------
    def _load_view_settings(self) -> None:
        """前回終了時の表示設定を ``QSettings`` から読み込んで属性に持つ。

        読み込むだけで、まだどこにも適用しない（``MainWindow.__init__`` が
        タブバーを作る前に呼ぶため）。実際にタブへ効かせるのは
        :meth:`_apply_window_settings`、タブバーへ効かせるのは
        :meth:`_apply_loaded_tab_colors`。
        """
        self._wrap_mode, self._wrap_column = load_wrap_settings()
        self._show_line_breaks = load_show_line_breaks()
        self._font_family, self._font_size = load_font_settings()
        self._editor_bg, self._editor_fg = load_editor_colors()
        self._tab_active_color, self._tab_inactive_color = load_tab_colors()

    def _apply_loaded_tab_colors(self) -> None:
        """読み込み済みのタブ配色をタブバーへ反映する（``self.tabs`` の生成後に呼ぶ）。"""
        self.tabs.set_tab_colors(self._tab_active_color, self._tab_inactive_color)

    def _apply_window_settings(self, editor) -> None:
        """このウィンドウの表示設定を 1 つのタブに適用する。

        **ウィンドウ単位の表示設定を増やしたら、ここに 1 行足すこと。**
        新規タブ (``MainWindow._add_editor``) と、他のウィンドウから移ってきた
        タブ (``MainWindow._adopt_editor``) の両方が必ずここを通るので、
        片方だけ設定し忘れる（＝ドラッグで移したタブだけ折り返しや
        フォントが元のウィンドウのまま、といった食い違い）ことが無くなる。
        """
        editor.set_wrap_mode(self._wrap_mode, column=self._wrap_column)
        editor.set_font_settings(self._font_family, self._font_size)
        editor.set_editor_colors(self._editor_bg, self._editor_fg)
        editor.set_show_line_breaks(self._show_line_breaks)

    def _sync_view_action_states(self) -> None:
        """チェック付きメニュー項目の初期状態を、いまの設定に合わせる。

        ``menus.create_actions()`` の直後に 1 回だけ呼ぶ。個々の同期は
        設定を変えるたびにも呼ばれるので、ここは初期化のためのまとめ役。
        """
        self._sync_wrap_action_checked()
        self.action_show_line_breaks.setChecked(self._show_line_breaks)
        self._sync_theme_action_checked()
        self._sync_tab_width_action_checked()
        self._sync_open_target_action_checked()
        self._apply_menu_bar_visibility()
        # 「起動時に更新を確認する」だけは update_check.py の持ち物だが、
        # チェック付き項目の初期化はここに集めてある。
        self._sync_check_updates_action_checked()

    # ------------------------------------------------------------------
    # 折り返しモード（実機フィードバックにより追加）
    # ------------------------------------------------------------------
    def set_wrap_mode(self, mode: str, *, column: int | None = None) -> None:
        """このウィンドウの全タブ（既存・新規とも）の折り返しモードを変える。

        次回起動時にも復元できるよう、変更内容を ``QSettings`` に保存する。
        """
        self._wrap_mode = mode
        if column is not None:
            self._wrap_column = column
        for editor in self.editors():
            editor.set_wrap_mode(mode, column=self._wrap_column)
        self._sync_wrap_action_checked()
        save_wrap_settings(self._wrap_mode, self._wrap_column)

    def _prompt_wrap_fixed_column(self) -> None:
        """「指定文字数で折り返す」の文字数をダイアログで尋ねる。"""
        value = prompt_int(
            self,
            "指定文字数で折り返す",
            "折り返す文字数:",
            self._wrap_column,
            1,
            1000,
        )
        if value is None:
            # キャンセルされたら、チェック状態を実際のモードに戻す。
            self._sync_wrap_action_checked()
            return
        self.set_wrap_mode(WRAP_FIXED, column=value)

    def _sync_wrap_action_checked(self) -> None:
        action = {
            WRAP_NONE: self.action_wrap_none,
            WRAP_WINDOW: self.action_wrap_window,
            WRAP_FIXED: self.action_wrap_fixed,
        }[self._wrap_mode]
        action.setChecked(True)

    # ------------------------------------------------------------------
    # 改行記号の表示（実機フィードバックにより追加）
    # ------------------------------------------------------------------
    def show_line_breaks(self) -> bool:
        """このウィンドウで改行記号を表示しているかどうか。"""
        return self._show_line_breaks

    def set_show_line_breaks(self, enabled: bool) -> None:
        """このウィンドウの全タブ（既存・新規とも）の改行記号表示を変える。

        折り返しモードと同じくウィンドウ単位の設定。次回起動時にも
        復元できるよう ``QSettings`` に保存する。
        """
        self._show_line_breaks = enabled
        for editor in self.editors():
            editor.set_show_line_breaks(enabled)
        self.action_show_line_breaks.setChecked(enabled)
        save_show_line_breaks(enabled)

    # ------------------------------------------------------------------
    # 本文フォント（実機フィードバックにより追加）
    # ------------------------------------------------------------------
    def font_settings(self) -> tuple[str, int]:
        """このウィンドウに設定されている本文フォント (ファミリー, サイズ pt)。"""
        return self._font_family, self._font_size

    def set_font_settings(self, family: str, size: int) -> None:
        """このウィンドウの全タブ（既存・新規とも）の本文フォントを変える。

        折り返しモードと同じ設計。次回起動時にも復元できるよう
        ``QSettings`` に保存する。
        """
        self._font_family = family
        self._font_size = size
        for editor in self.editors():
            editor.set_font_settings(family, size)
        save_font_settings(family, size)

    def _prompt_font(self) -> None:
        """フォント選択ダイアログを出し、選ばれたフォントを適用する。"""
        chosen = prompt_font(
            self,
            self._font_family,
            self._font_size,
            monospace_hint=self._font_family == DEFAULT_FONT_FAMILY,
        )
        if chosen is None:
            return
        self.set_font_settings(*chosen)

    # ------------------------------------------------------------------
    # タブの幅（実機フィードバックにより追加。ウィンドウごとに独立した設定）
    # ------------------------------------------------------------------
    def fixed_tab_width(self) -> int | None:
        """このウィンドウのタブ固定幅（px）。自動幅なら None。"""
        return self.tabs.fixed_tab_width()

    def set_fixed_tab_width(self, width: int | None) -> None:
        """このウィンドウの全タブの幅を ``width`` px に揃える。

        ``None`` を渡すと従来通りファイル名の長さに応じた自動幅に戻る。
        フォント・折り返しモードと違い、**この設定はウィンドウごとに独立**で
        ``QSettings`` には保存しない（新しいウィンドウ・次回起動時は自動幅から
        始まる）。同じウィンドウの新規タブ・受け入れたタブには、タブバーが
        ウィンドウ内で共通なので自動的に効く。
        """
        self.tabs.set_fixed_tab_width(width)
        self._sync_tab_width_action_checked()

    def _prompt_fixed_tab_width(self) -> None:
        """タブの固定幅をダイアログで尋ねる。"""
        current = self.fixed_tab_width()
        if current is None:
            # 自動幅から切り替えるときは、いま表示されているタブ幅を初期値に
            # しておくと調整しやすい（タブが無ければ最小幅）。
            rect = self.tabs.tabBar().tabRect(self.tabs.currentIndex())
            current = rect.width() if rect.width() > 0 else MIN_TAB_WIDTH

        value = prompt_int(
            self,
            "タブの幅",
            "タブの幅 (px):",
            current,
            MIN_TAB_WIDTH,
            MAX_TAB_WIDTH * 4,
        )
        if value is None:
            # キャンセルされたら、チェック状態を実際の設定に戻す。
            self._sync_tab_width_action_checked()
            return
        self.set_fixed_tab_width(value)

    def _sync_tab_width_action_checked(self) -> None:
        fixed = self.fixed_tab_width() is not None
        self.action_tab_width_fixed.setChecked(fixed)
        self.action_tab_width_auto.setChecked(not fixed)

    # ------------------------------------------------------------------
    # タブの配色（実機フィードバックにより追加。アプリ全体で共有する設定）
    # ------------------------------------------------------------------
    def tab_colors(self) -> tuple[QColor | None, QColor | None]:
        """カスタム設定中のタブの色 (アクティブ, 非アクティブ)。既定なら None。"""
        return self._tab_active_color, self._tab_inactive_color

    def set_tab_colors(self, active: QColor | None, inactive: QColor | None) -> None:
        """タブの背景色を変える。

        エディタ本文の配色（:meth:`set_editor_colors`）と同じ考え方で、
        アプリ全体で共有する設定として扱う。いま開いている全ウィンドウに
        即座に反映し、QSettings で次に開くウィンドウにも引き継ぐ。
        """
        for window in self.open_windows():
            window._tab_active_color = active
            window._tab_inactive_color = inactive
            window.tabs.set_tab_colors(active, inactive)
        save_tab_colors(active, inactive)

    def _prompt_tab_active_color(self) -> None:
        current = self._tab_active_color
        if current is None:
            current = self.tabs.tabBar().palette().color(QPalette.ColorRole.Base)
        color = prompt_color(self, "アクティブなタブの色", current)
        if color is not None:
            self.set_tab_colors(color, self._tab_inactive_color)

    def _prompt_tab_inactive_color(self) -> None:
        current = self._tab_inactive_color
        if current is None:
            current = self.tabs.tabBar().palette().color(QPalette.ColorRole.Button)
        color = prompt_color(self, "非アクティブなタブの色", current)
        if color is not None:
            self.set_tab_colors(self._tab_active_color, color)

    def _reset_tab_colors(self) -> None:
        """タブの配色を既定（パレット任せ）に戻す。"""
        self.set_tab_colors(None, None)

    # ------------------------------------------------------------------
    # テーマ（実機フィードバックにより追加）
    # ------------------------------------------------------------------
    def set_theme(self, theme: str) -> None:
        """アプリ全体のテーマを切り替える。

        ``QApplication`` 単位の設定なので、いま開いている全ウィンドウに
        即座に反映される。各ウィンドウが持つメニューのチェック状態も
        合わせて同期する。
        """
        app = QApplication.instance()
        apply_theme(app, theme)
        save_theme(theme)
        for window in self.open_windows():
            window._sync_theme_action_checked()

    def _sync_theme_action_checked(self) -> None:
        action = {
            THEME_LIGHT: self.action_theme_light,
            THEME_DARK: self.action_theme_dark,
        }[load_theme()]
        action.setChecked(True)

    # ------------------------------------------------------------------
    # メニューバーの表示/非表示（実機フィードバックにより追加）
    # ------------------------------------------------------------------
    def set_menu_bar_visible(self, visible: bool) -> None:
        """メニューバーの表示/非表示を切り替える。

        テーマと同じく、アプリ全体で共有する設定として扱う。いま開いて
        いる全ウィンドウに即座に反映し、QSettings で次に開くウィンドウ・
        次回起動時にも引き継ぐ。隠しても ``Ctrl+M``（``action_toggle_menu_bar``
        のショートカット）で戻せる。
        """
        save_menu_bar_visible(visible)
        for window in self.open_windows():
            window._apply_menu_bar_visibility()

    def _apply_menu_bar_visibility(self) -> None:
        """保存済みの設定に合わせて、このウィンドウのメニューバーを表示/非表示する。"""
        visible = load_menu_bar_visible()
        self.menuBar().setVisible(visible)
        self.action_toggle_menu_bar.setChecked(visible)

    # ------------------------------------------------------------------
    # 外部から開くファイルの読み込み先（実機フィードバックにより追加）
    # ------------------------------------------------------------------
    def set_ipc_target_mode(self, mode: str) -> None:
        """ファイルマネージャー等から新しいファイルを開いたとき、起動中の
        どのウィンドウに読み込むかを切り替える。

        テーマと同じく、アプリ全体で共有する設定として扱う。
        """
        save_ipc_target_mode(mode)
        for window in self.open_windows():
            window._sync_open_target_action_checked()

    def _sync_open_target_action_checked(self) -> None:
        action = {
            IPC_TARGET_FIRST: self.action_open_target_first,
            IPC_TARGET_LAST: self.action_open_target_last,
            IPC_TARGET_ACTIVE: self.action_open_target_active,
        }[load_ipc_target_mode()]
        action.setChecked(True)

    # ------------------------------------------------------------------
    # エディタの配色（実機フィードバックにより追加）
    # ------------------------------------------------------------------
    def set_editor_colors(self, background, text) -> None:
        """エディタ配色を変える。

        テーマと同じ考え方で、アプリ全体で共有する設定として扱う
        （折り返しモードとは異なり、ウィンドウ単位ではない）。いま
        開いている全ウィンドウの全タブに即座に反映し、QSettings で次に
        開くウィンドウへも引き継ぐ。
        """
        for window in self.open_windows():
            window._editor_bg = background
            window._editor_fg = text
            for editor in window.editors():
                editor.set_editor_colors(background, text)
        save_editor_colors(background, text)

    def _prompt_editor_background_color(self) -> None:
        current = self._editor_color_or_default(QPalette.ColorRole.Base, self._editor_bg)
        color = prompt_color(self, "エディタの背景色", current)
        if color is not None:
            self.set_editor_colors(color, self._editor_fg)

    def _prompt_editor_text_color(self) -> None:
        current = self._editor_color_or_default(QPalette.ColorRole.Text, self._editor_fg)
        color = prompt_color(self, "エディタの文字色", current)
        if color is not None:
            self.set_editor_colors(self._editor_bg, color)

    def _editor_color_or_default(self, role: QPalette.ColorRole, custom) -> QColor:
        """配色ダイアログに最初に見せる色。

        カスタム設定中ならその色、していなければ現在のタブの実際の色
        （タブが 1 つも無ければアプリ既定のパレットの色）。
        """
        if custom is not None:
            return custom
        editor = self.current_editor()
        if editor is not None:
            return editor.palette().color(role)
        return QPalette().color(role)

    def _reset_editor_colors(self) -> None:
        """エディタの配色をテーマに合わせる（カスタム解除）。"""
        self.set_editor_colors(None, None)
