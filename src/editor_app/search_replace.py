"""ウィンドウ内の全タブを横断する検索・置換のミックスイン (機能 6・7).

``MainWindow`` から「Ctrl+F の検索」と「Ctrl+H の置換」だけを切り出したもの
（`view_settings.py` / `file_watch.py` と同じ切り出し方）。**検索・置換に
関する変更は、`main_window.py` ではなくこのモジュールに書くこと。**

役割の分かれ方は 3 層になっている。

1. `textsearch.py` … 文字列としての検索（Python の文字位置と Qt の
   UTF-16 位置の違いを吸収する。ウィジェットを知らない）
2. `search_panel.py` … 検索パネルの見た目と、ヒット一覧の持ち回り
   （どのタブを検索するかは知らない）
3. このモジュール … 上の 2 つとタブ（``self.editors()``）をつなぐ。
   「全タブを回して検索する」「ヒットへジャンプする」「置換する」
   「本文が変わったら検索し直す」がここ

**このモジュールを触るときの注意**（過去に不具合として出たもの）:

- 検索結果は「検索した時点」ではなく「今の本文」に対して正しくなければ
  ならない。本文の編集・タブの並べ替え・ウィンドウ間移動のあとに古い
  ``SearchMatch`` で置換すると、本文の関係ない場所を書き換えてしまう。
  最後の歯止めが :meth:`SearchReplaceMixin._apply_replacement` の
  ``expected`` 照合なので、これを外さないこと。
- インクリメンタルサーチはしない（実機フィードバックで、実際のファイルでは
  1 文字ごとの全タブ走査が重すぎたため）。検索が走るのは「利用者が明示的に
  検索したとき」と「既に検索した語で検索し直すとき」だけ。

``MainWindow`` にミックスインとして混ぜて使う。``self.tabs``・
``self.editors()``・``self.current_editor()``・``self._tab_title()``・
``self.open_windows()`` に依存する。
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor

from .dialogs import prompt_int
from .editor import center_cursor_vertically, document_text
from .search_panel import NEWLINE_GLYPH, SearchMatch, SearchPanel
from .settings import save_search_input_lines
from .textsearch import find_matches, fold_case, line_number_at, line_snippet

#: 本文が編集されてから検索し直すまでの待ち時間 (ms)。連続入力の途中で
#: 何度も全タブを走査しないよう、最後の入力からこれだけ待ってまとめて行う。
SEARCH_REFRESH_DELAY_MS = 200

#: 検索し直すのにこれ以上かかったら「重い検索」とみなし、本文の編集への
#: 追随をやめる (ms)。1 文字打つたびに固まるのを防ぐための安全弁。
SLOW_SEARCH_REFRESH_MS = 300

#: 1 タブあたり、本文中にハイライトする検索ヒットの上限。ありふれた語で
#: 何千件もヒットすると ``QTextEdit.ExtraSelection`` を組み立てて
#: ``setExtraSelections()`` に渡すだけで重くなるため、ハイライトだけを
#: 間引く。**件数表示・ジャンプ・置換は全件を対象にしたままにすること**
#: （間引きが検索結果の正しさに影響してはいけない）。
MAX_HIGHLIGHTED_MATCHES = 500


def _selection_matches(cursor: QTextCursor, expected: str) -> bool:
    """カーソルが選択している文字列が、検索語 ``expected`` と同じかを返す。

    ``QTextCursor.selectedText()`` は改行を U+2029（段落区切り）で返すため、
    本文と同じ ``\\n`` に直してから比べる。大小文字の扱いは検索
    (:meth:`SearchReplaceMixin.search_all_tabs`) と同じ ``fold_case`` に揃える。
    """
    selected = cursor.selectedText().replace("\u2029", "\n")
    return fold_case(selected) == fold_case(expected)


class SearchReplaceMixin:
    """ウィンドウ内の全タブを横断する検索 (Ctrl+F)・置換 (Ctrl+H)。"""

    def _setup_search_panel(self) -> None:
        """検索パネルと、検索し直し用のタイマーを用意する。

        ``MainWindow.__init__`` から、タブ (``self.tabs``) を作ったあとに
        1 度だけ呼ぶこと。
        """
        # メインウィンドウには埋め込まず、独立したフローティングウィンドウにする
        # （EmEditor のように、置換モードでは欄が増えて縦に伸びる）。
        self.search_panel = SearchPanel(self)
        self.search_panel.setWindowFlag(Qt.WindowType.Tool, True)
        self.search_panel.setWindowTitle("検索")
        self.search_panel.hide()
        self.search_panel.search_requested.connect(self._run_search)
        self.search_panel.match_activated.connect(self._on_search_match_activated)
        self.search_panel.replace_one_requested.connect(self._replace_current_match)
        self.search_panel.replace_all_requested.connect(self._replace_all_matches)
        self.search_panel.closed.connect(self.close_search_panel)

        # 検索結果は「いつ検索したか」ではなく「今の本文」に対して正しく
        # なければならない（古い結果のまま置換すると、本文の別の場所を
        # 書き換えてしまう）。本文が編集されたら検索し直す。1 文字ごとに
        # 全タブを走査すると入力が重くなるので、少しだけ待ってまとめて
        # 実行する。
        self._search_refresh_timer = QTimer(self)
        self._search_refresh_timer.setSingleShot(True)
        self._search_refresh_timer.setInterval(SEARCH_REFRESH_DELAY_MS)
        self._search_refresh_timer.timeout.connect(self._refresh_search_results)
        #: 置換の実行中は、自分で加えた変更で検索し直さないよう止めておく。
        self._suspend_search_refresh = False
        #: 本文の編集に追随して検索し直すかどうか（重い検索では自動で降りる。
        #: :meth:`_run_search` を参照）。
        self._auto_refresh_on_edit = True

        # タブの並べ替えでも SearchMatch.editor_index がずれるので検索し直す。
        self.tabs.tabMoved.connect(self._on_tab_set_changed)

    # ------------------------------------------------------------------
    # 機能 6: ウィンドウ内の全タブを横断する検索 (Ctrl+F)
    # ------------------------------------------------------------------
    def show_search_panel(self) -> None:
        """検索パネルを検索専用モードで開く。選択中の文字列があれば検索欄に流し込む。"""
        self.search_panel.set_replace_mode(False)
        self._open_search_panel()

    def show_replace_panel(self) -> None:
        """検索パネルを置換モード（機能 7）で開く。"""
        self.search_panel.set_replace_mode(True)
        self._open_search_panel()

    def _open_search_panel(self) -> None:
        editor = self.current_editor()
        if editor is not None:
            # QTextCursor.selectedText() は改行を U+2029 で返すので、
            # 複数行選択でもそのまま検索欄に渡せるよう SearchPanel 側で変換する。
            self.search_panel.set_initial_text(editor.textCursor().selectedText())
        self.search_panel.show()
        # search_panel は独立したフローティングウィンドウなので、
        # focus_input() で Qt 内部のフォーカスを合わせるだけでは
        # OS 側のキーボード入力先が本体ウィンドウのままになることがある。
        # raise_()/activateWindow() で実際にアクティブウィンドウにする。
        self.search_panel.raise_()
        self.search_panel.activateWindow()
        self.search_panel.focus_input()
        # パネルを開くこと自体は利用者の明示的な操作なので、ここでは
        # 1 回だけ検索してよい（選択中の文字列を流し込んだ場合に、すぐ
        # 件数が出る）。以降はタイプしても検索しない。
        self._run_search(self.search_panel.query())

    def close_search_panel(self) -> None:
        """検索パネルを閉じて、ハイライトを消し、現在のタブへフォーカスを戻す。"""
        self._search_refresh_timer.stop()
        self.search_panel.hide()
        self._clear_match_highlights()
        editor = self.current_editor()
        if editor is not None:
            editor.setFocus()

    def _prompt_search_input_lines(self) -> None:
        """検索欄・置換欄の表示行数をダイアログで尋ねる（実機フィードバックにより追加）。"""
        value = prompt_int(
            self,
            "検索欄の高さ",
            "検索欄・置換欄の表示行数:",
            self.search_panel.input_visible_lines(),
            1,
            30,
        )
        if value is None:
            return
        self.set_search_input_lines(value)

    def set_search_input_lines(self, lines: int) -> None:
        """検索欄・置換欄の表示行数を変える。

        テーマ等と同じく、アプリ全体で共有する設定として扱う（開いている
        全ウィンドウの検索パネルへ即座に反映し、QSettings で次回起動時
        にも復元する）。
        """
        for window in self.open_windows():
            window.search_panel.set_input_visible_lines(lines)
        save_search_input_lines(lines)

    def _run_search(self, query: str) -> None:
        """``query`` で全タブを検索し、件数表示とハイライトを更新する。

        検索欄への入力では呼ばれない（インクリメンタルサーチはやめた）。
        呼ばれるのは「利用者が明示的に検索したとき」（Enter /「検索」
        ボタン / パネルを開いたとき / 置換の直前）と、「既に検索した語で
        検索し直すとき」（本文の編集・タブの並べ替えへの追随）だけ。
        """
        started = time.perf_counter()
        self.search_panel.set_matches(self.search_all_tabs(query), query=query)
        self._update_match_highlights()
        elapsed_ms = (time.perf_counter() - started) * 1000

        # 検索し直すのに時間がかかるとき（とても大きなファイル、ありふれた
        # 語で何万件もヒットする場合など）は、本文の編集に追随するのをやめる。
        # そのまま続けると 1 文字打つたびに固まってしまうため。追随しなく
        # なっても、古い結果のまま置換して本文を壊すことは
        # :meth:`_apply_replacement` の確認が防ぐ。検索語を打ち直せば
        # （そのときの所要時間で測り直して）また追随するようになる。
        self._auto_refresh_on_edit = elapsed_ms <= SLOW_SEARCH_REFRESH_MS

    def _on_editor_text_changed(self) -> None:
        """どれかのタブの本文が変わった。検索結果を作り直す（少し待ってから）。"""
        if self._suspend_search_refresh or not self.search_panel.isVisible():
            return
        if not self._auto_refresh_on_edit:
            return
        self._search_refresh_timer.start()

    def _on_tab_set_changed(self, *_args) -> None:
        """タブの増減・並べ替え・ウィンドウ間移動があった。

        :attr:`SearchMatch.editor_index` は「このウィンドウの何番目のタブか」
        なので、タブの並びが変わると古い結果は別のタブを指してしまう。
        待たずにその場で検索し直す。
        """
        if self._suspend_search_refresh or not self.search_panel.isVisible():
            return
        self._refresh_search_results()

    def _refresh_search_results(self) -> None:
        """今の本文・今のタブの並びで検索し直し、件数表示とハイライトを更新する。

        検索し直すのは **最後に実際に検索した語**（``searched_query()``）で
        あって、検索欄の今の内容ではない。打ちかけの語で勝手に全タブを
        検索してしまうと、インクリメンタルサーチをやめた意味が無くなる。
        """
        self._search_refresh_timer.stop()
        if not self.search_panel.isVisible():
            return
        query = self.search_panel.searched_query()
        if query is None:
            return
        self._run_search(query)

    def _update_match_highlights(self) -> None:
        """検索でヒットした箇所を、全タブの本文中にハイライト表示する。"""
        self._apply_match_highlights(
            self.search_panel.matches(), self.search_panel.current_match()
        )

    def _apply_match_highlights(
        self, matches: list[SearchMatch], current: SearchMatch | None
    ) -> None:
        matches_by_editor: dict[int, list[tuple[int, int]]] = {}
        for match in matches:
            matches_by_editor.setdefault(match.editor_index, []).append(
                (match.position, match.length)
            )

        for index, editor in enumerate(self.editors()):
            ranges = matches_by_editor.get(index, [])
            current_range: tuple[int, int] | None = None
            if current is not None and current.editor_index == index:
                current_range = (current.position, current.length)
                if current_range in ranges:
                    ranges = [r for r in ranges if r != current_range]
            # ヒットが極端に多いときはハイライトだけ間引く（件数表示・
            # ジャンプ・置換は全件のまま）。現在指している 1 件は
            # current_range として別に渡すので、上限に関わらず必ず光る。
            if len(ranges) > MAX_HIGHLIGHTED_MATCHES:
                ranges = ranges[:MAX_HIGHLIGHTED_MATCHES]
            editor.set_match_highlights(current_range, ranges)

    def _clear_match_highlights(self) -> None:
        for editor in self.editors():
            editor.set_match_highlights(None, [])

    def search_all_tabs(self, query: str) -> list[SearchMatch]:
        """このウィンドウの全タブを対象に、大小文字を区別せず検索する。

        検索語が改行を含む場合、マッチも複数行にまたがる（EmEditor の
        複数行検索と同様）。そのため行ごとではなく、文書全体を 1 つの
        文字列として扱い、文字位置 (``position``) ベースでマッチを返す。

        返す ``position`` / ``length`` は **Qt の文書内位置（UTF-16 コード単位）**
        で、そのまま ``QTextCursor.setPosition()`` に渡せる。検索そのものは
        Python の文字列で行うため、数え方の違いを ``textsearch`` の関数で
        吸収している（詳しくは ``editor_app/textsearch.py`` を参照）。
        ここでやるのはタブを回して、その結果を :class:`SearchMatch` に
        詰め替えることだけ。
        """
        matches: list[SearchMatch] = []
        for index, editor in enumerate(self.editors()):
            title = self._tab_title(editor)
            text = document_text(editor)
            for span in find_matches(text, query):
                matches.append(
                    SearchMatch(
                        editor_index=index,
                        tab_title=title,
                        position=span.position,
                        length=span.length,
                        line=line_number_at(text, span.py_position),
                        snippet=line_snippet(
                            text, span.py_position, span.py_length, NEWLINE_GLYPH
                        ),
                    )
                )
        return matches

    def _on_search_match_activated(self, match: SearchMatch) -> None:
        self.jump_to_match(match)
        self._update_match_highlights()

    def jump_to_match(self, match: SearchMatch) -> None:
        """該当タブへ切り替え、マッチした範囲（複数行にまたがってもよい）を
        選択してスクロールする。"""
        editors = self.editors()
        if not (0 <= match.editor_index < len(editors)):
            return
        editor = editors[match.editor_index]
        self.tabs.setCurrentWidget(editor)

        cursor = editor.textCursor()
        cursor.setPosition(match.position)
        cursor.setPosition(
            match.position + match.length, QTextCursor.MoveMode.KeepAnchor
        )
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        center_cursor_vertically(editor)
        editor.setFocus()

    # ------------------------------------------------------------------
    # 機能 7: ウィンドウ内の全タブを横断する検索・置換 (Ctrl+H)
    # ------------------------------------------------------------------
    def _ensure_search_current(self) -> None:
        """検索欄の語でまだ検索していなければ、置換の前に検索しておく。

        検索は明示的な操作でしか走らないので、「検索語を打って、置換語を
        打って、いきなり全置換を押す」という自然な操作では検索結果が空の
        ままになってしまう。置換ボタンも「明示的な操作」の 1 つとして
        扱い、その場で検索してから置換する。
        """
        if self.search_panel.is_query_pending():
            self._run_search(self.search_panel.query())

    def _replace_current_match(self) -> None:
        """検索パネルで現在選択されている 1 件だけを置換する。

        検索してから本文が変わっていた場合（``_apply_replacement`` が
        False を返した場合）は、何も置換せずに検索し直してその旨を伝える。
        古い位置のまま書き換えると、本文の関係ない場所が壊れてしまうため。
        """
        self._ensure_search_current()
        match = self.search_panel.current_match()
        if match is None:
            return
        query = self.search_panel.query()
        self._suspend_search_refresh = True
        try:
            replaced = self._apply_replacement(
                match, self.search_panel.replace_text(), expected=query
            )
        finally:
            self._suspend_search_refresh = False
        # 置換すると内容が変わるので検索し直す（残りマッチの位置もずれるため）。
        self._search_refresh_timer.stop()
        self._run_search(query)
        if not replaced:
            self.search_panel.show_stale_notice()

    def _replace_all_matches(self) -> None:
        """検索語に一致する全件を一括で置換し、置換件数をパネルに表示する。

        1 つのタブ内で複数件を置換しても、そのタブでは Ctrl+Z 一回で
        まとめて元に戻せるように、タブ（エディタ）ごとに
        ``beginEditBlock()``/``endEditBlock()`` で 1 つの Undo 単位にまとめる。
        """
        self._ensure_search_current()
        query = self.search_panel.query()
        matches = self.search_all_tabs(query)
        if not matches:
            return

        replacement = self.search_panel.replace_text()
        editors = self.editors()

        matches_by_editor: dict[int, list[SearchMatch]] = {}
        for match in matches:
            matches_by_editor.setdefault(match.editor_index, []).append(match)

        replaced_count = 0
        # 置換で本文が変わるたびに検索し直さないよう、この間は止めておく
        # （最後にまとめて 1 回だけ検索し直す）。
        self._suspend_search_refresh = True
        try:
            for editor_index, editor_matches in matches_by_editor.items():
                if not (0 <= editor_index < len(editors)):
                    continue
                editor = editors[editor_index]
                cursor = editor.textCursor()
                cursor.beginEditBlock()
                # 同じタブ内で先に置換すると後続マッチの position がずれるので、
                # 文書の後ろ側 (position が大きい方) から処理する。
                for match in sorted(
                    editor_matches, key=lambda m: m.position, reverse=True
                ):
                    if self._apply_replacement(match, replacement, expected=query):
                        replaced_count += 1
                cursor.endEditBlock()
        finally:
            self._suspend_search_refresh = False

        self._search_refresh_timer.stop()
        self._run_search(self.search_panel.query())
        self.search_panel.show_replacement_summary(replaced_count)
        # 検索語 (query) はもう本文に無いはずなので、代わりに置換後の文字列を
        # ハイライトして、どこが変わったか一目で分かるようにする。
        self._apply_match_highlights(self.search_all_tabs(replacement), None)

    def _apply_replacement(
        self, match: SearchMatch, replacement: str, *, expected: str | None = None
    ) -> bool:
        """1 件のマッチをカーソル選択→ ``insertText`` で置換する（Undo 可能）。

        ``expected`` を渡すと、置換する直前に「その位置に本当に検索語が
        あるか」を確かめ、違っていれば **何もせずに False を返す**。
        検索してから本文が編集されていたり、タブが並べ替えられていたりすると
        ``match`` の指す位置は別の文字列を指しているので、その状態で
        書き換えると本文の関係ない場所が壊れてしまう。その最後の歯止め。

        置換したら True、位置がずれていて置換しなかったら False。
        """
        editors = self.editors()
        if not (0 <= match.editor_index < len(editors)):
            return False
        editor = editors[match.editor_index]
        cursor = editor.textCursor()
        cursor.setPosition(match.position)
        cursor.setPosition(
            match.position + match.length, QTextCursor.MoveMode.KeepAnchor
        )
        if expected is not None and not _selection_matches(cursor, expected):
            return False
        cursor.insertText(replacement)
        return True
