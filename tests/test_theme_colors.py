"""テーマの「色そのもの」を縛るテスト（58 回目・仕上げの見直しで追加）。

``test_theme.py`` は「ライトとダークが違うこと」「QSS が空でないこと」
「エディタ本文を対象にしていないこと」を見ているが、**どちらが明るいのか・
文字が背景から読めるのか・どのウィジェットに色が当たっているのか**は
1 件も縛られていなかった。実際、58 回目に変異を当てて確かめたところ

- ライトテーマのウィンドウ背景を**ダークの色に差し替えても全て緑**
- ダークのツールチップ文字色を**背景と同じ色にしても全て緑**
- ``QMainWindow`` / ``QToolTip`` / ``QLineEdit`` / ``QMenu::item:selected``
  の**配色をまるごと消しても全て緑**

だった。これは `theme.py` の作り直しの発端（KDE のダーク設定が透けて
「ライトを選んでいるのにダークのまま」になる）と同じ形の事故が、
テストを素通りするということ。**「読めない配色」は実機でしか気づけない
のだから、機械で確かめられるところはここで固定しておく。**

色の善し悪しの基準には W3C の相対輝度・コントラスト比（WCAG 2.1）を使う。
「何となく違う」ではなく**外部にある基準**なので、あとから色を調整する
人にも意味が伝わる。
"""

from __future__ import annotations

import re
from contextlib import contextmanager

import pytest
from PySide6.QtWidgets import QApplication

from editor_app.theme import (
    THEME_DARK,
    THEME_LIGHT,
    apply_theme,
    colors_for,
    palette_for,
    stylesheet_for,
)

THEMES = [THEME_LIGHT, THEME_DARK]


@pytest.fixture(autouse=True)
def _restore_light_theme():
    """テーマはアプリ全体の状態なので、各テストの後で必ずライトへ戻す。"""
    yield
    app = QApplication.instance()
    if app is not None:
        apply_theme(app, THEME_LIGHT)


# ----------------------------------------------------------------------
# 色の計算（WCAG 2.1 の相対輝度とコントラスト比）
# ----------------------------------------------------------------------


def _relative_luminance(color: str) -> float:
    """WCAG 2.1 の相対輝度（0.0＝黒 〜 1.0＝白）。"""
    channels = [int(color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(one: str, other: str) -> float:
    """WCAG 2.1 のコントラスト比（1.0＝同じ色 〜 21.0＝黒と白）。"""
    lighter, darker = sorted((_relative_luminance(one), _relative_luminance(other)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _brightness(color: str) -> float:
    """0〜255 の明るさ。既存の `test_theme.py` と同じ数え方。"""
    red, green, blue = (int(color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


# ----------------------------------------------------------------------
# ライトは明るく、ダークは暗く（作り直しの発端そのもの）
# ----------------------------------------------------------------------

#: 背景として使う色。テーマの明暗はこれで決まる。
BACKGROUND_KEYS = (
    "window",
    "base",
    "alternate_base",
    "button",
    "scrollbar_groove",
)

#: 背景の上に載る文字の色。
FOREGROUND_KEYS = ("window_text", "text", "button_text", "tooltip_text")


@pytest.mark.parametrize("key", BACKGROUND_KEYS)
def test_light_theme_backgrounds_are_actually_light(key: str) -> None:
    """ライトテーマの背景が本当に明るいこと。

    `theme.py` が QSS へ作り直された理由は「ライトを選んでいるのに
    KDE のダークが透けて暗いまま」だったこと。**色表そのものが暗く
    なってしまっても誰も気づけない**状態だったので、ここで縛る。
    """
    assert _brightness(colors_for(THEME_LIGHT)[key]) >= 180


@pytest.mark.parametrize("key", BACKGROUND_KEYS)
def test_dark_theme_backgrounds_are_actually_dark(key: str) -> None:
    assert _brightness(colors_for(THEME_DARK)[key]) <= 120


@pytest.mark.parametrize("key", FOREGROUND_KEYS)
def test_light_theme_text_is_dark_and_dark_theme_text_is_light(key: str) -> None:
    """文字色の向きが背景と逆であること（白地に白・黒地に黒を防ぐ）。"""
    assert _brightness(colors_for(THEME_LIGHT)[key]) <= 120
    assert _brightness(colors_for(THEME_DARK)[key]) >= 180


# ----------------------------------------------------------------------
# 文字が背景から読めること
# ----------------------------------------------------------------------

#: (文字の色, その文字が載る背景の色) の組。
TEXT_ON_BACKGROUND = (
    ("text", "base"),
    ("window_text", "window"),
    ("button_text", "button"),
    ("highlighted_text", "highlight"),
    ("tooltip_text", "tooltip_base"),
)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("foreground", "background"), TEXT_ON_BACKGROUND)
def test_text_contrasts_with_the_background_it_sits_on(
    theme: str, foreground: str, background: str
) -> None:
    """文字と背景のコントラスト比を最低限（3.0）確保する。

    3.0 は WCAG 2.1 の「大きめの文字」の基準。いま一番きついのは
    ライトの選択中の文字（白 × #308cc6 で 3.69）なので、**今の配色は
    そのまま通り、同じ色にするような変更だけが落ちる**。
    """
    colors = colors_for(theme)
    ratio = _contrast_ratio(colors[foreground], colors[background])
    assert ratio >= 3.0, f"{theme}: {foreground} が {background} の上で読めない（比 {ratio:.2f}）"


@pytest.mark.parametrize("theme", THEMES)
def test_disabled_text_is_visible_but_clearly_dimmer(theme: str) -> None:
    """「押せない」項目は、読めるが**通常より明らかに淡い**こと。

    通常の文字色と同じにしてしまうと、押せるのかどうかが見て分からない。
    かといって背景に溶かすと、何と書いてあるか読めない。
    """
    colors = colors_for(theme)
    disabled = _contrast_ratio(colors["disabled_text"], colors["window"])
    normal = _contrast_ratio(colors["window_text"], colors["window"])

    assert disabled >= 2.0, f"{theme}: 無効の文字が背景に溶けている（比 {disabled:.2f}）"
    assert disabled <= normal / 2, f"{theme}: 無効の文字が通常と見分けにくい（比 {disabled:.2f}）"


@pytest.mark.parametrize("theme", THEMES)
def test_scroll_bar_handle_states_move_away_from_the_groove(theme: str) -> None:
    """つまみは、触ると（ホバー・押下）**溝からさらに離れる**こと。

    実機フィードバックは「ダークでつまみを見つけづらい」だった。
    ホバー・押下で色が動かない（＝通常と同じ）と、掴めているのかどうかが
    分からない。明るい側・暗い側のどちらへ動かすかはテーマで逆になるので、
    「溝との明るさの差が広がること」という形で縛る。
    """
    colors = colors_for(theme)
    groove = _brightness(colors["scrollbar_groove"])
    handle = abs(_brightness(colors["scrollbar_handle"]) - groove)
    hover = abs(_brightness(colors["scrollbar_handle_hover"]) - groove)
    pressed = abs(_brightness(colors["scrollbar_handle_pressed"]) - groove)

    assert hover >= handle + 15, f"{theme}: ホバーで色がほとんど動かない"
    assert pressed >= hover + 15, f"{theme}: 押下でホバーから色がほとんど動かない"


# ----------------------------------------------------------------------
# QSS が「どのウィジェットに」色を当てているか
# ----------------------------------------------------------------------


def _qss_rules(theme: str) -> dict[str, dict[str, str]]:
    """QSS を {セレクタ: {プロパティ: 値}} に開く。

    ``QPushButton, QToolButton { ... }`` のようにまとめ書きされた規則は、
    セレクタごとに分けて登録する（テスト側で「まとめ方」を気にせずに済む）。
    """
    body = re.sub(r"/\*.*?\*/", "", stylesheet_for(theme), flags=re.DOTALL)
    rules: dict[str, dict[str, str]] = {}
    for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", body):
        parsed: dict[str, str] = {}
        for declaration in declarations.split(";"):
            if ":" in declaration:
                prop, _, value = declaration.partition(":")
                parsed[prop.strip()] = value.strip()
        for selector in selectors.split(","):
            selector = " ".join(selector.split())
            if selector:
                rules.setdefault(selector, {}).update(parsed)
    return rules


#: (セレクタ, 背景に使う色のキー, 文字に使う色のキー)。
#: 「本文以外の枠組み」のうち、**色が抜けると実機でネイティブのテーマが
#: 透けてしまう**ものを並べてある（モジュール冒頭の説明を参照）。
REQUIRED_CHROME_RULES = (
    ("QMainWindow", "window", "window_text"),
    ("QDialog", "window", "window_text"),
    ("QMessageBox", "window", "window_text"),
    ("QMenuBar", "window", "window_text"),
    ("QMenu", "window", "window_text"),
    ("QToolBar", "window", "window_text"),
    ("QStatusBar", "window", "window_text"),
    ("QToolTip", "tooltip_base", "tooltip_text"),
    ("QLineEdit", "base", "text"),
    ("QSpinBox", "base", "text"),
    ("QPushButton", "button", "button_text"),
    ("QToolButton", "button", "button_text"),
    ("QComboBox", "button", "button_text"),
    ("QComboBox QAbstractItemView", "base", "text"),
    # 選択中の項目。ここが抜けると「選んでいる行」が分からなくなる。
    ("QMenu::item:selected", "highlight", "highlighted_text"),
    ("QMenuBar::item:selected", "highlight", "highlighted_text"),
)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("selector", "background", "foreground"), REQUIRED_CHROME_RULES)
def test_chrome_widgets_get_both_a_background_and_a_text_color(
    theme: str, selector: str, background: str, foreground: str
) -> None:
    """枠組みのウィジェットには**背景と文字の両方**をテーマの色で当てる。

    片方だけだと、残りはネイティブのテーマ（実機では KDE）が決めるため、
    「ダークの地に黒い文字」のような読めない組み合わせになりうる。
    これが `theme.py` を QSS へ作り直した理由そのもの。
    """
    colors = colors_for(theme)
    rule = _qss_rules(theme).get(selector)

    assert rule is not None, f"{selector} の指定が QSS に無い"
    assert rule.get("background-color") == colors[background], f"{selector} の背景色"
    assert rule.get("color") == colors[foreground], f"{selector} の文字色"


#: 「押せない」ときの表示。淡い色が当たっていないと、押せるものと見分けが
#: つかない（`QPushButton:disabled` は枠の色も淡くする）。
DISABLED_SELECTORS = (
    "QMenuBar::item:disabled",
    "QMenu::item:disabled",
    "QLabel:disabled",
    "QPushButton:disabled",
    "QToolButton:disabled",
    "QLineEdit:disabled",
    "QComboBox:disabled",
    "QCheckBox:disabled",
    "QRadioButton:disabled",
)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("selector", DISABLED_SELECTORS)
def test_disabled_widgets_use_the_dimmed_color(theme: str, selector: str) -> None:
    rule = _qss_rules(theme).get(selector)

    assert rule is not None, f"{selector} の指定が QSS に無い"
    assert rule.get("color") == colors_for(theme)["disabled_text"]


# ----------------------------------------------------------------------
# パレット側（QSS が効かない要素のための土台）
# ----------------------------------------------------------------------


@pytest.mark.parametrize("theme", THEMES)
def test_palette_dims_text_in_the_disabled_group(theme: str) -> None:
    """パレットの「無効」グループにも淡い色を入れておくこと。

    QSS が効かない要素（自前描画のタブバー等）や、QSS で拾っていない
    ウィジェットは、この色で「押せない」ことを表す。
    """
    from PySide6.QtGui import QPalette

    palette = palette_for(theme)
    expected = colors_for(theme)["disabled_text"]
    disabled = QPalette.ColorGroup.Disabled

    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        assert palette.color(disabled, role).name() == expected, role


@pytest.mark.parametrize("theme", THEMES)
def test_palette_sets_the_placeholder_text_color(theme: str) -> None:
    """入力欄の「薄い案内文字」の色。

    設定しないと Qt の既定（テーマに追従しない灰色）になり、ダークでは
    背景に溶けて何と書いてあるか読めなくなる。
    """
    from PySide6.QtGui import QPalette

    palette = palette_for(theme)
    assert palette.color(QPalette.ColorRole.PlaceholderText).name() == (
        colors_for(theme)["disabled_text"]
    )


# ----------------------------------------------------------------------
# 適用のしかた
# ----------------------------------------------------------------------


@contextmanager
def _spy_on_set_style():
    """``QApplication.setStyle`` の呼び出しを記録する。

    QSS を設定していると ``app.style()`` は ``QStyleSheetStyle`` の
    包み込みになり、**元のスタイル名が取れない**（`theme.py` の
    ``apply_theme`` の注記のとおり）。そのため呼び出しを直接見ている。
    ``setStyle`` は静的メソッドなので、差し替えも元へ戻すのも
    ``staticmethod`` のまま行う（素の関数を入れて戻すと、以後
    ``app.setStyle(...)`` が自分自身を引数に渡してしまう）。
    """
    original = QApplication.__dict__["setStyle"]
    calls: list[tuple[object, ...]] = []
    QApplication.setStyle = staticmethod(lambda *args: calls.append(args))
    try:
        yield calls
    finally:
        QApplication.setStyle = original


@pytest.mark.parametrize("theme", THEMES)
def test_apply_theme_requests_the_fusion_style(qtbot, theme: str) -> None:
    """必ず ``Fusion`` にすること。

    プラットフォーム独自のスタイル（実機では KDE の Breeze）は自前で
    描画する部分が多く、こちらのパレット・QSS が素通りする。テーマの
    切り替えが効かなくなるので、毎回 ``Fusion`` を指定し直している。
    """
    app = QApplication.instance()

    with _spy_on_set_style() as calls:
        apply_theme(app, theme)

    assert calls == [("Fusion",)]
