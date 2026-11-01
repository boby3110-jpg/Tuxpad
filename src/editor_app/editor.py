"""1 タブぶんのテキスト編集ウィジェット."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPalette,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
)
from PySide6.QtWidgets import QPlainTextDocumentLayout, QPlainTextEdit, QTextEdit

from .fileio import file_signature
from .tab_bar import TAB_MIME_TYPE

DEFAULT_ENCODING = "utf-8"
DEFAULT_NEWLINE = "\n"

#: 折り返しなし。
WRAP_NONE = "none"
#: ウィンドウ幅で折り返す。
WRAP_WINDOW = "window"
#: 指定した文字数で折り返す。
WRAP_FIXED = "fixed"

#: 既定の折り返しモード（今までの「折り返さない」を維持）。
DEFAULT_WRAP_MODE = WRAP_NONE
#: 「指定文字数で折り返す」の既定の文字数。
DEFAULT_WRAP_COLUMN = 80

#: 既定のフォントファミリー。"monospace" は個別のフォント名ではなく、
#: システムの等幅フォントに解決される総称名。
DEFAULT_FONT_FAMILY = "monospace"
#: 既定のフォントサイズ (pt)。
DEFAULT_FONT_SIZE = 11
#: 本文のタブ幅（半角スペース何個ぶんか）。フォントを変えるたびに
#: この文字数ぶんのピクセル数を計算し直す。
TAB_STOP_SPACES = 4

#: マウスホイール 1 ノッチで本文を何行スクロールするか（既定値）。
#: Qt の既定はデスクトップの設定（KDE は 3 行）に従うが、利用者の要望で
#: **1 行ずつ**にした（2026-10-26 実機フィードバック。⑯ で本文欄を
#: ``QPlainTextEdit`` へ戻したら 3 行ずつ動いて使いにくい、との報告）。
#: 同日、「表示 → ホイールのスクロール量...」で変えられるようにした。
WHEEL_SCROLL_LINES = 1

#: ホイールのスクロール量として設定できる上限（行）。
MAX_WHEEL_SCROLL_LINES = 20

#: ホイール 1 ノッチぶんの回転量（Qt の ``angleDelta`` で 1/8 度単位）。
#: タッチパッドなどは 1 ノッチより細かく届くので、貯めて 1 行ずつ進める。
WHEEL_NOTCH_DELTA = 120

#: 改行記号（EmEditor 等にある「行末に薄い記号を表示する」機能）の既定値。
#: 実機フィードバックにより追加。
DEFAULT_SHOW_LINE_BREAKS = False

#: 行末に描く改行記号。EmEditor に合わせて下向き矢印にする。
LINE_BREAK_MARK = "↓"

#: 改行記号の色。「薄っすら見えるくらい」という要望なので、薄い青に
#: アルファを効かせて本文よりずっと目立たない濃さにする。
LINE_BREAK_MARK_COLOR = QColor(90, 140, 220, 120)

#: 改行記号を行末の文字からどれだけ離して描くか (px)。
_LINE_BREAK_MARK_GAP = 1

#: 検索でヒットした箇所（現在地点以外）のハイライト色。
_MATCH_HIGHLIGHT_COLOR = QColor(180, 230, 180)

#: 検索で現在指している箇所のハイライト色（他より目立たせる）。
_CURRENT_MATCH_HIGHLIGHT_COLOR = QColor(255, 165, 0)

#: アプリ全体のメニュー操作として使っているキー。X11/KDE には「Ctrl+H = 1文字削除」
#: 「Ctrl+W = 1単語削除」のような Emacs 風のテキスト編集キーバインドがあり、
#: QTextEdit (系のテキスト編集ウィジェット) はこれらを ShortcutOverride イベントで自分の編集操作として
#: 先取りしてしまう。そのため何もしないと、これらのキーがフォーカスされた
#: エディタに食われてしまい、メインウィンドウの QAction ショートカット
#: （検索・置換・タブを閉じる等）が一切発火しなくなる。ここに挙げたキーは
#: 明示的にショートカットへ譲る。
_RESERVED_APP_SHORTCUTS: frozenset[tuple[Qt.Key, Qt.KeyboardModifier]] = frozenset(
    {
        (Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier),
        (Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_F4, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier),
        # タブ切り替え（実機フィードバックにより追加）。Tab キーは Qt の
        # フォーカス移動・タブ文字入力に先取りされるので、ここで必ず譲る。
        # Shift を伴う場合、環境によってキーが Key_Backtab で届くため両方書く。
        (Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier),
        (
            Qt.Key.Key_Backtab,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        ),
        (Qt.Key.Key_Backtab, Qt.KeyboardModifier.ControlModifier),
    }
)

#: Qt が文書の中で段落の区切り (= 改行) に使う文字。
_PARAGRAPH_SEPARATOR = "\u2029"

#: Qt が文書の中で「段落の中の行の区切り」に使う文字。**Shift+Enter を
#: 押すとこれが入る**（QTextEdit の既定の動作）。
_LINE_SEPARATOR = "\u2028"


def document_text(widget) -> str:
    """本文を取り出す。**``toPlainText()`` を使ってはいけない。**

    ``QTextDocument.toPlainText()`` は、文書に入っている文字をそのまま
    返さない。**改行しない空白 (U+00A0, NBSP) を普通の半角空白へ黙って
    置き換える**（Qt 側の仕様）。そのため「開いて、何も触らずに保存する」
    だけで、本文中の NBSP が全て普通の空白に変わってしまう。**NBSP と
    空白は画面上で見分けが付かないので、利用者は一生気づけない** ——
    ウェブや Word から貼り付けた日本語の文書にはよく紛れ込む文字で、
    利用者の最優先事項（ファイルを勝手に変えない）に真っ向から反する。

    そこで ``toRawText()``（置き換えをしない生の文字列）から取り、
    **改行として扱うべき 2 文字だけ**を ``\\n`` へ直す:

    - ``U+2029`` … Qt が段落の区切りに使う文字。読み込んだ ``\\n`` は
      全てこれになる。
    - ``U+2028`` … 段落の中の行区切り。**Shift+Enter で入る**ため、
      ここを残すと「利用者は改行したつもりなのに、ファイルには目に見えない
      文字が 1 つ書かれる」ことになる（Shift-JIS では保存すらできなくなる）。
      画面上も改行に見えているので、``\\n`` として書き出すのが素直。

    置き換えは 1 文字 → 1 文字なので、``toPlainText()`` と**文字数も位置も
    完全に同じ**まま。文字位置を Qt の文書位置へ直す既存の計算
    (:mod:`editor_app.textsearch`) はそのまま使える。

    なお **NBSP を落とすかどうかは保存の側で決める**
    （:func:`~editor_app.fileio.downgrade_no_break_spaces`）。ここは
    「文書に入っている文字をそのまま渡す」に徹する——落とすかどうかは
    **保存先の文字コード次第**であり、本文の取り出しには判断材料が無いため。

    この約束（`src/` の中で ``toPlainText()`` を呼ばない・``toRawText()`` は
    ここだけ）は `tests/test_document_text_only.py` が構文木で見張っている。
    """
    raw = widget.document().toRawText()
    return raw.replace(_PARAGRAPH_SEPARATOR, "\n").replace(_LINE_SEPARATOR, "\n")


#: :class:`_EditorDocumentLayout` が「自分が組んだ」印として覚えておく
#: ブロックの数の上限。見えているぶん（40 個ほど）しか増えないが、本文を
#: 変えずに長いファイルを延々とスクロールされると溜まり続けるので頭を
#: 押さえる。超えたら全部捨てる——捨てても、見えているブロックをもう一度
#: 組み直すだけで、表示は何も変わらない。
_MAX_REMEMBERED_BLOCK_LAYOUTS = 4096


class _EditorDocumentLayout(QPlainTextDocumentLayout):
    """本文の文書レイアウト。**「指定文字数で折り返す」だけ**を自前で組む。

    ``QPlainTextEdit`` の折り返しは「折り返さない」「ウィンドウ幅」の
    2 つしか無く、``QTextEdit`` にあった「指定文字数」(``FixedColumnWidth``)
    が無い。そこを埋めるためのクラス。

    **``QTextEdit`` の「指定文字数」が実際にやっていたこと**（2026-10-25、
    81 回目の次の回に offscreen で実測して確かめた。引き継ぎ ⑯ の節に
    あった「フォントのどの幅 × N か」という見立ては**誤り**だった）:

    - 折り返し位置は**ピクセル幅ではなく文字数そのもの**で決まる
      （Qt の内部で ``QTextLine.setNumColumns(N, 使える幅)`` を呼んでいる）。
      全角でも半角でも **1 文字は 1 文字**なので、「あ」が 15px・「a」が
      9px のフォントでも、1 行には必ず N 文字が載る。
    - ``QTextEdit`` は**使える幅（ビューポートの幅）で頭打ち**にしていた。
      N 文字がウィンドウに入り切らないときは、入るところまでで折り返す。

    **2026-10-26、利用者の判断で頭打ちをやめた**（「指定文字数」と言う以上、
    ウィンドウが狭くても N 文字で折り返し、はみ出す分は横スクロールで
    見るのが正しい挙動、とのこと。EmEditor と同じ形）。そのため
    ``setNumColumns(N)``（幅の上限なし）で組み、文書の幅
    (:meth:`documentSize`) を「N 文字ぶん」として Qt に伝える。ウィンドウが
    それより狭ければ、``QPlainTextEdit`` が横スクロールバーを出す。
    ``N`` が 0 のとき（折り返しなし・ウィンドウ幅）は**何もせず基底
    クラスにそのまま任せる**ので、その 2 つの振る舞いは素の
    ``QPlainTextEdit`` と 1 つも変わらない。

    組み直しが要るときの印は ``QTextLayout.lineCount() == 0``。本文が
    変わったとき・ビューポートの幅が変わったときは**基底クラスが**
    ブロックの組みを捨ててくれる（``QPlainTextEdit`` が幅を渡し直す
    経路）ので、こちらは「捨てられていたら組み直す」だけでよい。
    文字数そのものを変えたときだけは誰も捨ててくれないので、
    :meth:`set_fixed_columns` が自分で捨てる。
    """

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        #: 0 なら「文字数での折り返し」をしない（基底クラスに任せる）。
        self._columns = 0
        #: 自分が組んだブロックの「視覚行の数」。
        #: ``{ブロック番号: 視覚行の数}``。:meth:`_needs_column_layout` が
        #: 「いまの組みが自分のものか」を見分けるのに使う。文書が変わると
        #: 捨てる（:meth:`documentChanged`）。
        self._laid_out: dict[int, int] = {}

    def fixed_columns(self) -> int:
        """文字数で折り返しているときの文字数。0 なら文字数では折り返さない。"""
        return self._columns

    def set_fixed_columns(self, columns: int) -> None:
        """文字数での折り返しを設定する。0 で解除。"""
        columns = max(0, int(columns))
        if columns == self._columns:
            return
        self._columns = columns
        # 文字数が変わると、既に組んである行は全部間違いになる。基底
        # クラスが「幅が変わったとき」に通るのと同じ道で捨てさせる。
        document = self.document()
        self.documentChanged(0, 0, document.characterCount())
        self.documentSizeChanged.emit(self.documentSize())

    def blockBoundingRect(self, block):  # noqa: N802 - Qt の命名規則
        """ブロック 1 つぶんの矩形。**ここが折り返しの入口**。

        ``QPlainTextEdit`` は、描画も当たり判定もスクロール量の計算も
        すべてこのメソッドを通して行う（Qt の実装でそうなっている）。
        基底クラスは「まだ組まれていなければ組む」ので、**その前に
        自分で組んでしまえば**、折り返し方だけを差し替えられる。
        """
        if self._columns <= 0 or not block.isValid():
            return super().blockBoundingRect(block)
        if block.isVisible() and self._needs_column_layout(block):
            self._layout_block_in_columns(block)
        return super().blockBoundingRect(block)

    def _needs_column_layout(self, block) -> bool:
        """このブロックを「N 文字ごと」に組み直す必要があるか。

        **「組みが捨てられているか」(``lineCount() == 0``) だけでは足りない。**
        基底クラスは、**捨てずにその場で組み直す**道も持っている——
        そのとき組まれるのは当然「ウィンドウ幅で」なので、こちらの
        組み方が**黙って上書きされる**。2026-10-25（82 回目のもう 1 本）に
        実測した、上書きされる場面:

        - **本文を 1 文字打つ**… 打った行（ブロック）だけがウィンドウ幅で
          組み直され、**その行だけ折り返しが伸びる**（他の行は正しいまま
          なので、編集中の行が急に伸びて見える）。
        - **折り返す文字数を変える**… 既に組んである行が残る
          （``set_fixed_columns`` が ``documentChanged`` を通しても、
          基底クラスはこの組みを捨てない）。
        - **フォントを変える**… 同じく、組み直しがウィンドウ幅で行われる。

        どれも「ビューポートの幅が変わる」と自然に直っていた（基底クラスが
        そこでは確かに捨てるため）ので、**窓の大きさを変えるまで崩れたまま**
        という出方をしていた。

        そこで「自分が、いまの条件で組んだ組みか」を**ブロックごとに
        覚えておいて**突き合わせる。覚える先は ``QTextBlock`` の
        ``userState``（Qt が構文ハイライト用に用意している、1 ブロック 1 個の
        整数の置き場）。**このアプリは構文ハイライトを持たない**ので
        空いている（「シンプルなエディタ」が目標でハイライトは対象外、
        README 参照）。もしハイライトを足すなら、ここと取り合いになる。

        **文書全体を舐めて捨てて回る形にはしないこと。** 実測では
        20 万行のうち実際に組まれているのは **37 個**（見えているぶん）
        だけなのに、全ブロックを舐めるだけで **380ms** かかる——引き継ぎ
        ⑯ で消したはずの「ファイルの大きさに比例して止まる」が戻ってくる。
        ここが**見えているブロックしか触らない**作りになっているのは
        そのため。
        """
        layout = block.layout()
        if layout.lineCount() == 0:
            return True
        # 自分が組んだときの視覚行の数と合わなければ、**誰かに組み直された**。
        # 基底クラスの組み方はウィンドウ幅なので、折り返しの数が必ず変わる
        # （たまたま同じ行数になる場合は、どちらで組んでも折り返し位置が
        # 同じなので、合っていてよい）。
        return self._laid_out.get(block.blockNumber()) != layout.lineCount()

    def documentChanged(self, from_position, chars_removed, chars_added):  # noqa: N802
        """文書が変わったことの受け口。**控えを捨てる**のが肝心。

        基底クラスはここで、変わったブロックを**その場で組み直す**
        （当然ウィンドウ幅で）。番号のずれた控えを残すと、別のブロックの
        行数とたまたま一致して「自分が組んだ」と誤認しうるので、
        文書が変わったら全部捨てる。

        **ここでは文書を舐めない。** 組み直すのは、このあと実際に
        ``blockBoundingRect()`` を通るブロック、つまり**画面に見えている
        40 個ほど**だけ。20 万行の文書でも、全ブロックを舐めれば
        それだけで 380ms かかる（実測）——引き継ぎ ⑯ で消したはずの
        「大きさに比例して止まる」を戻さないために、ここは必ず
        「捨てるだけ」に留めること。
        """
        self._laid_out.clear()
        super().documentChanged(from_position, chars_removed, chars_added)

    def column_width(self) -> float:
        """N 文字ぶんの文書の幅 (px)。両脇の文書マージンを含む。

        **ウィンドウの幅にもファイルの中身にも左右されない値**にしてある
        （フォントと文字数だけで決まる）。82 回目に、組んだ行の幅の最大値を
        覚えて文書の幅にしたところ、**一度広がった幅が縮まず**、広い窓で
        開いてから狭めると要らない横スクロールバーが出た（PROGRESS.md 参照）。
        ここでは中身を見ないので、その形の取り残しは起きない。

        幅は「全角 1 文字（"あ"）の幅 × N」。全角も半角も 1 文字は 1 文字で
        折り返すので、1 行がいちばん広くなるのは全角だけの行。
        """
        document = self.document()
        metrics = QFontMetricsF(document.defaultFont())
        return self._columns * metrics.horizontalAdvance("あ") + 2 * document.documentMargin()

    def documentSize(self):  # noqa: N802 - Qt の命名規則
        """文書の大きさ。文字数で折り返しているときは、幅を N 文字ぶんにする。

        ``QPlainTextEdit`` は、この幅がビューポートより広いと横スクロール
        バーを出す。N 文字ぶんより窓が広ければ（ふつうの使い方）幅は
        ビューポート以下なので、横スクロールバーは出ない。
        """
        size = super().documentSize()
        if self._columns > 0:
            size.setWidth(self.column_width())
        return size

    def _layout_block_in_columns(self, block) -> None:
        """ブロックを「N 文字ごと」に組む（``QTextEdit`` と同じ規則）。"""
        document = self.document()
        margin = document.documentMargin()
        text_layout = block.layout()

        # 折り返しの「切れ目の選び方」。**必ず自分で入れること**——基底
        # クラスの組み方 (``layoutBlock``) を通らないので、何もしないと
        # 文書の既定ではなく ``QTextLayout`` の初期値（単語の切れ目でしか
        # 切らない ``WordWrap``）のまま組まれ、英文が N 文字より手前で
        # 折り返してしまう。``QTextEdit`` の ``FixedColumnWidth`` は
        # ``WrapAtWordBoundaryOrAnywhere`` だったので、それに合わせる。
        option = QTextOption(document.defaultTextOption())
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        text_layout.setTextOption(option)

        height = 0.0
        text_layout.beginLayout()
        while True:
            line = text_layout.createLine()
            if not line.isValid():
                break
            line.setLeadingIncluded(True)
            # 幅の上限は付けない（ウィンドウが狭くても N 文字で折り返し、
            # はみ出す分は横スクロールで見せる。2026-10-26 利用者の判断）。
            line.setNumColumns(self._columns)
            line.setPosition(QPointF(margin, height))
            height += line.height()
        text_layout.endLayout()

        # 縦スクロールバーの目盛りは「文書全体の視覚行の数」でできて
        # いる（``QTextDocument.lineCount()`` ＝ 各ブロックのこの値の
        # 合計）。組み直したら必ず教え直すこと——忘れると、折り返しが
        # 増えてもスクロールできる範囲が増えない。
        block.setLineCount(text_layout.lineCount())
        # 「自分が組んだ」印として視覚行の数を控える
        # (:meth:`_needs_column_layout`)。見えているぶんしか溜まらないが、
        # 文書を変えずに延々とスクロールされると増え続けるので、
        # 増えすぎたら捨てる（捨てても、もう一度組み直すだけで害は無い）。
        if len(self._laid_out) > _MAX_REMEMBERED_BLOCK_LAYOUTS:
            self._laid_out.clear()
        self._laid_out[block.blockNumber()] = text_layout.lineCount()


class EditorWidget(QPlainTextEdit):
    """1 つの文書 (タブ) を表す編集ウィジェット.

    ファイルパス・文字コード・改行コードといった「その文書に紐づく情報」を
    ウィジェット自身が保持する。まだファイルに保存されていない文書は
    ``path`` が None で、``untitled_name`` に「無題-1」などの仮の名前を持つ。
    """

    #: タブやウィンドウに表示すべき内容 (名前・変更状態・文字コード) が
    #: 変わったときに発火する
    display_state_changed = Signal()

    def __init__(
        self,
        parent=None,
        *,
        path: Path | None = None,
        untitled_name: str = "",
        encoding: str = DEFAULT_ENCODING,
        newline: str = DEFAULT_NEWLINE,
    ) -> None:
        super().__init__(parent)
        # 「指定文字数で折り返す」を自前で組むための文書レイアウト
        # (:class:`_EditorDocumentLayout`) を挿した文書に差し替える。
        # ``QPlainTextEdit`` は ``QPlainTextDocumentLayout`` 以外を挿した
        # 文書を受け取らない（警告を出して無視する）ので、**派生クラス**で
        # なければならない。
        document = QTextDocument(self)
        self._document_layout = _EditorDocumentLayout(document)
        document.setDocumentLayout(self._document_layout)
        self.setDocument(document)

        self._path = Path(path) if path is not None else None
        self._untitled_name = untitled_name
        self._encoding = encoding
        #: 文字コードの判定が拮抗したときの「もう一方の候補」。
        #: :attr:`encoding_alternatives` を参照。
        self._encoding_alternatives: tuple[str, ...] = ()
        #: 最後に見たディスク上の状態。:meth:`remember_disk_state` で控える。
        self._disk_signature: tuple[int, int] | None = None
        #: 上書きされる前の姿を既に控えたファイル。:attr:`backed_up_path` 参照。
        self._backed_up_path: Path | None = None
        self.newline = newline
        self._wrap_mode = DEFAULT_WRAP_MODE
        self._wrap_column = DEFAULT_WRAP_COLUMN
        self._font_family = DEFAULT_FONT_FAMILY
        self._font_size = DEFAULT_FONT_SIZE
        self._editor_bg: QColor | None = None
        self._editor_fg: QColor | None = None
        # カスタム配色の再適用中フラグ（changeEvent の再入防止）。
        self._reapplying_editor_colors = False
        self._show_line_breaks = DEFAULT_SHOW_LINE_BREAKS
        #: ホイールの回転のうち、まだ 1 ノッチに満たない端数（:meth:`wheelEvent`）。
        self._wheel_remainder = 0
        #: ホイール 1 ノッチで送る行数（:meth:`set_wheel_scroll_lines`）。
        self._wheel_scroll_lines = WHEEL_SCROLL_LINES

        # ``QPlainTextEdit`` はそもそも書式を持たないので、``QTextEdit``
        # のときに必要だった ``setAcceptRichText(False)`` は要らない
        # （貼り付けは常に書式の落ちたプレーンテキストになる）。

        # シンプルなエディタなので折り返しなしの等幅フォント表示を既定とする。
        self.set_wrap_mode(DEFAULT_WRAP_MODE)
        self.set_font_settings(DEFAULT_FONT_FAMILY, DEFAULT_FONT_SIZE)

        self.document().modificationChanged.connect(
            lambda _modified: self.display_state_changed.emit()
        )

    def event(self, event) -> bool:  # noqa: N802 - Qt の命名規則
        """アプリのショートカットに割り当てたキーは、Emacs 風の編集キー
        バインドとして自分で処理せず、ショートカットとして発火させる。"""
        if event.type() == QEvent.Type.ShortcutOverride:
            mods = event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
            if (event.key(), mods) in _RESERVED_APP_SHORTCUTS:
                event.ignore()
                return True
        return super().event(event)

    def wheel_scroll_lines(self) -> int:
        """ホイール 1 ノッチで縦に送る行数。"""
        return self._wheel_scroll_lines

    def set_wheel_scroll_lines(self, lines: int) -> None:
        """ホイール 1 ノッチで縦に送る行数を設定する（1〜上限に丸める）。"""
        self._wheel_scroll_lines = max(1, min(int(lines), MAX_WHEEL_SCROLL_LINES))

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        """ホイール 1 ノッチで :meth:`wheel_scroll_lines` 行だけ縦に送る。

        ``QPlainTextEdit`` の縦スクロールバーは 1 目盛り＝1 行（折り返して
        いれば見た目の 1 行）なので、目盛りを直接動かせばよい。

        修飾キー付き（Ctrl＝文字の拡大縮小、Shift＝横スクロールなど）と、
        横向きの回転は、今までどおり基底クラスに任せる。
        """
        delta = event.angleDelta().y()
        if event.modifiers() != Qt.KeyboardModifier.NoModifier or delta == 0:
            super().wheelEvent(event)
            return
        self._wheel_remainder += delta
        notches = int(self._wheel_remainder / WHEEL_NOTCH_DELTA)  # 0 へ向けて切り捨て
        if notches:
            self._wheel_remainder -= notches * WHEEL_NOTCH_DELTA
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - notches * self._wheel_scroll_lines)
        event.accept()

    # ------------------------------------------------------------------
    # 折り返し（実機フィードバックにより追加）
    # ------------------------------------------------------------------
    def wrap_mode(self) -> str:
        """現在の折り返しモード（``WRAP_NONE``/``WRAP_WINDOW``/``WRAP_FIXED``）。"""
        return self._wrap_mode

    def wrap_column(self) -> int:
        """``WRAP_FIXED`` のときに折り返す文字数。"""
        return self._wrap_column

    def set_wrap_mode(self, mode: str, *, column: int | None = None) -> None:
        """折り返しモードを設定する。

        - ``WRAP_NONE``: 折り返さない（横スクロール）
        - ``WRAP_WINDOW``: ウィンドウ（このウィジェット）の幅で折り返す
        - ``WRAP_FIXED``: ``column`` で指定した文字数で折り返す
          （省略時は前回の・既定の文字数を使う）
        """
        # **覚えるより先に確かめる。** 知らないモードを受け取ったとき、
        # 先に覚えてしまうと「画面は前のまま・記憶だけ知らない値」という
        # ちぐはぐな状態で残り、設定の保存を通じて次の起動まで持ち越される。
        if mode not in (WRAP_NONE, WRAP_WINDOW, WRAP_FIXED):
            raise ValueError(f"unknown wrap mode: {mode!r}")

        self._wrap_mode = mode
        if column is not None:
            self._wrap_column = column

        if mode == WRAP_NONE:
            self._document_layout.set_fixed_columns(0)
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        elif mode == WRAP_WINDOW:
            self._document_layout.set_fixed_columns(0)
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        elif mode == WRAP_FIXED:
            # 「指定文字数」は ``QPlainTextEdit`` には無いので、折り返し
            # そのものは :class:`_EditorDocumentLayout` が組む。
            # ウィジェット側は「ウィンドウ幅で折り返す」にしておく。
            #
            # **これは動きを変えるための行ではない**（「折り返さない」に
            # しても見た目・スクロール・横スクロールバーのどれも変わら
            # ないことを実測で確かめた。こちらは幅を ``viewport()`` から
            # 直に訊いていて、``QPlainTextEdit`` が文書レイアウトへ渡す
            # 幅には頼っていないため）。**実際に折り返している以上、
            # ``lineWrapMode()`` にも折り返していると答えさせる**ための
            # 行。こうしておけば、文字数の折り返しを外した（``columns``
            # が 0 の）瞬間に基底クラスへ落ちても、本文は右へ流れ出さず
            # ウィンドウ幅で折り返す。
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            self._document_layout.set_fixed_columns(self._wrap_column)

    # ------------------------------------------------------------------
    # 改行記号の表示（実機フィードバックにより追加）
    # ------------------------------------------------------------------
    def show_line_breaks(self) -> bool:
        """行末に改行記号を表示しているかどうか。"""
        return self._show_line_breaks

    def set_show_line_breaks(self, enabled: bool) -> None:
        """行末に改行記号（↓）を表示する／しないを切り替える。

        EmEditor 等にある「改行記号を表示」に相当する機能。以前は Qt が
        持つ ``QTextOption.Flag.ShowLineAndParagraphSeparators`` を使って
        いたが、あれは「¶ 風の記号」で**形も色も変えられない**。
        「↓ を薄い青で薄っすら」という実機からの要望に応えるため、
        ``paintEvent()`` での自前描画に切り替えた。ここでは表示状態を
        覚えて再描画を促すだけでよい。
        """
        self._show_line_breaks = enabled
        self.viewport().update()

    def line_break_mark_points(self) -> list[QPoint]:
        """改行記号を描く位置（ビューポート座標のベースライン左端）の一覧。

        描画そのものと切り離してあるのは、ヘッドレス環境でも位置決めの
        規則をテストで固定できるようにするため。規則は次のとおり:

        - **次のブロックがある**（＝そこに実際に改行がある）ブロックだけに
          描く。文書末尾の改行が無い行には描かない。
        - ソフトラップ（折り返し）の境界は改行ではないので描かない。
          折り返された行では**最後の視覚行の行末**に 1 つだけ描く。
        - 画面に見えているブロックだけを走査する（大きなファイルでも
          文書全体を舐めない）。
        """
        if not self._show_line_breaks:
            return []

        viewport_height = self.viewport().height()
        # ``QPlainTextEdit`` は「画面のいちばん上に出ているブロック」を
        # そのまま教えてくれる（``firstVisibleBlock``）。``QTextEdit``
        # だった頃は当たり判定で探り当ててから上へさかのぼっていたが、
        # その必要はもう無い。
        block = self.firstVisibleBlock()
        offset = self.contentOffset()
        ascent = self.fontMetrics().ascent()
        points: list[QPoint] = []
        while block.isValid() and block.next().isValid():
            geometry = self.blockBoundingGeometry(block).translated(offset)
            if geometry.top() > viewport_height:
                break
            if geometry.bottom() >= 0:
                rect = self._end_of_block_rect(block)
                points.append(QPoint(rect.left() + _LINE_BREAK_MARK_GAP, rect.top() + ascent))
            block = block.next()
        return points

    def _end_of_block_rect(self, block):
        """ブロック（論理行）の**行末**のカーソル矩形（ビューポート座標）。

        折り返されているブロックでは、最後の視覚行の末尾になる。
        """
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        return self.cursorRect(cursor)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        """本文を描いたあと、改行記号を自前で描き足す。

        ``QPlainTextEdit`` は ``QAbstractScrollArea`` なので、この
        ``paintEvent`` はビューポートの描画イベント。親クラスの描画が
        終わってから ``self.viewport()`` に対して上書きで描く。
        """
        super().paintEvent(event)
        points = self.line_break_mark_points()
        if not points:
            return
        painter = QPainter(self.viewport())
        painter.setPen(LINE_BREAK_MARK_COLOR)
        painter.setFont(self.font())
        for point in points:
            painter.drawText(point, LINE_BREAK_MARK)
        painter.end()

    # ------------------------------------------------------------------
    # フォント（実機フィードバックにより追加）
    # ------------------------------------------------------------------
    def font_family(self) -> str:
        """現在の本文フォントのファミリー名。"""
        return self._font_family

    def font_size(self) -> int:
        """現在の本文フォントのサイズ (pt)。"""
        return self._font_size

    def set_font_settings(self, family: str, size: int) -> None:
        """本文フォントのファミリーとサイズ (pt) を設定する。

        タブ幅 (``setTabStopDistance``) は「半角スペース ``TAB_STOP_SPACES``
        個ぶんの幅」なのでフォントの文字幅に依存する。フォントを変えたら
        必ず計算し直す必要がある。
        """
        if size < 1:
            size = DEFAULT_FONT_SIZE

        self._font_family = family
        self._font_size = size

        font = QFont(family, size)
        if family == DEFAULT_FONT_FAMILY:
            # 総称名 "monospace" を実フォントへ解決するためのヒント。
            # 利用者が具体的なフォント名を選んだときは、そのフォントを
            # 尊重したいのでヒントは付けない。
            font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setTabStopDistance(
            TAB_STOP_SPACES * self.fontMetrics().horizontalAdvance(" ")
        )

    # ------------------------------------------------------------------
    # エディタの配色（実機フィードバックにより追加）
    # ------------------------------------------------------------------
    def editor_colors(self) -> tuple[QColor | None, QColor | None]:
        """カスタム設定中の (背景色, 文字色)。テーマに合わせている場合は
        それぞれ None。"""
        return self._editor_bg, self._editor_fg

    def set_editor_colors(self, background: QColor | None, text: QColor | None) -> None:
        """エディタ本文の背景色・文字色を、アプリ全体のテーマとは独立に
        固定する。両方 None を渡すとテーマに合わせる状態に戻る。

        一度でも明示的に ``setPalette()`` すると、その要素は以後
        ``QApplication.setPalette()``（テーマ切り替え）で自動追従しなく
        なる（Qt の ``WA_SetPalette`` 属性の仕様）。そのため「テーマに
        合わせる」への復帰は、単に値を戻すだけでなく明示的に属性を
        外して本当の意味で継承状態に戻す必要がある。

        ``QPlainTextEdit`` は ``QAbstractScrollArea`` なので、実際に背景を
        描画しているのは ``self`` ではなく ``self.viewport()``。通常は
        親子関係でパレットが伝播するが、**一度カスタム配色を設定した
        あとにリセットし、続けてまた別の色を設定する**という手順を踏むと
        ``viewport()`` 側のパレットが追従しなくなり、本文の背景色だけ
        古い色のまま反映されない不具合が実機フィードバックで見つかった
        （文字色は ``self`` 側の変更で正しく反映されるため、背景色だけが
        置き去りになって気づきにくい）。自動追従に頼らず、
        ``viewport()`` にも明示的に同じパレットを設定して確実に揃える。
        """
        self._editor_bg = background
        self._editor_fg = text

        if background is None and text is None:
            self.setPalette(QPalette())
            self.setAttribute(Qt.WidgetAttribute.WA_SetPalette, False)
            self.viewport().setPalette(QPalette())
            self.viewport().setAttribute(Qt.WidgetAttribute.WA_SetPalette, False)
            return

        palette = self.palette()
        if background is not None:
            palette.setColor(QPalette.ColorRole.Base, background)
        if text is not None:
            palette.setColor(QPalette.ColorRole.Text, text)
        self.setPalette(palette)
        self.viewport().setPalette(palette)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        """スタイル／パレットが変わったら、カスタム配色を貼り直す。

        ``QApplication.setStyleSheet()``（テーマ切り替え）を呼ぶと、Qt は
        全ウィジェットを polish し直し、その過程でウィジェットへ個別に
        設定してあったパレットが取り消されてしまう。そのままだと
        「エディタの配色を設定 → テーマを切り替えたら配色が消える」
        という不具合になるため、ここで自分の設定を貼り直す。
        """
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.StyleChange,
            QEvent.Type.PaletteChange,
        ):
            self._reapply_editor_colors()

    def _reapply_editor_colors(self) -> None:
        if self._reapplying_editor_colors:
            return

        if self._editor_bg is None and self._editor_fg is None:
            # テーマ任せ。polish のやり直しで、以前のカスタム配色が
            # ウィジェットへ復元されてしまっていたら取り消して、
            # 親（テーマ）からの伝播に従う状態へ戻す。
            if self.testAttribute(Qt.WidgetAttribute.WA_SetPalette):
                self._reapplying_editor_colors = True
                try:
                    self.set_editor_colors(None, None)
                finally:
                    self._reapplying_editor_colors = False
            return

        palette = self.palette()
        bg_ok = self._editor_bg is None or (
            palette.color(QPalette.ColorRole.Base) == self._editor_bg
        )
        fg_ok = self._editor_fg is None or (
            palette.color(QPalette.ColorRole.Text) == self._editor_fg
        )
        if bg_ok and fg_ok:
            return

        self._reapplying_editor_colors = True
        try:
            self.set_editor_colors(self._editor_bg, self._editor_fg)
        finally:
            self._reapplying_editor_colors = False

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        """ファイルのドラッグ&ドロップは、本文へのテキスト挿入ではなく
        「ファイルを開く」として扱う（本文の通常のドラッグ&ドロップ編集は
        そのまま QPlainTextEdit の既定動作に任せる）。"""
        if event.mimeData().hasFormat(TAB_MIME_TYPE):
            # 他のウィンドウから流れてきたタブ。本文は関知しないので、
            # ウィンドウ（MainWindow）に処理させる。
            event.ignore()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        if event.mimeData().hasFormat(TAB_MIME_TYPE):
            event.ignore()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        if event.mimeData().hasFormat(TAB_MIME_TYPE):
            event.ignore()
            return
        urls = event.mimeData().urls()
        if any(url.isLocalFile() for url in urls):
            window = self.window()
            if hasattr(window, "open_dropped_urls"):
                window.open_dropped_urls(urls)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # ------------------------------------------------------------------
    # 検索結果のハイライト（機能 6・7 の拡張）
    # ------------------------------------------------------------------
    def set_match_highlights(
        self,
        current: tuple[int, int] | None,
        others: list[tuple[int, int]],
    ) -> None:
        """検索でヒットした箇所を本文中にハイライト表示する。

        ``current``（現在指している 1 件）と ``others``（それ以外のヒット）を
        別の色でハイライトする。``QTextEdit.ExtraSelection`` を使うので、
        本文そのもの（Undo 履歴やドキュメント内容）には一切影響しない。
        """
        selections: list[QTextEdit.ExtraSelection] = []

        for start, length in others:
            selections.append(self._make_highlight(start, length, _MATCH_HIGHLIGHT_COLOR))

        if current is not None:
            start, length = current
            selections.append(
                self._make_highlight(start, length, _CURRENT_MATCH_HIGHLIGHT_COLOR)
            )

        self.setExtraSelections(selections)

    def _make_highlight(
        self, start: int, length: int, color: QColor
    ) -> QTextEdit.ExtraSelection:
        cursor = QTextCursor(self.document())
        cursor.setPosition(start)
        cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)

        fmt = QTextCharFormat()
        fmt.setBackground(color)

        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = fmt
        return selection

    # ------------------------------------------------------------------
    # パス / 表示名
    # ------------------------------------------------------------------
    @property
    def path(self) -> Path | None:
        """保存先のファイルパス。未保存の新規文書では None。"""
        return self._path

    @path.setter
    def path(self, value: Path | str | None) -> None:
        new_path = Path(value) if value is not None else None
        # 「このタブは、このファイルを上書きする前の姿を既に控えた」という
        # 記録は、**指す先が変わったときだけ**捨てる（:attr:`backed_up_path`）。
        # 保存できた直後にも同じパスがここへ代入し直されるので、無条件に
        # 捨てると「初めての保存」の控えが毎回の保存で作られてしまう。
        if new_path != self._path:
            self._backed_up_path = None
        self._path = new_path
        # 指す先が変わった以上、前のファイルについて覚えていた「見分け札」は
        # もう使えない。ここで必ず捨て、必要なら代入した側が
        # :meth:`remember_disk_state` で取り直す（別のファイルの札を
        # 持ち越すと、そのファイルは外部で変わっていないのに
        # 「変わっている」と判定されてしまう）。
        self._disk_signature = None
        self.display_state_changed.emit()

    # ------------------------------------------------------------------
    # 外部変更の検知用（保存直前のチェック）
    # ------------------------------------------------------------------
    @property
    def disk_signature(self) -> tuple[int, int] | None:
        """このタブが最後に見たディスク上の状態 ``(更新日時 ns, サイズ)``。

        「保存しようとした時点で、読み込んだ後に誰かがこのファイルを
        書き換えていないか」を判定するための控え
        (:func:`~editor_app.fileio.file_signature`)。まだ控えていない
        （無題タブ・存在しないファイル）場合は None。
        """
        return self._disk_signature

    def remember_disk_state(self) -> None:
        """今ディスクにある状態を「自分が知っている状態」として控え直す。

        **控えるのはこの 1 か所だけにしてある。** 控え直すべき場面は
        「開いた直後」「保存できた直後」「外部変更を読み直した直後」と
        散らばっており、それぞれで ``stat`` を書くと必ずどれかが漏れて
        「保存できないタブ」や「外部変更を見逃すタブ」が生まれる。
        """
        self._disk_signature = file_signature(self._path)

    # ------------------------------------------------------------------
    # 上書き前の控え（引き継ぎ ⑦。:mod:`editor_app.backups`）
    # ------------------------------------------------------------------
    @property
    def backed_up_path(self) -> Path | None:
        """このタブが「上書きする前の姿」を既に控えたファイル（無ければ None）。

        控えるのは**そのタブがそのファイルへ初めて書き込む直前の 1 回だけ**
        なので、「もう控えたかどうか」をタブ自身が覚えておく必要がある。
        真偽値ではなく**パス**で持つのは、「名前を付けて保存」で保存先が
        移ったときに、前のファイルの控えを取ったことをもって新しい保存先を
        控えずに済ませてしまわないため（:attr:`path` の setter で捨てている
        のもこの理由）。
        """
        return self._backed_up_path

    @backed_up_path.setter
    def backed_up_path(self, value: Path | str | None) -> None:
        self._backed_up_path = Path(value) if value is not None else None

    @property
    def encoding(self) -> str:
        """この文書の文字コード (コーデック名)。

        読み込んだときのものを保持し、保存時もこれで書き戻す (機能 8)。
        「文字コードを指定して保存」で保存し直したときと、外部で変わった
        ファイルを再読み込みしたときは、その文字コードに入れ替わる。
        """
        return self._encoding

    @encoding.setter
    def encoding(self, value: str) -> None:
        """文字コードを差し替え、変わったならウィンドウ側へ知らせる。

        ステータスバーには「今のタブの文字コード」が出ているので、
        代入した誰か (開く・再読み込み・文字コードを指定して保存) が
        それぞれ更新を呼ぶ形にすると、いずれ呼び忘れが出る。
        ここ 1 か所から :attr:`display_state_changed` を出すことで、
        代入すれば必ず表示も追いつくようにしてある。
        """
        if value == self._encoding:
            return
        self._encoding = value
        self.display_state_changed.emit()

    @property
    def encoding_alternatives(self) -> tuple[str, ...]:
        """文字コードの判定が拮抗したときの、もう一方の候補（無ければ空）。

        「このファイルは cp932 でも euc_jp でも読めてしまい、点数で
        cp932 を選んだ」という事情
        （:func:`~editor_app.fileio._pick_japanese_legacy_detail`）を、
        ステータスバーの印として利用者に伝えるために持っている
        （利用者の最優先事項は「文字化けしたまま編集して保存し、ファイルを
        壊してしまう」ことを避けること。**編集する前に気づける経路**を
        増やすのがいちばん効く）。

        **文字コードが変わったら必ず入れ直すこと。** 開き直し・再読み込み・
        文字コードを指定して保存のいずれも、その時点の判定に基づいて
        設定し直す（利用者が自分で文字コードを選んだ場合は「拮抗は解決した」
        ので空にする）。
        """
        return self._encoding_alternatives

    @encoding_alternatives.setter
    def encoding_alternatives(self, value) -> None:
        alternatives = tuple(value)
        if alternatives == self._encoding_alternatives:
            return
        self._encoding_alternatives = alternatives
        self.display_state_changed.emit()

    @property
    def untitled_name(self) -> str:
        """未保存の新規文書に割り当てられた仮の名前 (例: 無題-1)。"""
        return self._untitled_name

    @property
    def display_name(self) -> str:
        """タブに表示するファイル名 (変更マークは含まない)。"""
        if self._path is not None:
            return self._path.name
        return self._untitled_name

    @property
    def is_untitled(self) -> bool:
        """まだ一度もファイルに保存されていない文書かどうか。"""
        return self._path is None

    # ------------------------------------------------------------------
    # 変更状態
    # ------------------------------------------------------------------
    @property
    def is_modified(self) -> bool:
        """最後の保存 (または読み込み) 以降に編集されているかどうか。"""
        return self.document().isModified()

    def set_modified(self, modified: bool) -> None:
        self.document().setModified(modified)

    def set_content(self, text: str) -> None:
        """本文を丸ごと差し替え、「未編集」の状態にする。

        ファイルを読み込んだ直後に使う。undo 履歴も破棄して、
        読み込み前の内容に undo で戻れないようにする。
        """
        self.setPlainText(text)
        self.document().clearUndoRedoStacks()
        self.set_modified(False)

    def is_empty_untitled(self) -> bool:
        """「未保存・未編集・空」の新規タブかどうか。

        「新規作成」直後のまっさらなタブを、ファイルを開いたときに
        使い回してよいかを判断するために使う。
        """
        return self.is_untitled and not self.is_modified and not document_text(self)


def center_cursor_vertically(editor: EditorWidget) -> None:
    """カーソルの位置が、なるべくビューポートの中央寄りに来るようスクロールする。

    ``ensureCursorVisible()`` は「最小限だけ」スクロールするため、
    ジャンプ直後のカーソルがビューポートの端ぎりぎりに来ることが多い
    （実機フィードバック：検索でヒットした語が画面端に埋もれて気づき
    づらい、という報告への対応）。``ensureCursorVisible()`` の後に呼び、
    縦方向のスクロール位置だけ追加で調整する。

    ``QTextEdit`` だった頃は「垂直スクロールバーがピクセル単位」なのを
    利用して ``cursorRect()`` との差を足していたが、``QPlainTextEdit``
    の垂直スクロールバーは**行単位**なので、ピクセルを足すと見当違いの
    ところへ飛ぶ。``QPlainTextEdit`` には同じことをする
    ``centerCursor()`` が備わっているので、それに任せる
    （Qt 側が行単位で計算してくれる）。

    検索でのジャンプ (`search_replace.py`) と、保存できない文字への
    ジャンプ (`main_window.py`) の両方から使うので、ここに置いてある。
    """
    editor.centerCursor()
