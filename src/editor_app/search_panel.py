"""ウィンドウ内の全タブを横断する検索・置換パネル (機能 6・7)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QPalette, QTextCursor
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from .editor import document_text
from .settings import load_search_input_lines

#: プレビュー文字列に使う、マッチ内の改行の置き換え記号（機能 6 のテストでも使う）。
NEWLINE_GLYPH = "⏎"

#: 検索したあとに本文が変わっていて、置換を見送ったときに件数欄へ出す案内。
STALE_RESULT_NOTICE = "本文が変わったため検索し直しました"

#: 検索欄を打ち替えたが、まだ検索していないときに件数欄へ出す案内。
#: 検索はインクリメンタルサーチではなく明示的な操作（Enter / 「検索」ボタン）で
#: 行うため、古い件数のまま黙って残さず「まだ検索していない」ことを伝える。
PENDING_QUERY_NOTICE = "Enter か「検索」で検索"

#: 検索欄・置換欄の配色（実機フィードバックにより追加）。ダークテーマだと
#: 背景が暗く選択ハイライトも見えづらいとの指摘があったため、アプリの
#: テーマとは無関係に常にこの配色で固定する（本文の折り返し・配色設定とは
#: 別で、利用者が変更できる項目にはしていない）。
_INPUT_BASE_COLOR = QColor("white")
_INPUT_TEXT_COLOR = QColor("black")
_INPUT_HIGHLIGHT_COLOR = QColor(51, 144, 255)
_INPUT_HIGHLIGHTED_TEXT_COLOR = QColor("white")


@dataclass(frozen=True)
class SearchMatch:
    """1 件の検索結果 (あるタブ内の 1 箇所)。

    検索語自体が改行を含む場合、マッチは複数行にまたがることがある
    (EmEditor の複数行検索・置換と同様)。位置は「行番号 + 行内オフセット」
    ではなく、文書全体を通した文字位置 (``position``) で持つ。こうすると
    複数行にまたがるマッチも `QTextCursor` にそのまま渡せて扱いやすい。
    """

    editor_index: int
    tab_title: str
    position: int  #: 文書全体を通した開始位置 (0 始まり)
    length: int  #: マッチした文字列の長さ (改行も 1 文字として数える)
    line: int  #: マッチが始まる行番号 (1 始まり)
    snippet: str  #: マッチが含まれる行 (複数行なら全行) のプレビュー文字列


class _QueryTextEdit(QPlainTextEdit):
    """検索欄・置換欄で共通して使う複数行入力欄。

    - Enter: ``activate_current`` を発火（検索欄では「次を検索」、
      置換欄では「選択中の 1 件を置換」に使う）
    - Shift+Enter: ``activate_shift`` を発火（検索欄では「前を検索」に使う）
    - Ctrl+Enter: 検索語・置換後文字列に改行を挿入する
      （複数行検索・置換用。ふつうは改行を含む文字列をペーストするだけで
      よいので、手入力で改行したいときだけ使う想定）
    - Esc: パネルを閉じる
    """

    activate_current = Signal()
    activate_shift = Signal()
    escape_pressed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # changeEvent() 内での setPalette() が自分自身を再入させないための
        # ガード（EditorWidget._reapplying_editor_colors と同じ考え方）。
        self._reapplying_fixed_palette = False
        self._apply_fixed_palette()

    def _apply_fixed_palette(self) -> None:
        """配色を白背景・黒文字・見やすいハイライト色に固定する。

        ``QTextEdit``/``QPlainTextEdit`` は ``QAbstractScrollArea`` なので、
        実際に描画しているのは ``self`` ではなく ``self.viewport()``。
        両方に同じパレットを設定しないと、背景色だけ反映されない
        （``EditorWidget.set_editor_colors()`` で見つかった不具合と同じ
        原因なので、ここでも両方に設定しておく）。
        """
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Base, _INPUT_BASE_COLOR)
        palette.setColor(QPalette.ColorRole.Text, _INPUT_TEXT_COLOR)
        palette.setColor(QPalette.ColorRole.Highlight, _INPUT_HIGHLIGHT_COLOR)
        palette.setColor(QPalette.ColorRole.HighlightedText, _INPUT_HIGHLIGHTED_TEXT_COLOR)
        self.setPalette(palette)
        self.viewport().setPalette(palette)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        """テーマ切替（``QApplication.setStyleSheet()``）で全ウィジェットが
        repolish されると、個別に設定したパレットが取り消されることがある
        （``EditorWidget.changeEvent()`` と同じ理由）。ここでも配色を
        貼り直して、常に白背景・黒文字を保つ。
        """
        super().changeEvent(event)
        if self._reapplying_fixed_palette:
            return
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.PaletteChange):
            self._reapplying_fixed_palette = True
            try:
                self._apply_fixed_palette()
            finally:
                self._reapplying_fixed_palette = False

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt の命名規則
        key = event.key()
        mods = event.modifiers()

        if key == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if mods & Qt.KeyboardModifier.ControlModifier:
                # QPlainTextEdit は Ctrl+Return では何もしない（改行を
                # 挿入しない）ため、自分で挿入する。
                self.textCursor().insertText("\n")
            elif mods & Qt.KeyboardModifier.ShiftModifier:
                self.activate_shift.emit()
            else:
                self.activate_current.emit()
            return

        super().keyPressEvent(event)


class SearchPanel(QWidget):
    """検索欄・置換欄をまとめたパネル。

    見た目と「現在どのマッチを指しているか」の管理だけを担当し、実際に
    「全タブを検索する」ロジックは持たない。呼び出し側 (MainWindow) が
    :attr:`search_requested` を受けて全タブを検索し、:meth:`set_matches` で
    結果を渡す。ナビゲーションは Enter（次へ）/ Shift+Enter（前へ）で行い、
    結果の一覧表示は行わず「n/N 件」という件数表示だけにしてある。

    **検索はインクリメンタルサーチではない。** 1 文字打つたびに全タブを
    検索するとヒット数が多いときに入力が固まるため、検索欄への入力では
    検索せず、Enter か「検索」ボタンという明示的な操作で初めて検索する
    （:meth:`run_search`）。「最後に実際に検索した語」は
    :meth:`searched_query` が返し、検索欄の内容がそれと違う間は件数欄に
    :data:`PENDING_QUERY_NOTICE` を出して「まだ検索していない」ことを示す。
    """

    #: 検索を実行してほしい（Enter /「検索」ボタン等の明示的な操作）。
    #: 呼び出し側は全タブを検索し、:meth:`set_matches` で結果を返すこと。
    search_requested = Signal(str)

    #: 現在（あるいは新たに）指しているマッチへジャンプしてほしい
    match_activated = Signal(object)  # SearchMatch

    #: 現在指している 1 件を置換してほしい（置換欄で Enter / 「置換」ボタン）
    replace_one_requested = Signal()

    #: 検索語に一致する全件を置換してほしい（「全置換」ボタン）
    replace_all_requested = Signal()

    #: パネルを閉じる（Esc / 閉じるボタン）
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._matches: list[SearchMatch] = []
        self._current_match_index = -1
        #: 最後に実際に検索した語（一度も検索していなければ None）。
        #: 検索欄の内容とこれが違う＝「打ち替えたがまだ検索していない」。
        self._searched_query: str | None = None
        #: 検索欄・置換欄の表示行数（実機フィードバックにより設定可能にした。
        #: 元は 3 行決め打ちだったが、まず 2 倍の 6 行に変更したうえで、
        #: 利用者が行数を指定できるようにした）。
        self._visible_lines = load_search_input_lines()

        self._input = _QueryTextEdit(self)
        self._input.setPlaceholderText(
            "検索... (Enter=検索/次へ Shift+Enter=前へ Ctrl+Enterで改行=複数行検索)"
        )
        self._input.setTabChangesFocus(True)
        self._input.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._set_input_height(self._visible_lines)
        # 入力そのものでは検索しない（インクリメンタルサーチをやめた）。
        # 打っている間は件数欄とボタンの状態を更新するだけに留める。
        self._input.textChanged.connect(self._on_query_text_changed)
        self._input.activate_current.connect(self._search_or_go_to_next_match)
        self._input.activate_shift.connect(self._search_or_go_to_previous_match)
        self._input.escape_pressed.connect(self.closed)

        self._search_button = QPushButton("検索", self)
        self._search_button.setToolTip(
            "検索欄の語で全タブを検索します (Enter と同じ)"
        )
        self._search_button.clicked.connect(self._search_or_go_to_next_match)

        self._count_label = QLabel(self)
        self._count_label.setMinimumWidth(80)
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 検索欄を閉じる × ボタンは置いていない（実機フィードバックにより
        # 削除）。Esc キー、またはフローティングウィンドウ自身のタイトルバーの
        # × で閉じられるので不要。

        # 置換欄 (機能 7)。Ctrl+H で開いたときだけ見せる。検索欄と同じ
        # _QueryTextEdit を使うので、置換後の文字列にも改行を入れられる。
        self._replace_input = _QueryTextEdit(self)
        self._replace_input.setPlaceholderText(
            "置換後の文字列... (Ctrl+Enterで改行)"
        )
        self._replace_input.setTabChangesFocus(True)
        self._replace_input.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._set_input_height(self._visible_lines, target=self._replace_input)
        self._replace_input.activate_current.connect(self.replace_one_requested)
        self._replace_input.activate_shift.connect(self._insert_newline_in_replace_field)
        self._replace_input.escape_pressed.connect(self.closed)

        self._replace_button = QPushButton("置換", self)
        self._replace_button.setToolTip("現在指している 1 件を置換します")
        self._replace_button.clicked.connect(self.replace_one_requested)

        self._replace_all_button = QPushButton("全置換", self)
        self._replace_all_button.setToolTip("見つかった全件を置換します")
        self._replace_all_button.clicked.connect(self.replace_all_requested)

        # 検索行（0 行目）と置換行（1 行目）を QGridLayout に並べる。列を
        # 共有するので、**検索欄と置換欄の入力ボックスは常に同じ幅**になる
        # （実機フィードバック：検索欄と置換欄で幅が違うのが気になる、との
        # 指摘への対応）。0 列目（入力欄）だけを伸ばす。
        top = Qt.AlignmentFlag.AlignTop
        grid = QGridLayout(self)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.addWidget(self._input, 0, 0)
        grid.addWidget(self._search_button, 0, 1, top)
        grid.addWidget(self._count_label, 0, 2, top)
        grid.addWidget(self._replace_input, 1, 0)
        grid.addWidget(self._replace_button, 1, 1, top)
        grid.addWidget(self._replace_all_button, 1, 2, top)
        grid.setColumnStretch(0, 1)

        # 置換行は Ctrl+H で置換モードにしたときだけ見せる。行の 3 つの
        # ウィジェットをまとめて出し入れする（全部隠せば行は潰れて高さ 0 になる）。
        self._replace_widgets = (
            self._replace_input,
            self._replace_button,
            self._replace_all_button,
        )
        for widget in self._replace_widgets:
            widget.hide()

        self._update_count_label()
        self._update_replace_buttons_enabled()

    def _set_input_height(
        self, visible_lines: int, *, target: QPlainTextEdit | None = None
    ) -> None:
        widget = target if target is not None else self._input
        metrics = widget.fontMetrics()
        margins = widget.contentsMargins()
        height = (
            metrics.lineSpacing() * visible_lines
            + margins.top()
            + margins.bottom()
            + 8  # QPlainTextEdit 内部の余白ぶん
        )
        widget.setFixedHeight(height)

    def input_visible_lines(self) -> int:
        """検索欄・置換欄の現在の表示行数。"""
        return self._visible_lines

    def set_input_visible_lines(self, visible_lines: int) -> None:
        """検索欄・置換欄の表示行数を変える（実機フィードバックにより追加）。

        両方の欄を常に同じ行数に揃える（利用者からの要望）。
        """
        self._visible_lines = visible_lines
        self._set_input_height(visible_lines)
        self._set_input_height(visible_lines, target=self._replace_input)

    # ------------------------------------------------------------------
    # 呼び出し側からの入力
    # ------------------------------------------------------------------
    def focus_input(self, *, select_all: bool = True) -> None:
        self._input.setFocus()
        if select_all:
            cursor = self._input.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            self._input.setTextCursor(cursor)

    def set_initial_text(self, text: str) -> None:
        """パネルを開いたときに、選択中の文字列などを検索欄へ流し込む。

        ``QTextCursor.selectedText()`` は改行を U+2029 (段落区切り) で返す
        ため、複数行選択でもそのまま検索できるように ``\\n`` へ変換する。
        """
        if not text:
            return
        self._input.setPlainText(text.replace(" ", "\n"))
        cursor = self._input.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self._input.setTextCursor(cursor)

    def query(self) -> str:
        return document_text(self._input)

    def searched_query(self) -> str | None:
        """最後に実際に検索した語（一度も検索していなければ None）。

        検索欄の内容 (:meth:`query`) と違うことがある（打ち替えたが
        まだ Enter を押していない状態）。本文の編集に追随して検索し直す
        ような「利用者が明示的に要求していない検索」は、検索欄の内容では
        なくこちらの語で行うこと。
        """
        return self._searched_query

    def is_query_pending(self) -> bool:
        """検索欄を打ち替えたが、まだその語で検索していない状態か。"""
        return self.query() != self._searched_query

    def run_search(self) -> None:
        """検索欄の語で実際に検索する（:attr:`search_requested` を出す）。

        既に同じ語で検索済みなら何もしない（Enter の連打が
        「次のマッチへ移動」になるように）。
        """
        if not self.is_query_pending():
            return
        self.search_requested.emit(self.query())

    def set_replace_mode(self, enabled: bool) -> None:
        """置換欄・置換ボタンを表示するかどうかを切り替える（Ctrl+F / Ctrl+H の違い）。"""
        for widget in self._replace_widgets:
            widget.setVisible(enabled)

    def is_replace_mode(self) -> bool:
        return self._replace_input.isVisible()

    def replace_text(self) -> str:
        return document_text(self._replace_input)

    def matches(self) -> list[SearchMatch]:
        """現在の検索結果全件（本文中のハイライト表示などに使う）。"""
        return list(self._matches)

    def current_match(self) -> SearchMatch | None:
        """現在指しているマッチ（無ければ None）。

        まだ Enter 等でナビゲートしていなければ、最初のマッチを指している
        ものとして扱う（「置換」ボタンをいきなり押しても最初の 1 件が
        対象になるように）。
        """
        if not self._matches:
            return None
        index = self._current_match_index if self._current_match_index >= 0 else 0
        return self._matches[index]

    def set_matches(
        self, matches: list[SearchMatch], *, query: str | None = None
    ) -> None:
        """全タブの検索結果を反映する（MainWindow が検索を実行した結果）。

        ``query`` は「その結果を得るために実際に検索した語」。省略すると
        検索欄の現在の内容を使う。本文の編集に追随して検索し直した場合
        など、検索欄の内容と実際に検索した語が違いうる場面では明示的に
        渡すこと（件数表示が「まだ検索していない」判定を誤らないように）。
        """
        self._matches = matches
        self._current_match_index = -1
        self._searched_query = self.query() if query is None else query
        self._update_count_label()
        self._update_replace_buttons_enabled()

    def show_stale_notice(self) -> None:
        """「検索したあとに本文が変わっていたので置換しなかった」ことを伝える。

        古い検索結果の位置のまま置換すると本文の関係ない場所を壊すため、
        ``MainWindow`` 側はその場合、置換せずに検索し直してこれを呼ぶ。
        ``set_matches()`` の後に呼ぶ想定（件数表示をこの案内で上書きする）。
        """
        self._count_label.setText(STALE_RESULT_NOTICE)

    def show_replacement_summary(self, count: int) -> None:
        """全置換が完了したことと、置換した件数をパネルに表示する。

        ``set_matches()`` の後に呼ぶ想定（全置換の結果、通常マッチ件数は
        変わっている＝その表示を、置換件数の表示で上書きする）。
        """
        self._count_label.setText(f"{count} 件を置換しました")

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _on_query_text_changed(self) -> None:
        """検索欄が打ち替えられた。**検索はしない**（表示の更新だけ）。"""
        self._update_count_label()
        self._update_replace_buttons_enabled()

    def _update_count_label(self) -> None:
        if not self.query():
            text = ""
        elif self.is_query_pending():
            # 古い件数をそのまま残すと、今の検索語の件数だと誤解される。
            text = PENDING_QUERY_NOTICE
        elif not self._matches:
            text = "見つかりません"
        elif self._current_match_index < 0:
            text = f"{len(self._matches)} 件"
        else:
            text = f"{self._current_match_index + 1}/{len(self._matches)} 件"
        self._count_label.setText(text)

    def _update_replace_buttons_enabled(self) -> None:
        # まだ検索していない語が入っているときも押せるようにしておく
        # （押した側＝MainWindow が、置換の前に検索し直してくれる）。
        # ここで無効にしてしまうと、検索語を打ってすぐ「全置換」を押す、
        # という自然な操作ができなくなる。
        enabled = bool(self._matches) or bool(self.query() and self.is_query_pending())
        self._replace_button.setEnabled(enabled)
        self._replace_all_button.setEnabled(enabled)

    def _go_to_relative_match(self, delta: int) -> None:
        if not self._matches:
            return
        if self._current_match_index < 0:
            self._current_match_index = 0
        else:
            self._current_match_index = (self._current_match_index + delta) % len(
                self._matches
            )
        self._update_count_label()
        self.match_activated.emit(self._matches[self._current_match_index])

    def _go_to_next_match(self) -> None:
        self._go_to_relative_match(1)

    def _go_to_previous_match(self) -> None:
        self._go_to_relative_match(-1)

    def _search_or_go_to_next_match(self) -> None:
        """Enter /「検索」ボタン: まだ検索していなければ検索し、次のマッチへ。

        検索したばかりのときは ``_current_match_index`` が -1 なので、
        続く :meth:`_go_to_next_match` は「1 件目」へ移動する。同じ語で
        もう一度押すと再検索せず次のマッチへ進む。
        """
        self.run_search()
        self._go_to_next_match()

    def _search_or_go_to_previous_match(self) -> None:
        """Shift+Enter: まだ検索していなければ検索し、前のマッチへ。"""
        self.run_search()
        self._go_to_previous_match()

    def _insert_newline_in_replace_field(self) -> None:
        cursor = self._replace_input.textCursor()
        cursor.insertText("\n")
