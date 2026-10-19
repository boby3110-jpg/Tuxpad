"""テーマ（ライト/ダーク）切り替えのテスト。実機フィードバックにより追加。"""

from __future__ import annotations

import re

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from editor_app.main_window import MainWindow
from editor_app.settings import load_theme
from editor_app.theme import (
    EXCLUDED_QSS_SELECTORS,
    REQUIRED_SCROLLBAR_SUBCONTROLS,
    THEME_DARK,
    THEME_LIGHT,
    apply_theme,
    colors_for,
    palette_for,
    stylesheet_for,
)


@pytest.fixture(autouse=True)
def _restore_light_theme():
    """テーマはアプリ全体の状態なので、各テストの後で必ずライトへ戻す。"""
    yield
    app = QApplication.instance()
    if app is not None:
        apply_theme(app, THEME_LIGHT)


def _strip_qss_comments(qss: str) -> str:
    return re.sub(r"/\*.*?\*/", "", qss, flags=re.DOTALL)


def test_default_theme_is_light() -> None:
    assert load_theme() == THEME_LIGHT


def test_dark_palette_differs_from_light_palette() -> None:
    light = palette_for(THEME_LIGHT)
    dark = palette_for(THEME_DARK)

    assert light.color(QPalette.ColorRole.Base) != dark.color(QPalette.ColorRole.Base)
    assert light.color(QPalette.ColorRole.Text) != dark.color(QPalette.ColorRole.Text)


def test_unknown_theme_falls_back_to_light() -> None:
    assert palette_for("bogus").color(QPalette.ColorRole.Base) == palette_for(
        THEME_LIGHT
    ).color(QPalette.ColorRole.Base)


def test_apply_theme_sets_application_palette(qtbot) -> None:
    app = QApplication.instance()
    apply_theme(app, THEME_DARK)
    try:
        assert app.palette().color(QPalette.ColorRole.Base) == palette_for(THEME_DARK).color(
            QPalette.ColorRole.Base
        )
    finally:
        apply_theme(app, THEME_LIGHT)


def test_set_theme_persists_and_syncs_menu_across_windows(
    window: MainWindow, make_window
) -> None:
    other = make_window()

    window.set_theme(THEME_DARK)

    assert load_theme() == THEME_DARK
    assert window.action_theme_dark.isChecked()
    assert other.action_theme_dark.isChecked()

    # 後片付け：アプリの見た目をライトに戻しておく。
    window.set_theme(THEME_LIGHT)


# ----------------------------------------------------------------------
# QSS（実機で setPalette() が KDE に上書きされる問題への対応）
# ----------------------------------------------------------------------


@pytest.mark.parametrize("theme", [THEME_LIGHT, THEME_DARK])
def test_stylesheet_is_not_empty_for_both_themes(theme: str) -> None:
    """ライトも「QSS 無し（ネイティブ任せ）」にはしない。

    KDE の既定がダークの環境では、ライトを選んだのに QSS が空だと
    ダークのままに見えてしまう（今回直した不具合そのもの）。
    """
    assert stylesheet_for(theme).strip()


def test_light_and_dark_stylesheets_differ() -> None:
    assert stylesheet_for(THEME_LIGHT) != stylesheet_for(THEME_DARK)


def test_unknown_theme_stylesheet_falls_back_to_light() -> None:
    assert stylesheet_for("bogus") == stylesheet_for(THEME_LIGHT)


@pytest.mark.parametrize("theme", [THEME_LIGHT, THEME_DARK])
@pytest.mark.parametrize("selector", EXCLUDED_QSS_SELECTORS)
def test_stylesheet_never_targets_editor_or_generic_widgets(
    theme: str, selector: str
) -> None:
    """エディタ本文と包括セレクタは QSS の対象外（最重要の制約）。

    ``QWidget`` のような広いセレクタは派生クラスである ``QPlainTextEdit``
    にも効いてしまい、実機で動作確認済みのエディタ配色機能を壊す。
    """
    body = _strip_qss_comments(stylesheet_for(theme))
    assert not re.search(rf"\b{selector}\b", body), f"{selector} を QSS の対象にしないこと"


@pytest.mark.parametrize("theme", [THEME_LIGHT, THEME_DARK])
def test_apply_theme_sets_application_stylesheet(qtbot, theme: str) -> None:
    app = QApplication.instance()

    apply_theme(app, theme)

    assert app.styleSheet() == stylesheet_for(theme)


def test_apply_theme_applies_palette_to_open_windows(window: MainWindow) -> None:
    """アプリのパレットだけでなく、各ウィンドウにも直接適用する。

    実機では QApplication のパレットが KDE の統合プラグインに上書き
    されるため、ウィンドウ側に明示的に設定しておく必要がある。
    """
    apply_theme(QApplication.instance(), THEME_DARK)

    expected = palette_for(THEME_DARK).color(QPalette.ColorRole.Window)
    assert window.palette().color(QPalette.ColorRole.Window) == expected


def test_new_window_picks_up_current_theme(window: MainWindow, make_window) -> None:
    """テーマ適用後に作ったウィンドウにもテーマが効いていること。"""
    window.set_theme(THEME_DARK)

    other = make_window()

    expected = palette_for(THEME_DARK).color(QPalette.ColorRole.Window)
    assert other.palette().color(QPalette.ColorRole.Window) == expected


def test_editor_without_custom_colors_follows_theme(window: MainWindow) -> None:
    editor = window.current_editor()
    assert editor.editor_colors() == (None, None)

    window.set_theme(THEME_DARK)

    expected = palette_for(THEME_DARK).color(QPalette.ColorRole.Base)
    assert editor.palette().color(QPalette.ColorRole.Base) == expected


def test_custom_editor_colors_survive_theme_switch(window: MainWindow) -> None:
    """QSS / パレット適用でエディタのカスタム配色が壊れないこと。

    実機ですでに動いている機能なので、テーマ側の変更で絶対に
    壊してはいけない。
    """
    editor = window.current_editor()
    editor.set_editor_colors(QColor("white"), QColor("black"))

    for theme in (THEME_DARK, THEME_LIGHT, THEME_DARK):
        window.set_theme(theme)
        assert editor.palette().color(QPalette.ColorRole.Base) == QColor("white")
        assert editor.palette().color(QPalette.ColorRole.Text) == QColor("black")

    editor.set_editor_colors(None, None)


def test_tab_bar_follows_theme(window: MainWindow) -> None:
    """自前で描画しているタブバーもテーマに追従すること（QSS は効かないため
    ウィンドウ経由のパレット伝播で対応している）。"""
    tab_bar = window.tabs.tabBar()
    assert tab_bar.tab_colors() == (None, None)

    window.set_theme(THEME_DARK)

    expected = palette_for(THEME_DARK).color(QPalette.ColorRole.Base)
    assert tab_bar.tab_background_color(selected=True, hovered=False) == expected


# ----------------------------------------------------------------------
# スクロールバー（実機フィードバック: ダークでつまみが見つけづらい）
# ----------------------------------------------------------------------


@pytest.mark.parametrize("theme", [THEME_LIGHT, THEME_DARK])
def test_stylesheet_styles_the_scroll_bar(theme: str) -> None:
    assert re.search(r"\bQScrollBar\b", _strip_qss_comments(stylesheet_for(theme)))


@pytest.mark.parametrize("theme", [THEME_LIGHT, THEME_DARK])
@pytest.mark.parametrize("subcontrol", REQUIRED_SCROLLBAR_SUBCONTROLS)
def test_scroll_bar_qss_defines_every_subcontrol(theme: str, subcontrol: str) -> None:
    """サブコントロールの部分指定は禁止（指定漏れは「空白のボタン」になる）。

    溝とつまみだけを指定すると、両端の矢印ボタンが「場所は取るのに何も
    描かれない」状態になることを offscreen の描画結果で確認している。
    """
    body = _strip_qss_comments(stylesheet_for(theme))
    assert f"QScrollBar::{subcontrol}" in body, f"{subcontrol} の指定が漏れている"


def test_colors_for_returns_a_copy_that_callers_cannot_corrupt() -> None:
    """色表は**複製して**返す（呼び出し側が書き換えても大元は無事なこと）。

    `colors_for()` の `dict(...)` は、うっかり書き換えられても**アプリ全体の
    配色が壊れない**ようにするためのもの。いまは書き換える呼び出し側が
    無いので外しても動くが、外れていることに誰も気づけない（34 回目に
    変異 `t-colors-defensive-copy` が生き残って発覚）。あとから
    「取ってきた色表をその場で少しいじる」書き方をされたときに、
    静かに全体が壊れるのを防ぐため、契約としてここで固定しておく。
    """
    colors = colors_for(THEME_LIGHT)
    original = colors["window"]
    colors["window"] = "#ff00ff"

    assert colors_for(THEME_LIGHT)["window"] == original


@pytest.mark.parametrize("theme", [THEME_LIGHT, THEME_DARK])
def test_scroll_bar_handle_contrasts_with_its_groove(theme: str) -> None:
    """つまみと溝の明度をはっきり離すこと（これが今回の目的そのもの）。"""
    colors = colors_for(theme)
    groove = QColor(colors["scrollbar_groove"])
    handle = QColor(colors["scrollbar_handle"])

    def luminance(color: QColor) -> float:
        return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()

    # 変更前（Fusion のパレット任せ）は約 14 しか差が無く「見つけづらい」と
    # 報告された。倍以上に広げてあることを固定する。
    assert abs(luminance(groove) - luminance(handle)) >= 40


@pytest.mark.parametrize("theme", [THEME_LIGHT, THEME_DARK])
def test_scroll_bar_renders_only_groove_and_handle(qtbot, theme: str) -> None:
    """実際に描画させて、想定外の色（描き残しの空白ボタン等）が無いか確かめる。

    offscreen では見た目の良し悪しまでは分からないが、「溝の色」と
    「つまみの色」以外がスクロールバーの中央線に現れないことは確認できる。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollBar

    app = QApplication.instance()
    apply_theme(app, theme)

    bar = QScrollBar(Qt.Orientation.Vertical)
    qtbot.addWidget(bar)
    bar.setPalette(palette_for(theme))
    bar.setRange(0, 1000)
    bar.setPageStep(100)
    bar.setValue(400)
    bar.resize(16, 300)
    bar.show()
    app.processEvents()

    image = bar.grab().toImage()
    column = {
        QColor(image.pixel(image.width() // 2, y)).name()
        for y in range(image.height())
    }
    colors = colors_for(theme)
    assert column <= {colors["scrollbar_groove"], colors["scrollbar_handle"]}


@pytest.mark.parametrize("theme", [THEME_LIGHT, THEME_DARK])
def test_apply_theme_sets_palette_on_each_open_window(
    make_window, theme: str
) -> None:
    """テーマ適用が、開いている各ウィンドウへ直接パレットを当てること。

    QSS はメニュー・ボタン等にしか効かず、**自前描画のタブバー
    (``MultiRowTabBar``) は QPalette だけが頼り**。また
    ``QApplication.setPalette()`` は KDE のプラットフォームテーマ統合に
    上書きされうる（この作り直しの発端そのもの）のに対し、ウィジェットへ
    直接設定したパレットは上書きされない。ここが抜けると
    **ダークにしてもタブバーだけライトのまま**という見え方になる。
    """
    window = make_window()
    app = QApplication.instance()

    apply_theme(app, theme)

    expected = palette_for(theme)
    actual = window.palette()
    for role in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.Highlight,
    ):
        assert actual.color(role) == expected.color(role), role
    # タブバーは子ウィジェットなので、親からの伝播で同じ色になる。
    assert window.tabs._bar.palette().color(QPalette.ColorRole.Window) == expected.color(
        QPalette.ColorRole.Window
    )


# ----------------------------------------------------------------------
# 「見張りそのもの」が黙って無効にならないこと（2026-10-11・73 回目に追加）
#
# 上の 2 つの @pytest.mark.parametrize は、どちらも theme.py 側の
# タプルを**件数の素**にしている。タプルを空にすると pytest は
# 「空の parametrize」を**既定で skip** するだけなので、
# `filterwarnings = error` にも引っかからず、**赤 1 件も出ないまま
# 最重要の制約が検査されなくなる**（73 回目に実測：
# EXCLUDED_QSS_SELECTORS を () にしても `35 passed, 2 skipped`）。
# 件数を減らす（1 つだけ外す）場合も同じで、黙って検査が薄くなる。
# ----------------------------------------------------------------------


def test_excluded_selectors_cover_the_editor_widget_class_hierarchy() -> None:
    """本文ウィジェットの**継承の連なり**を 1 つも欠かさず除外していること。

    QSS の型セレクタは**派生クラスにも効く**ため、``EditorWidget`` の基底
    クラスのどれ 1 つでも除外し忘れると、そのセレクタを書いた時点で
    本文の配色（``set_editor_colors()``）が奪われる。

    ここを**継承の連なりから自動で突き合わせている**のが肝心な点。
    固定の一覧を突き合わせるだけだと、``EditorWidget`` の基底クラスを
    （``QTextEdit`` から ``QPlainTextEdit`` へ等）差し替えたときに、
    一覧の方が古びたことに誰も気づけない。

    73 回目にこれで ``QFrame`` の抜けが見つかった（offscreen で実測して、
    ``QWidget`` と同じように本文の背景を塗り替えることを確認済み）。
    """
    from editor_app.editor import EditorWidget

    # QSS のセレクタになりうる Qt の基底クラスだけを見る
    # （QObject・QPaintDevice はウィジェットではないので対象外）。
    targetable = {
        cls.__name__
        for cls in EditorWidget.__mro__
        if cls.__name__.startswith("Q")
    } - {"QObject", "QPaintDevice"}

    assert targetable, "継承の連なりから Qt の型が 1 つも取れていない"
    missing = targetable - set(EXCLUDED_QSS_SELECTORS)
    assert not missing, f"本文の基底クラスが QSS の除外から漏れている: {sorted(missing)}"


def test_excluded_qss_selectors_keeps_every_documented_widget_type() -> None:
    """除外の一覧が**空にも短くもならない**こと（見張りの件数の素）。

    継承の連なりから導けない ``QPlainTextEdit``（いまの ``EditorWidget`` は
    ``QTextEdit`` 派生だが、モジュール冒頭の厳守事項が名指しで禁じている）も
    ここで固定する。
    """
    required = {
        "QWidget",
        "QAbstractScrollArea",
        "QFrame",
        "QPlainTextEdit",
        "QTextEdit",
    }
    missing = required - set(EXCLUDED_QSS_SELECTORS)
    assert not missing, f"QSS の除外から外してはいけない型が消えている: {sorted(missing)}"


def test_required_scrollbar_subcontrols_keeps_every_space_taking_subcontrol() -> None:
    """スクロールバーのサブコントロール一覧が**空にも短くもならない**こと。

    部分指定は禁止（モジュール冒頭の厳守事項）。指定し漏れたサブコントロールは
    「場所は取るのに何も描かれない空白」になる。ここが空や短いままになると、
    上の ``test_scroll_bar_qss_defines_every_subcontrol`` が**黙って
    検査しなくなる**。
    """
    required = {"handle", "add-line", "sub-line", "add-page", "sub-page"}
    missing = required - set(REQUIRED_SCROLLBAR_SUBCONTROLS)
    assert not missing, f"必ず指定するサブコントロールが一覧から消えている: {sorted(missing)}"


# ----------------------------------------------------------------------
# 自前描画のタブバーが頼りにしているパレットのロール（73 回目に追加）
#
# QSS はメニュー・ボタン等にしか効かず、`MultiRowTabBar` は
# `QPalette` **だけ**が頼り。ここが抜けると、プラットフォームテーマの
# 既定色がそのまま出る（ライトを選んでいるのに KDE のダークが透ける、
# というこのモジュールの作り直しの発端そのものの形で出る）。
# ----------------------------------------------------------------------


@pytest.mark.parametrize("theme", [THEME_LIGHT, THEME_DARK])
def test_palette_sets_every_role_the_self_drawn_tab_bar_reads(theme: str) -> None:
    """``MultiRowTabBar`` が読むロールが、すべてテーマの色になっていること。

    参照箇所（``tab_bar.py``）:

    - ``windowText`` … 選択中のタブの文字色
    - ``buttonText`` … 非選択のタブの文字色・閉じるボタンの ✕
    - ``highlight`` … 現在のタブを示す下線・閉じるボタンのホバー円
    - ``highlightedText`` … ホバー中の閉じるボタンの ✕
    """
    palette = palette_for(theme)
    colors = colors_for(theme)

    for role_name, color_key in (
        ("windowText", "window_text"),
        ("buttonText", "button_text"),
        ("highlight", "highlight"),
        ("highlightedText", "highlighted_text"),
    ):
        actual = getattr(palette, role_name)().color()
        assert actual == QColor(colors[color_key]), (
            f"{role_name} がテーマの色になっていない"
            f"（{actual.name()} != {colors[color_key]}）"
        )


@pytest.mark.parametrize("theme", [THEME_LIGHT, THEME_DARK])
def test_tab_bar_text_colors_follow_the_theme(window: MainWindow, theme: str) -> None:
    """実際のタブバーの文字色が、テーマ適用で追従すること（上の実利用側）。"""
    app = QApplication.instance()

    apply_theme(app, theme)

    bar = window.tabs.tabBar()
    colors = colors_for(theme)
    assert bar.tab_text_color(selected=True) == QColor(colors["window_text"])
    assert bar.tab_text_color(selected=False) == QColor(colors["button_text"])


@pytest.mark.parametrize("theme", [THEME_LIGHT, THEME_DARK])
def test_scroll_bar_handle_keeps_a_minimum_grab_size(theme: str) -> None:
    """つまみに**掴める大きさの下限**を与えていること。

    実機フィードバックは「ダークでつまみを見つけづらい」だったので、
    色のはっきり差（``tc-handle-hover`` 等）だけでなく**大きさ**も要る。
    下限が無いと、行数の多いファイルではつまみが数ピクセルの線になり、
    狙って掴めなくなる（色をどれだけ離しても掴めない）。
    """
    body = _strip_qss_comments(stylesheet_for(theme))

    sizes = re.findall(r"min-(?:height|width):\s*(\d+)px", body)
    assert len(sizes) >= 2, "縦・横それぞれのつまみに下限が指定されていない"
    assert all(int(value) >= 20 for value in sizes), (
        f"つまみの下限が小さすぎて掴めない: {sizes}"
    )
