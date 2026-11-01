"""「真偽値の直書き」を取り違えても誰も気づけない所を塞ぐ（83 回目）。

78〜80 回目の数え上げ（モジュール直下の数値定数 → メソッドの中の数値 →
文字列リテラル）の続き。80 回目の申し送りが残した 2 つのうち、**真偽値の
直書き**を一巡させた。

``src/editor_app/`` の中の ``True`` / ``False`` を構文木で数えると **191 件**
（引数の既定 8・呼び出しに渡す値 89・代入 47・``return`` 47）。このうち
``tools/mutation_check.py`` に登録が無い **75 件**を 1 件ずつ裏返して実測し、
**44 件は既存のテストが捕まえた**。残った 31 通りを全件（2272 件）で切り分け、
**本物の穴だったものだけ**をここで塞ぐ。

**生き残ったものの大半は「同じ動きにしかならない変異」だった**。真偽値には
数値や文字列と違う傾向があって、

- **引数の既定が、どの呼び出し元からも使われていない**（``monospace_hint``・
  ``chosen``・``canceled``・``truncated``）。呼ぶ側が必ず明示的に渡すので、
  既定を裏返しても到達しない。
- **押した時点で上書きされる状態の旗**（``tab_bar.py`` のマウス状態）。
  ``__init__`` やリセットでの初期値は、``mousePressEvent`` が必ず入れ直す。
- **見た目にしか出ず、offscreen では差が出ない**（``setRenderHint``・
  ``setLeadingIncluded``。後者はこの環境のフォントの ``leading`` が
  すべて 0 なので、含めても含めなくても行の高さが変わらない）。

**どれも「既定を縛るテスト」を足す意味は薄い**（壊しても利用者に届かない）。
一巡ぶんの内訳と、1 件ずつの理由は ``PROGRESS.md`` に残してある。
"""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel

from editor_app import dialogs
from editor_app.main_window import MainWindow
from editor_app.tab_bar import MultiRowTabBar


# ----------------------------------------------------------------------
# dialogs.py: ProgressReporter の setAutoClose / setAutoReset
# ----------------------------------------------------------------------
def test_progress_window_survives_being_advanced_to_the_last_item(
    window: MainWindow,
) -> None:
    """最後の 1 件まで進めても、窓は消えず・進み具合も戻らないこと。

    ``QProgressDialog`` の既定は「最大値に達したら自分で閉じて 0 に戻す」
    (``autoClose`` / ``autoReset``)。全置換（引き継ぎ ⑨）はこの窓を
    **自分で開いて自分で閉じる**前提で書かれているので、どちらも切って
    ある。切り忘れると、``advance()`` が最後の一塊を渡した瞬間に
    **窓が勝手に消えて、進み具合が -1 に戻る**（実測）。利用者には
    「置換がまだ続いているのに窓が消え、次の一塊でまた出てくる」
    ちらつきとして見え、**何件まで進んだのかも分からなくなる**。

    既存の ``test_real_progress_reporter_advances_and_closes`` は
    **10 件のうち 5 件までしか進めていない**ので、ここだけは誰も
    通っていなかった（全 2272 件が緑のまま）。
    """
    total = 10
    progress = dialogs.ProgressReporter(
        window, title="全置換", label=f"{total} 件を置換しています...", total=total
    )
    try:
        # 途中の一塊。ここで Qt に制御が戻り、窓が実際に出る
        # （``minimumDuration`` を 0 にしてあるので待たされない）。
        assert progress.advance(total // 2) is True
        assert progress._dialog.isVisible() is True
        assert progress._dialog.value() == total // 2

        # 最後の一塊。**ここが誰も通っていなかった所。**
        assert progress.advance(total) is True

        assert progress._dialog.isVisible() is True
        assert progress._dialog.value() == total
        assert progress.was_canceled() is False
    finally:
        progress.close()


# ----------------------------------------------------------------------
# dialogs.py: ExternalChangeDialog の説明文の setWordWrap
# ----------------------------------------------------------------------
def test_external_change_dialog_explanation_wraps(window: MainWindow, qtbot) -> None:
    """差分ダイアログの説明文が折り返されること。

    折り返さないと、**説明文 1 行の幅がそのままダイアログの下限**になる
    （実測で最小幅が 471px → 653px）。差分そのものは等幅・折り返し無しで
    見せる約束があるので（``test_dialogs.py`` の
    ``test_external_change_dialog_shows_the_diff_monospaced_and_unwrapped``）、
    **窓を狭くして横に並べる**余地は説明文側にしか無い。
    """
    dialog = dialogs.ExternalChangeDialog(
        window, name="さくら.txt", diff_text="-古い行\n+新しい行"
    )
    qtbot.addWidget(dialog)

    explanation = dialog.findChildren(QLabel)[0]
    assert "外部で変更されています" in explanation.text()
    assert explanation.wordWrap() is True


# ----------------------------------------------------------------------
# tab_bar.py: ドラッグ中にカーソルへ付いてくる絵（_drag_pixmap）
# ----------------------------------------------------------------------
def test_the_dragged_tab_picture_uses_the_active_tab_color(qtbot) -> None:
    """つかんだタブの絵は、**選択中の色**で描くこと。

    ドラッグ中の絵は「いまつかんでいるもの」の分身なので、選択中の
    見た目で描く（つかめるのは選択中のタブだけではないため、
    ``selected=True`` を直に渡している）。非選択の色で描くと、
    **タブの配色をはっきり分けている人**（実機フィードバックで足した
    機能）には、絵がどのタブのものか分からなくなる。
    """
    active, inactive = QColor("#ff0000"), QColor("#0000ff")
    bar = MultiRowTabBar()
    qtbot.addWidget(bar)
    bar.addTab("あ.txt")
    bar.addTab("い.txt")
    bar.setCurrentIndex(0)
    bar.set_tab_colors(active, inactive)
    bar.resize(400, 60)
    bar.show()

    # いま選択していない 2 枚目をつかむ（選択中の 1 枚目と区別するため）。
    pixmap = bar._drag_pixmap(1)
    assert pixmap is not None
    image = pixmap.toImage()
    # 見るのは「絵の中でいちばん多い色」＝タブの背景色。**真ん中の 1 点を
    # 見てはいけない**: タブ名の文字がそこに掛かると文字の色を拾う。
    # 公開リポの CI に日本語フォントを入れた途端（2026-11-01）、「い」の字が
    # 真ん中に描かれるようになってここが落ちた（それまでは字が豆腐で細く、
    # たまたま外れていた）。
    counts: dict[int, int] = {}
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixel(x, y)
            counts[pixel] = counts.get(pixel, 0) + 1
    background = QColor(max(counts, key=counts.get))

    assert background == active
    assert background != inactive
