"""全置換をバッチに割って進める仕組み（引き継ぎ ⑨）のテスト。

実機で「2 万件ヒットする置換を実行すると数秒〜数十秒 応答なしになる」と
報告された。原因は 2 つで、どちらもここと `test_textsearch.py` で見張る。

1. 検索結果を組み立てるとき、行番号を 1 件ごとに先頭から数え直していた
   （`LineNumbers` に変更。`test_textsearch.py` 側で見張る）
2. 全置換が最後まで一気に走り、その間 Qt に制御が戻らなかった
   （`REPLACE_BATCH_SIZE` 件ごとに戻すよう変更。ここで見張る）

**このモジュールの要点は「途中で制御を戻しても本文が壊れない」こと。**
制御を戻すと、その隙にタイマー等が動く（＝本文が変わりうる）ので、
置換の一件ごとの確認や、Undo の単位の閉じ忘れが致命的になる。
"""

from __future__ import annotations

import pytest

from editor_app import search_replace
from editor_app.main_window import MainWindow


class FakeProgress:
    """進み具合の窓の代わり。Qt の窓を出さずに呼ばれ方だけを記録する。

    ``cancel_at`` を渡すと、その回の :meth:`advance` で「中止」が押された
    ことにする（1 始まり）。
    """

    def __init__(self, total: int, *, cancel_at: int | None = None) -> None:
        self.total = total
        self.values: list[int] = []
        self.closed = False
        self._cancel_at = cancel_at
        self._canceled = False

    def advance(self, value: int) -> bool:
        self.values.append(value)
        if self._cancel_at is not None and len(self.values) >= self._cancel_at:
            self._canceled = True
        return not self._canceled

    def was_canceled(self) -> bool:
        return self._canceled

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def small_batches(monkeypatch):
    """少ない件数でもバッチに割られるよう、しきい値を下げる。"""
    monkeypatch.setattr(search_replace, "REPLACE_BATCH_SIZE", 2)
    monkeypatch.setattr(search_replace, "REPLACE_PROGRESS_MIN_MATCHES", 3)


def use_fake_progress(window: MainWindow, monkeypatch, **kwargs) -> list[FakeProgress]:
    """``_make_replace_progress`` を FakeProgress に差し替え、作られた分を返す。"""
    created: list[FakeProgress] = []

    def factory(total: int):
        if total < search_replace.REPLACE_PROGRESS_MIN_MATCHES:
            return None
        progress = FakeProgress(total, **kwargs)
        created.append(progress)
        return progress

    monkeypatch.setattr(window, "_make_replace_progress", factory)
    return created


def make_tab(window: MainWindow, text: str) -> None:
    window.new_file()
    editor = window.current_editor()
    editor.selectAll()
    editor.insertPlainText(text)


def start_replace(window: MainWindow, query: str, replacement: str) -> None:
    window.show_replace_panel()
    window.search_panel._input.setPlainText(query)
    window.search_panel._replace_input.setPlainText(replacement)
    window.search_panel.run_search()


# ----------------------------------------------------------------------
# 少量のときは今までどおり（窓を出さない）
# ----------------------------------------------------------------------
def test_small_replacement_does_not_show_progress(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    make_tab(window, "対象 と 対象\n")
    created = use_fake_progress(window, monkeypatch)
    start_replace(window, "対象", "置換後")

    window._replace_all_matches()

    assert created == []  # 2 件（しきい値 3 件未満）なので窓は出さない
    assert window.current_editor().toPlainText() == "置換後 と 置換後\n"


def test_make_replace_progress_returns_none_below_threshold(
    window: MainWindow,
) -> None:
    """本物の生成メソッドも、少量なら窓を作らない（Qt の窓を出さない）。"""
    assert window._make_replace_progress(search_replace.REPLACE_PROGRESS_MIN_MATCHES - 1) is None


# ----------------------------------------------------------------------
# 大量のときはバッチに割って進める
# ----------------------------------------------------------------------
def test_large_replacement_yields_to_qt_in_batches(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    make_tab(window, "対象\n" * 10)
    created = use_fake_progress(window, monkeypatch)
    start_replace(window, "対象", "置換後")

    window._replace_all_matches()

    assert window.current_editor().toPlainText() == "置換後\n" * 10
    (progress,) = created
    assert progress.total == 10
    # 2 件ごとに制御を戻している（10 件なら 5 回）。
    assert progress.values == [2, 4, 6, 8, 10]
    assert progress.closed


def test_progress_is_closed_even_if_replacement_raises(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    """途中で例外が出ても、窓は閉じ、後始末の印も戻す。

    閉じ忘れると、モーダルの窓が残って**アプリ全体が操作できなくなる**。
    """
    make_tab(window, "対象\n" * 10)
    created = use_fake_progress(window, monkeypatch)
    start_replace(window, "対象", "置換後")

    def boom(*args, **kwargs):
        raise RuntimeError("置換の途中で落ちた")

    monkeypatch.setattr(window, "_apply_replacement", boom)

    with pytest.raises(RuntimeError):
        window._replace_all_matches()

    (progress,) = created
    assert progress.closed
    assert window._suspend_search_refresh is False
    assert window._long_operation_active is False


def test_replacement_across_tabs_counts_all_batches(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    """タブをまたいでも、進み具合はタブごとに 0 へ戻らず通しで進む。"""
    make_tab(window, "対象\n" * 5)
    make_tab(window, "対象\n" * 5)
    created = use_fake_progress(window, monkeypatch)
    start_replace(window, "対象", "置換後")

    window._replace_all_matches()

    (progress,) = created
    assert progress.values == [2, 4, 6, 8, 10]
    # 最初から開いている空のタブは対象外なので、本文を入れた 2 つを見る。
    for editor in window.editors()[-2:]:
        assert editor.toPlainText() == "置換後\n" * 5


# ----------------------------------------------------------------------
# 「中止」
# ----------------------------------------------------------------------
def test_cancel_stops_replacing_and_keeps_what_was_done(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    """中止したら、そこでやめる。**それまでの置換は残す**（元に戻さない）。"""
    make_tab(window, "対象\n" * 10)
    created = use_fake_progress(window, monkeypatch, cancel_at=2)
    start_replace(window, "対象", "置換後")

    window._replace_all_matches()

    text = window.current_editor().toPlainText()
    # 後ろ側から置換するので、残るのは前半。
    assert text == "対象\n" * 6 + "置換後\n" * 4
    (progress,) = created
    assert progress.values == [2, 4]  # 2 回目で中止＝それ以降は進めない
    assert progress.closed


def test_cancel_reports_the_partial_count(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    """件数だけ出すと「残りも置換できた」と誤解されるので、中止も伝える。"""
    make_tab(window, "対象\n" * 10)
    use_fake_progress(window, monkeypatch, cancel_at=2)
    start_replace(window, "対象", "置換後")

    window._replace_all_matches()

    assert window.search_panel._count_label.text() == "4 件を置換しました（中止しました）"


def test_cancel_leaves_the_remaining_matches_searchable(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    """中止後は、置換し残した分が検索結果として出ている（どこが残ったか分かる）。"""
    make_tab(window, "対象\n" * 10)
    use_fake_progress(window, monkeypatch, cancel_at=2)
    start_replace(window, "対象", "置換後")

    window._replace_all_matches()

    assert len(window.search_panel.matches()) == 6
    assert window.search_panel.searched_query() == "対象"


def test_cancel_in_one_tab_leaves_the_other_tabs_untouched(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    """中止は「このタブの残り」だけでなく、**まだ手を付けていないタブ**にも効く。

    次のタブへ進んでしまうと、中止したつもりが別のファイルを書き換える。
    """
    make_tab(window, "対象\n" * 6)
    make_tab(window, "対象\n" * 6)
    first, second = window.editors()[-2:]
    use_fake_progress(window, monkeypatch, cancel_at=1)
    start_replace(window, "対象", "置換後")

    window._replace_all_matches()

    # 最初のタブは 2 件だけ置換されて止まり、次のタブは 1 件も置換されない。
    assert first.toPlainText() == "対象\n" * 4 + "置換後\n" * 2
    assert second.toPlainText() == "対象\n" * 6
    assert window.search_panel._count_label.text() == "2 件を置換しました（中止しました）"


def test_cancel_highlights_the_matches_that_are_still_there(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    """中止後のハイライトは「置換し残した検索語」を指す。

    全部置換したときは置換後の文字列を光らせる（どこが変わったか分かる）が、
    中止したときに同じことをすると、**まだ残っている分が見えなくなる**。
    """
    make_tab(window, "対象\n" * 10)
    editor = window.current_editor()
    use_fake_progress(window, monkeypatch, cancel_at=2)
    start_replace(window, "対象", "置換後")

    window._replace_all_matches()

    text = editor.toPlainText()
    highlighted = sorted(
        sel.cursor.selectionStart() for sel in editor.extraSelections()
    )
    # 置換し残した「対象」6 件の位置と一致する（置換後の 4 件ではない）。
    remaining = [i for i in range(len(text)) if text.startswith("対象", i)]
    assert highlighted == remaining
    assert len(highlighted) == 6


def test_cancel_closes_the_undo_block(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    """中止しても Undo の単位は閉じる。

    閉じ忘れると、そのあと利用者が打った文字まで同じ塊に吸い込まれ、
    Ctrl+Z 一回で**打った覚えのない範囲まで**巻き戻る。
    """
    make_tab(window, "対象\n" * 10)
    use_fake_progress(window, monkeypatch, cancel_at=2)
    start_replace(window, "対象", "置換後")

    window._replace_all_matches()

    editor = window.current_editor()
    after_cancel = editor.toPlainText()
    cursor = editor.textCursor()
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    editor.insertPlainText("あ")
    assert editor.toPlainText() == "あ" + after_cancel

    editor.undo()  # 打った 1 文字だけが戻る
    assert editor.toPlainText() == after_cancel


# ----------------------------------------------------------------------
# バッチ化しても Undo は「タブごとに 1 回」のまま
# ----------------------------------------------------------------------
def test_batched_replacement_is_still_one_undo_per_tab(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    """制御を戻す回数が増えても、Ctrl+Z 一回で全部戻ること。"""
    make_tab(window, "対象\n" * 10)
    use_fake_progress(window, monkeypatch)
    start_replace(window, "対象", "置換後")
    original = window.current_editor().toPlainText()

    window._replace_all_matches()
    assert window.current_editor().toPlainText() == "置換後\n" * 10

    window.current_editor().undo()
    assert window.current_editor().toPlainText() == original


# ----------------------------------------------------------------------
# 制御を戻している間に割り込まれても壊れない
# ----------------------------------------------------------------------
def test_external_reload_prompt_is_deferred_during_replacement(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    """置換の途中で「再読み込みしますか？」を出さない（後回しにする）。

    出してしまうと、本文が丸ごと入れ替わった上に残りの置換が走る。
    """
    make_tab(window, "対象\n" * 10)
    use_fake_progress(window, monkeypatch)
    start_replace(window, "対象", "置換後")

    asked: list[str] = []
    monkeypatch.setattr(
        window, "_maybe_reload_changed_file", lambda path: asked.append(str(path))
    )
    window._pending_reload_paths.add("/tmp/どこか.txt")
    window._long_operation_active = True

    window._process_pending_reloads()

    assert asked == []
    # 保留は消さずに残し、あとでやり直す。
    assert window._pending_reload_paths == {"/tmp/どこか.txt"}
    assert window._reload_check_timer.isActive()

    window._long_operation_active = False
    window._process_pending_reloads()
    assert asked == ["/tmp/どこか.txt"]


def test_text_edited_between_batches_does_not_corrupt_other_text(
    window: MainWindow, monkeypatch, small_batches
) -> None:
    """バッチの合間に本文が変わっても、関係ない場所を書き換えない。

    窓はモーダルなので利用者は打てないが、タイマー等は動く。その隙に本文が
    ずれたときの最後の歯止めが `_apply_replacement` の ``expected`` 照合で、
    バッチ化でその隙が現実に生まれた。
    """
    make_tab(window, "対象\n" * 10)
    editor = window.current_editor()

    created: list[FakeProgress] = []

    class MeddlingProgress(FakeProgress):
        """最初に制御が戻ったところで、本文の先頭に文字を差し込む。"""

        def advance(self, value: int) -> bool:
            if not self.values:
                cursor = editor.textCursor()
                cursor.setPosition(0)
                cursor.insertText("XY")
            return super().advance(value)

    def factory(total: int):
        progress = MeddlingProgress(total)
        created.append(progress)
        return progress

    monkeypatch.setattr(window, "_make_replace_progress", factory)
    start_replace(window, "対象", "置換後")

    window._replace_all_matches()

    text = editor.toPlainText()
    # 差し込んだ 2 文字は残っていて、本文の行数も変わっていない。
    assert text.startswith("XY")
    assert len(text.splitlines()) == 10
    # どの行も「対象」か「置換後」のまま（中途半端に削れた行が無い）。
    for line in text.splitlines():
        assert line.removeprefix("XY") in ("対象", "置換後")


# ----------------------------------------------------------------------
# 本物の窓（ProgressReporter）
# ----------------------------------------------------------------------
def test_real_progress_reporter_advances_and_closes(window: MainWindow) -> None:
    """本物の ProgressReporter が、出して・進めて・閉じられること。

    ヘッドレスでも固まらない（``exec()`` していない）ことの確認も兼ねる。
    """
    from editor_app.dialogs import ProgressReporter

    progress = ProgressReporter(window, title="全置換", label="置換中...", total=10)
    try:
        assert progress.advance(5) is True
        assert progress.was_canceled() is False
    finally:
        progress.close()
