"""設定を訊く「小窓」の**呼び出し側**のテスト（2026-09-20（64 回目））。

小窓そのもの（:func:`editor_app.dialogs.prompt_int` /
:func:`editor_app.dialogs.prompt_font`）は `tests/test_dialogs.py` が見ている
——キャンセルを ``None`` に均すか、範囲を渡すか、ピクセル指定のフォントで
大きさを取り落とさないか。

ここで見るのは、その**手前**にある 4 つの呼び出し側:

* 折り返し文字数（表示 → 指定文字数で折り返す）
* タブの幅（表示 → タブの幅 → 固定幅を指定）
* 本文フォント（表示 → フォント）
* 検索欄・置換欄の高さ（編集 → 検索欄の高さ）

見張るのは 3 つだけで、どれも**画面には何も出ない**:

1. **いくつから始めるか**（初期値）… 今の設定から始まらないと、
   「少しだけ広げる」のような調整のたびに入れ直しになる。
2. **いくつまで許すか**（範囲）… 下限が外れると 1px のタブ（名前も ×
   ボタンも出ない）や 0 行の検索欄が作れてしまう。上限が外れると、
   設定した本人にも直せない形で画面が埋まる。
3. **やめたときの後始末**… メニューの「指定文字数で折り返す」「固定幅を
   指定」はチェック付きなので、押した時点で Qt がチェックを付ける。
   キャンセルしたらこちらで**実際の設定へ戻さないと**、画面の表示と
   実際の動きが食い違ったままになる。

**2026-09-20（64 回目）まで、1〜3 はどのテストも見ていなかった**
（既存のテストは「入力した値が効くか」「やめたら変わらないか」までで、
小窓へ渡す引数は素通りしていた）。
"""

from __future__ import annotations

from PySide6.QtGui import QFont

from editor_app.editor import DEFAULT_FONT_FAMILY, WRAP_FIXED, WRAP_NONE
from editor_app.main_window import MainWindow
from editor_app.tab_bar import MAX_TAB_WIDTH, MIN_TAB_WIDTH


# ----------------------------------------------------------------------
# 補助：小窓へ渡された引数を記録するだけの偽物
# ----------------------------------------------------------------------
class IntPrompt:
    """``QInputDialog.getInt`` の代わり。渡された引数を覚えて答えを返す。"""

    def __init__(self, answer: int | None) -> None:
        self.answer = answer
        self.calls: list[dict[str, int]] = []

    def install(self, monkeypatch) -> "IntPrompt":
        def fake(parent, title, label, value, minimum, maximum, step):
            self.calls.append(
                {
                    "value": value,
                    "minimum": minimum,
                    "maximum": maximum,
                    "step": step,
                }
            )
            return (self.answer if self.answer is not None else 0, self.answer is not None)

        monkeypatch.setattr("editor_app.dialogs.QInputDialog.getInt", staticmethod(fake))
        return self

    @property
    def only(self) -> dict[str, int]:
        assert len(self.calls) == 1, f"小窓は 1 回だけ開くこと（{len(self.calls)} 回開いた）"
        return self.calls[0]


class FontPrompt:
    """``QFontDialog.getFont`` の代わり。最初に見せるフォントを覚える。"""

    def __init__(self, answer: QFont | None) -> None:
        self.answer = answer
        self.initial: list[QFont] = []

    def install(self, monkeypatch) -> "FontPrompt":
        def fake(initial, parent, title):
            self.initial.append(QFont(initial))
            return (self.answer is not None, self.answer or initial)

        monkeypatch.setattr("editor_app.dialogs.QFontDialog.getFont", staticmethod(fake))
        return self

    @property
    def only(self) -> QFont:
        assert len(self.initial) == 1, "小窓は 1 回だけ開くこと"
        return self.initial[0]


# ----------------------------------------------------------------------
# 折り返し文字数（表示 → 指定文字数で折り返す）
# ----------------------------------------------------------------------
def test_wrap_column_prompt_starts_at_the_current_column(
    window: MainWindow, monkeypatch
) -> None:
    """今の折り返し文字数から始める（毎回入れ直しにしない）。"""
    window.set_wrap_mode(WRAP_FIXED, column=72)
    prompt = IntPrompt(80).install(monkeypatch)

    window._prompt_wrap_fixed_column()

    assert prompt.only["value"] == 72


def test_wrap_column_prompt_refuses_zero_and_absurd_columns(
    window: MainWindow, monkeypatch
) -> None:
    """折り返し文字数は 1〜1000 の範囲でしか訊かない。

    下限が 0 になると「0 文字で折り返す」が指定でき、上限が桁違いに
    広がると「折り返さないのと同じ」値を折り返しモードとして設定できる。
    どちらも本文が読めなくなるうえ、画面からは直し方が分からない。
    """
    prompt = IntPrompt(40).install(monkeypatch)

    window._prompt_wrap_fixed_column()

    assert prompt.only["minimum"] == 1
    assert prompt.only["maximum"] == 1000


def test_cancelling_the_wrap_column_restores_the_menu_check(
    window: MainWindow, monkeypatch
) -> None:
    """やめたら、メニューのチェックを実際の折り返しモードへ戻す。

    「指定文字数で折り返す」はチェック付きの項目なので、**押した時点で
    Qt がチェックを付ける**（ここでは ``trigger()`` がその代わり）。
    やめたときに戻さないと、本文は折り返していないのにメニューだけ
    「指定文字数で折り返す」に見える状態が残る。
    """
    window.set_wrap_mode(WRAP_NONE)
    IntPrompt(None).install(monkeypatch)

    window.action_wrap_fixed.trigger()

    assert window.action_wrap_none.isChecked()
    assert not window.action_wrap_fixed.isChecked()


# ----------------------------------------------------------------------
# タブの幅（表示 → タブの幅 → 固定幅を指定）
# ----------------------------------------------------------------------
def test_tab_width_prompt_starts_at_the_width_on_screen(
    window: MainWindow, monkeypatch
) -> None:
    """自動幅から切り替えるときは、いま表示されているタブ幅から始める。"""
    bar = window.tabs.tabBar()
    on_screen = bar.tabRect(window.tabs.currentIndex()).width()
    assert on_screen > 0, "前提：タブが 1 枚は表示されていること"
    prompt = IntPrompt(on_screen).install(monkeypatch)

    window._prompt_fixed_tab_width()

    assert prompt.only["value"] == on_screen


# タブが 1 枚も無いときの初期値（`rect.width()` が 0 なので
# ``MIN_TAB_WIDTH`` に差し替えている行）は、**わざと見張っていない**。
# 64 回目に実測したところ、``QInputDialog.getInt()`` は範囲外の初期値を
# 下限へ丸めるので、0 を渡しても欄に入るのは 56（``MIN_TAB_WIDTH``）で、
# 返り値も 56 だった——つまり利用者からは区別が付かない。
# 区別の付かないものをテストで固定すると、後で直す自由だけが減る。


def test_tab_width_prompt_range_keeps_the_tab_usable(
    window: MainWindow, monkeypatch
) -> None:
    """タブ幅は「押せる下限」から「自動幅の 4 倍」までで訊く。

    下限が外れると、名前も × ボタンも出ない 1px のタブが作れてしまう。
    上限を自動幅の上限（``MAX_TAB_WIDTH``）まで狭めると、長いファイル名を
    そのまま読むために広げる、という使い方ができなくなる（固定幅は
    自動幅と違って利用者が明示的に決めるものなので、広い側は許す）。
    """
    prompt = IntPrompt(160).install(monkeypatch)

    window._prompt_fixed_tab_width()

    assert prompt.only["minimum"] == MIN_TAB_WIDTH
    assert prompt.only["maximum"] == MAX_TAB_WIDTH * 4


def test_cancelling_the_tab_width_restores_the_menu_check(
    window: MainWindow, monkeypatch
) -> None:
    """やめたら、メニューのチェックを「自動」へ戻す。"""
    IntPrompt(None).install(monkeypatch)

    window.action_tab_width_fixed.trigger()

    assert window.fixed_tab_width() is None
    assert window.action_tab_width_auto.isChecked()
    assert not window.action_tab_width_fixed.isChecked()


# ----------------------------------------------------------------------
# 本文フォント（表示 → フォント）
# ----------------------------------------------------------------------
def test_font_prompt_starts_at_the_current_font(window: MainWindow, monkeypatch) -> None:
    """今の書体と大きさから始める（選び直しがやり直しにならない）。"""
    window.set_font_settings("Courier", 17)
    prompt = FontPrompt(QFont("Courier", 17)).install(monkeypatch)

    window._prompt_font()

    assert prompt.only.family() == "Courier"
    assert prompt.only.pointSize() == 17


def test_font_prompt_hints_monospace_only_for_the_default_family(
    window: MainWindow, monkeypatch
) -> None:
    """等幅のヒントは、既定の書体のときだけ渡す。

    既定の ``monospace`` は「実体のある書体名」ではないので、ヒントが
    無いとダイアログが似ても似つかない書体を選んだ状態で開く。
    逆に、利用者が自分で選んだ書体のときにヒントを立てると、
    **選んだはずの書体から始まらない**（等幅の代役へすり替わる）。
    """
    window.set_font_settings(DEFAULT_FONT_FAMILY, 12)
    default_prompt = FontPrompt(None).install(monkeypatch)
    window._prompt_font()
    assert default_prompt.only.styleHint() == QFont.StyleHint.Monospace

    window.set_font_settings("Serif", 12)
    chosen_prompt = FontPrompt(None).install(monkeypatch)
    window._prompt_font()
    assert chosen_prompt.only.styleHint() != QFont.StyleHint.Monospace


# ----------------------------------------------------------------------
# 検索欄・置換欄の高さ（編集 → 検索欄の高さ）
# ----------------------------------------------------------------------
def test_search_input_lines_prompt_starts_at_the_current_height(
    window: MainWindow, monkeypatch
) -> None:
    """今の表示行数から始める。"""
    window.set_search_input_lines(6)
    prompt = IntPrompt(8).install(monkeypatch)

    window._prompt_search_input_lines()

    assert prompt.only["value"] == 6


def test_search_input_lines_prompt_keeps_the_panel_usable(
    window: MainWindow, monkeypatch
) -> None:
    """検索欄の行数は 1〜30 の範囲でしか訊かない。

    0 行にすると打った検索語が見えなくなり、桁違いに大きくすると
    検索欄がパネルを埋めて**結果一覧が画面から押し出される**
    （機能 6 は結果一覧を見るための窓なので、そのまま使えなくなる）。
    """
    prompt = IntPrompt(10).install(monkeypatch)

    window._prompt_search_input_lines()

    assert prompt.only["minimum"] == 1
    assert prompt.only["maximum"] == 30
