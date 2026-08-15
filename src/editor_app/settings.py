"""アプリ全体で永続化する設定（実機フィードバックにより追加）.

``QSettings`` を薄くラップしているだけ。``QSettings()``（引数無し）は
``QApplication`` に設定した組織名・アプリ名から保存先を自動的に決める
（Linux では ``~/.config/<組織名>/<アプリ名>.conf``）ため、
``create_app()`` で ``setOrganizationName()``/``setApplicationName()``
を呼んでおく必要がある。

テストからは、この ``QSettings`` を差し替えて（本物の設定ファイルに
書き込まないよう）一時ファイルへリダイレクトする
（``tests/conftest.py`` の ``_isolated_settings`` 参照）。

**文字列として読む値には必ず ``type=str`` を付けること。** 設定ファイル
（INI）の値にカンマが入っていると、``QSettings.value()`` は **``list``**
を返す。それを ``mode in _VALID_...`` のような集合の判定にかけると
``TypeError: unhashable type: 'list'`` になり、**壊れた設定ファイルが
1 つあるだけでアプリが起動しなくなる**（読み込みはどれも起動時に通る）。
``type=str`` を付けておけば Qt 側が空文字に均してくれるので、その後の
「知らない値なら既定値」の判定に素直に落ちる。
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtGui import QColor

from .editor import (
    DEFAULT_FONT_FAMILY,
    DEFAULT_FONT_SIZE,
    DEFAULT_SHOW_LINE_BREAKS,
    DEFAULT_WRAP_COLUMN,
    DEFAULT_WRAP_MODE,
    WRAP_FIXED,
    WRAP_NONE,
    WRAP_WINDOW,
)
from .theme import DEFAULT_THEME, VALID_THEMES

_WRAP_MODE_KEY = "editor/wrap_mode"
_WRAP_COLUMN_KEY = "editor/wrap_column"
_FONT_FAMILY_KEY = "editor/font_family"
_FONT_SIZE_KEY = "editor/font_size"
_WINDOW_GEOMETRY_KEY = "window/geometry"
_THEME_KEY = "appearance/theme"
_EDITOR_BG_KEY = "editor/custom_background"
_EDITOR_FG_KEY = "editor/custom_foreground"
_TAB_ACTIVE_COLOR_KEY = "appearance/tab_active_color"
_TAB_INACTIVE_COLOR_KEY = "appearance/tab_inactive_color"
_IPC_TARGET_KEY = "ipc/open_target_mode"
_SHOW_LINE_BREAKS_KEY = "editor/show_line_breaks"
_MENU_BAR_VISIBLE_KEY = "appearance/menu_bar_visible"
_SEARCH_INPUT_LINES_KEY = "search/input_visible_lines"
_CHECK_UPDATES_ON_STARTUP_KEY = "update/check_on_startup"

#: 検索欄・置換欄の既定の表示行数（実機フィードバックにより、
#: 元は 3 行だったものを 2 倍の 6 行にした上で、設定できるようにした）。
DEFAULT_SEARCH_INPUT_LINES = 6

_VALID_WRAP_MODES = {WRAP_NONE, WRAP_WINDOW, WRAP_FIXED}

#: 他プロセス（ファイルマネージャー等）から転送されたファイルを、
#: 「最初に起動したウィンドウ」に開く（従来からの既定の挙動）。
IPC_TARGET_FIRST = "first"
#: 「最後に起動した（＝一番新しい）ウィンドウ」に開く。
IPC_TARGET_LAST = "last"
#: 「最後にアクティブにしたウィンドウ」に開く。
IPC_TARGET_ACTIVE = "active"
#: 既定値は従来の挙動を維持する（実機フィードバックにより追加した機能
#: だが、既に「最初のウィンドウに開く」ことに依存して使っている利用者が
#: いるため、既定を変えて驚かせないようにする）。
DEFAULT_IPC_TARGET_MODE = IPC_TARGET_FIRST

_VALID_IPC_TARGET_MODES = {IPC_TARGET_FIRST, IPC_TARGET_LAST, IPC_TARGET_ACTIVE}

#: 旧アプリ名。2026-08-10 に Tabula → Tuxpad へ改名した。旧名称で保存されて
#: いた設定を新名称へ一度だけ引き継ぐために覚えておく（下の migrate_legacy_settings）。
_LEGACY_APP_NAME = "Tabula"


def migrate_legacy_settings() -> None:
    """旧名称(Tabula)時代の設定を、新名称(Tuxpad)へ一度だけ引き継ぐ。

    ``QSettings`` の保存先は組織名・アプリ名から決まるため、改名すると
    ``~/.config/Tabula/Tabula.conf`` → ``~/.config/Tuxpad/Tuxpad.conf`` と
    別ファイルになり、そのままでは配色・フォント等の設定が失われる。
    新設定がまだ空で、旧設定が残っているときだけ旧設定の全キーをコピーする
    （既に新設定があれば何もしない＝冪等）。プライマリインスタンスの起動時に
    1 回だけ呼ぶ（``editor_app.app.main``）。
    """
    current = QSettings()
    if current.allKeys():
        return  # 既に新設定がある（移行済み、または新名称で使い始めた後）
    legacy = QSettings(_LEGACY_APP_NAME, _LEGACY_APP_NAME)
    keys = legacy.allKeys()
    if not keys:
        return  # 引き継ぐ旧設定が無い
    for key in keys:
        current.setValue(key, legacy.value(key))
    current.sync()


def load_wrap_settings() -> tuple[str, int]:
    """前回終了時点の折り返しモードを読み込む。保存されていなければ既定値。"""
    settings = QSettings()

    mode = settings.value(_WRAP_MODE_KEY, DEFAULT_WRAP_MODE, type=str)
    if mode not in _VALID_WRAP_MODES:
        mode = DEFAULT_WRAP_MODE

    try:
        column = int(settings.value(_WRAP_COLUMN_KEY, DEFAULT_WRAP_COLUMN))
    except (TypeError, ValueError):
        column = DEFAULT_WRAP_COLUMN
    if column < 1:
        column = DEFAULT_WRAP_COLUMN

    return mode, column


def save_wrap_settings(mode: str, column: int) -> None:
    """折り返しモードを保存し、次回起動時に復元できるようにする。"""
    settings = QSettings()
    settings.setValue(_WRAP_MODE_KEY, mode)
    settings.setValue(_WRAP_COLUMN_KEY, column)


def load_show_line_breaks() -> bool:
    """前回終了時点の「改行記号を表示」設定を読み込む。"""
    settings = QSettings()
    return bool(settings.value(_SHOW_LINE_BREAKS_KEY, DEFAULT_SHOW_LINE_BREAKS, type=bool))


def save_show_line_breaks(enabled: bool) -> None:
    """「改行記号を表示」設定を保存し、次回起動時に復元できるようにする。"""
    settings = QSettings()
    settings.setValue(_SHOW_LINE_BREAKS_KEY, enabled)


def load_menu_bar_visible() -> bool:
    """前回終了時点の「メニューバーを表示」設定を読み込む（既定は表示）。"""
    settings = QSettings()
    return bool(settings.value(_MENU_BAR_VISIBLE_KEY, True, type=bool))


def save_menu_bar_visible(visible: bool) -> None:
    """「メニューバーを表示」設定を保存し、次回起動時に復元できるようにする。"""
    settings = QSettings()
    settings.setValue(_MENU_BAR_VISIBLE_KEY, visible)


def load_check_updates_on_startup() -> bool:
    """「起動時に更新を確認する」設定を読み込む（既定は ON）。

    git clone で導入した利用PCが、開発PCの更新を取りこぼさないよう既定は
    ON にしてある。更新を作っている側（開発PC）では毎回の確認が邪魔になる
    ので、``設定 → 更新`` から OFF にできる。
    """
    settings = QSettings()
    return bool(settings.value(_CHECK_UPDATES_ON_STARTUP_KEY, True, type=bool))


def save_check_updates_on_startup(enabled: bool) -> None:
    """「起動時に更新を確認する」設定を保存する。"""
    settings = QSettings()
    settings.setValue(_CHECK_UPDATES_ON_STARTUP_KEY, enabled)


def load_search_input_lines() -> int:
    """前回終了時点の検索欄・置換欄の表示行数を読み込む。"""
    settings = QSettings()
    try:
        lines = int(settings.value(_SEARCH_INPUT_LINES_KEY, DEFAULT_SEARCH_INPUT_LINES))
    except (TypeError, ValueError):
        lines = DEFAULT_SEARCH_INPUT_LINES
    if lines < 1:
        lines = DEFAULT_SEARCH_INPUT_LINES
    return lines


def save_search_input_lines(lines: int) -> None:
    """検索欄・置換欄の表示行数を保存し、次回起動時に復元できるようにする。"""
    settings = QSettings()
    settings.setValue(_SEARCH_INPUT_LINES_KEY, lines)


def load_font_settings() -> tuple[str, int]:
    """前回終了時点の本文フォント (ファミリー, サイズ pt) を読み込む。

    保存されていない・壊れている場合は既定値
    (``DEFAULT_FONT_FAMILY`` / ``DEFAULT_FONT_SIZE``) を返す。
    """
    settings = QSettings()

    family = settings.value(_FONT_FAMILY_KEY, DEFAULT_FONT_FAMILY, type=str)
    if not family:
        family = DEFAULT_FONT_FAMILY

    try:
        size = int(settings.value(_FONT_SIZE_KEY, DEFAULT_FONT_SIZE))
    except (TypeError, ValueError):
        size = DEFAULT_FONT_SIZE
    if size < 1:
        size = DEFAULT_FONT_SIZE

    return family, size


def save_font_settings(family: str, size: int) -> None:
    """本文フォントを保存し、次回起動時に復元できるようにする。"""
    settings = QSettings()
    settings.setValue(_FONT_FAMILY_KEY, family)
    settings.setValue(_FONT_SIZE_KEY, size)


def load_window_geometry() -> QByteArray | None:
    """前回終了時点のウィンドウ位置・サイズを読み込む（無ければ None）。

    ``QMainWindow.saveGeometry()``/``restoreGeometry()`` の形式（最大化
    状態やマルチモニタ環境も含めて復元できる）でそのまま保存・復元する。

    **Wayland では「位置」の復元は効かない見込み**（アプリが自分の画面上の
    位置を指定することを Wayland が設計上許可していないため。ウィンドウの
    配置はコンポジタの役目）。サイズの復元は効く。これはプラットフォームの
    制約であってこのアプリの不具合ではないので、低レベル API を直接叩いて
    まで回避しようとしないこと（「シンプルなエディタ」という方針にも反する）。
    """
    settings = QSettings()
    value = settings.value(_WINDOW_GEOMETRY_KEY)
    if not isinstance(value, QByteArray) or value.isEmpty():
        return None
    return value


def save_window_geometry(geometry: QByteArray) -> None:
    """ウィンドウを閉じるときに位置・サイズを保存する。"""
    settings = QSettings()
    settings.setValue(_WINDOW_GEOMETRY_KEY, geometry)


def load_theme() -> str:
    """前回終了時点のテーマ（ライト/ダーク）を読み込む。"""
    settings = QSettings()
    theme = settings.value(_THEME_KEY, DEFAULT_THEME, type=str)
    return theme if theme in VALID_THEMES else DEFAULT_THEME


def save_theme(theme: str) -> None:
    """テーマを保存し、次回起動時に復元できるようにする。"""
    settings = QSettings()
    settings.setValue(_THEME_KEY, theme)


def load_editor_colors() -> tuple[QColor | None, QColor | None]:
    """カスタム設定中のエディタ配色 (背景色, 文字色) を読み込む。

    テーマに合わせている（カスタムしていない）場合はそれぞれ None。
    """
    settings = QSettings()
    bg_name = settings.value(_EDITOR_BG_KEY, "", type=str)
    fg_name = settings.value(_EDITOR_FG_KEY, "", type=str)

    background = QColor(bg_name) if bg_name else None
    if background is not None and not background.isValid():
        background = None

    text = QColor(fg_name) if fg_name else None
    if text is not None and not text.isValid():
        text = None

    return background, text


def save_editor_colors(background: QColor | None, text: QColor | None) -> None:
    """エディタの配色を保存し、次回起動時・新規ウィンドウに引き継ぐ。"""
    settings = QSettings()
    settings.setValue(_EDITOR_BG_KEY, background.name() if background is not None else "")
    settings.setValue(_EDITOR_FG_KEY, text.name() if text is not None else "")


def _color_or_none(name: str) -> QColor | None:
    """設定に入っている色名（``#RRGGBB``）を QColor に戻す。空/不正なら None。"""
    if not name:
        return None
    color = QColor(name)
    return color if color.isValid() else None


def load_tab_colors() -> tuple[QColor | None, QColor | None]:
    """カスタム設定中のタブの色 (アクティブ, 非アクティブ) を読み込む。

    未設定（＝ウィジェットのパレット任せ）の場合はそれぞれ None。
    """
    settings = QSettings()
    active = _color_or_none(settings.value(_TAB_ACTIVE_COLOR_KEY, "", type=str))
    inactive = _color_or_none(settings.value(_TAB_INACTIVE_COLOR_KEY, "", type=str))
    return active, inactive


def save_tab_colors(active: QColor | None, inactive: QColor | None) -> None:
    """タブの色を保存し、次回起動時・新規ウィンドウに引き継ぐ。"""
    settings = QSettings()
    settings.setValue(_TAB_ACTIVE_COLOR_KEY, active.name() if active is not None else "")
    settings.setValue(_TAB_INACTIVE_COLOR_KEY, inactive.name() if inactive is not None else "")


def load_ipc_target_mode() -> str:
    """他プロセスから転送されたファイルを、どのウィンドウに開くかの設定。

    ``IPC_TARGET_FIRST``（最初に起動したウィンドウ、既定）または
    ``IPC_TARGET_ACTIVE``（最後にアクティブにしたウィンドウ）。
    """
    settings = QSettings()
    mode = settings.value(_IPC_TARGET_KEY, DEFAULT_IPC_TARGET_MODE, type=str)
    return mode if mode in _VALID_IPC_TARGET_MODES else DEFAULT_IPC_TARGET_MODE


def save_ipc_target_mode(mode: str) -> None:
    """読み込み先の設定を保存し、次回起動時にも復元できるようにする。"""
    settings = QSettings()
    settings.setValue(_IPC_TARGET_KEY, mode)
