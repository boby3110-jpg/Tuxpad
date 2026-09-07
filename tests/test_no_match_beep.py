"""検索・置換で「1 件も見つからなかった」ときに音を鳴らす（引き継ぎ ⑪）。

**この開発環境には音のデバイスが無い**ので、実際に鳴ったかどうかは
確かめられない。ここで見張るのは
``editor_app.search_replace.beep`` を **呼んだか／呼ばなかったか** だけ
（本当に音が出るかは実機での確認事項）。

鳴らす条件を「利用者が明示的に検索・置換したとき」に限っているのが肝で、
**鳴らさない側のテストの方が大事**：本文の編集に追随した自動の検索し直しで
鳴ってしまうと、打っている間ずっと鳴り続ける。
"""

from __future__ import annotations

import pytest

from editor_app import search_replace
from editor_app.main_window import MainWindow
from editor_app.settings import load_beep_on_no_match, save_beep_on_no_match


@pytest.fixture
def beeps(monkeypatch) -> list[None]:
    """``beep()`` の呼び出しを数える（本物の警告音は鳴らさない）。"""
    calls: list[None] = []
    monkeypatch.setattr(search_replace, "beep", lambda: calls.append(None))
    return calls


def edit_text(editor, text: str) -> None:
    """本文を書き換える (setPlainText は変更フラグが立たないため使わない)。"""
    editor.selectAll()
    editor.insertPlainText(text)


def make_tabs(window: MainWindow, *texts: str) -> None:
    for text in texts:
        window.new_file()
        edit_text(window.current_editor(), text)


def search(window: MainWindow, query: str) -> None:
    """検索欄に語を入れて、利用者が Enter を押したのと同じ経路で検索する。"""
    window.search_panel._input.setPlainText(query)
    window.search_panel.run_search()


# ----------------------------------------------------------------------
# 鳴る場面
# ----------------------------------------------------------------------
def test_beeps_when_search_finds_nothing(window: MainWindow, beeps) -> None:
    make_tabs(window, "ここには何も無い\n")
    window.show_search_panel()

    search(window, "見つからない語")

    assert len(beeps) == 1
    # 件数欄の表示（これまでの知らせ方）も残っていること。
    assert window.search_panel._count_label.text() == "見つかりません"


def test_beeps_when_replace_all_finds_nothing(window: MainWindow, beeps) -> None:
    make_tabs(window, "ここには何も無い\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("見つからない語")
    window.search_panel._replace_input.setPlainText("置換後")

    window.search_panel._replace_all_button.click()

    assert len(beeps) == 1


def test_beeps_when_replace_one_finds_nothing(window: MainWindow, beeps) -> None:
    make_tabs(window, "ここには何も無い\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("見つからない語")
    window.search_panel._replace_input.setPlainText("置換後")

    window.search_panel._replace_button.click()

    assert len(beeps) == 1


def test_beeps_in_every_window_independently(make_window, beeps) -> None:
    """ウィンドウが複数あっても、検索したウィンドウで 1 回だけ鳴る。"""
    first = make_window()
    second = make_window()
    make_tabs(first, "あ\n")
    make_tabs(second, "い\n")

    first.show_search_panel()
    search(first, "無い語")

    assert len(beeps) == 1


# ----------------------------------------------------------------------
# 鳴らない場面（こちらの方が大事）
# ----------------------------------------------------------------------
def test_does_not_beep_when_search_finds_something(window: MainWindow, beeps) -> None:
    make_tabs(window, "hello world\n")
    window.show_search_panel()

    search(window, "hello")

    assert beeps == []


def test_does_not_beep_for_empty_query(window: MainWindow, beeps) -> None:
    """検索語が空なら、ヒット 0 件でも鳴らさない（当たり前の 0 件のため）。

    ``show_search_panel()`` は開いた直後に 1 回検索する。検索語が空の
    まま鳴らしてしまうと、**Ctrl+F を押すたびに鳴る**ことになる。
    """
    make_tabs(window, "hello world\n")

    window.show_search_panel()
    # 空欄のまま検索を要求されても鳴らない（明示的な検索の入口を直に叩く）。
    window._on_search_requested("")

    assert beeps == []
    assert window.search_panel.matches() == []


def test_does_not_beep_when_setting_is_off(window: MainWindow, beeps) -> None:
    make_tabs(window, "ここには何も無い\n")
    save_beep_on_no_match(False)
    window.show_search_panel()

    search(window, "見つからない語")

    assert beeps == []
    # 音を切っても、件数欄の知らせは今までどおり出ること。
    assert window.search_panel._count_label.text() == "見つかりません"


def test_does_not_beep_when_refreshing_after_edit(window: MainWindow, beeps) -> None:
    """本文を編集して検索結果が 0 件になっても鳴らさない。

    ここで鳴らすと、検索語を含む行を消していく間ずっと鳴り続ける。
    """
    make_tabs(window, "hello world\n")
    window.show_search_panel()
    search(window, "hello")
    assert beeps == []

    # 検索語を本文から消す → 追随して検索し直され、0 件になる。
    edit_text(window.current_editor(), "もう無い\n")
    window._refresh_search_results()

    assert window.search_panel.matches() == []
    assert beeps == []


def test_does_not_beep_when_reopening_the_panel(window: MainWindow, beeps) -> None:
    """前の検索語が残ったまま Ctrl+F を押し直しただけでは鳴らさない。

    ``_open_search_panel()`` は開いた直後に 1 回検索する（選択中の語を
    流し込んだときにすぐ件数が出るように）。パネルを開くのは「検索して」
    という指示ではなく「これから検索する」合図なので、開いた瞬間に
    鳴るのは驚かせるだけ。
    """
    make_tabs(window, "hello world\n")
    window.show_search_panel()
    search(window, "hello")
    window.close_search_panel()
    # 検索語を本文から消しておく（次に開いたときの検索は 0 件になる）。
    edit_text(window.current_editor(), "もう無い\n")
    assert beeps == []

    window.show_search_panel()  # 選択は無いので検索欄は "hello" のまま

    assert window.search_panel.matches() == []
    assert beeps == []


def test_does_not_beep_after_replacing_the_last_match(window: MainWindow, beeps) -> None:
    """最後の 1 件を置換して 0 件になっても鳴らさない（置換は成功している）。"""
    make_tabs(window, "hello\n")
    window.show_replace_panel()
    window.search_panel._input.setPlainText("hello")
    window.search_panel._replace_input.setPlainText("bye")

    window.search_panel._replace_all_button.click()

    assert "bye" in window.current_editor().toPlainText()
    assert window.search_panel.matches() == []
    assert beeps == []


# ----------------------------------------------------------------------
# 設定（保存・メニューの印）
# ----------------------------------------------------------------------
def test_setting_defaults_to_on() -> None:
    assert load_beep_on_no_match() is True


def test_setting_round_trips() -> None:
    save_beep_on_no_match(False)
    assert load_beep_on_no_match() is False
    save_beep_on_no_match(True)
    assert load_beep_on_no_match() is True


def test_menu_action_reflects_saved_setting(make_window) -> None:
    save_beep_on_no_match(False)
    win = make_window()
    assert win.action_beep_on_no_match.isChecked() is False


def test_menu_action_toggles_setting_in_every_window(make_window) -> None:
    """アプリ全体で共有する設定なので、他のウィンドウの印も揃うこと。"""
    first = make_window()
    second = make_window()
    assert first.action_beep_on_no_match.isChecked() is True

    first.action_beep_on_no_match.trigger()

    assert load_beep_on_no_match() is False
    assert first.action_beep_on_no_match.isChecked() is False
    assert second.action_beep_on_no_match.isChecked() is False


def test_setting_takes_effect_without_reopening_the_panel(
    window: MainWindow, beeps
) -> None:
    """メニューで切った瞬間から鳴らなくなること（開き直さなくてよい）。"""
    make_tabs(window, "ここには何も無い\n")
    window.show_search_panel()
    search(window, "無い語1")
    assert len(beeps) == 1

    window.action_beep_on_no_match.trigger()  # ON → OFF
    search(window, "無い語2")

    assert len(beeps) == 1


def test_beep_is_skipped_without_application(monkeypatch) -> None:
    """``QApplication`` がまだ／もう無い場面で呼ばれても、落ちも鳴りもしない。"""

    class _NoApplication:
        @staticmethod
        def instance():
            return None

        @staticmethod
        def beep():
            raise AssertionError("QApplication が無いのに鳴らそうとした")

    monkeypatch.setattr(search_replace, "QApplication", _NoApplication)

    search_replace.beep()  # 例外が出なければよい


def test_beep_uses_qapplication_beep(monkeypatch) -> None:
    """設定が ON でヒットが無ければ、最後は ``QApplication.beep()`` に届く。

    ここまで見ておかないと、``beep()`` を差し替えている他のテストは
    **中身が空でも全部緑**になる（音が出るかは実機でしか確かめられないが、
    「呼ぶところまでは繋がっている」ことはここで固定できる）。
    """
    calls: list[None] = []

    class _FakeApplication:
        @staticmethod
        def instance():
            return object()

        @staticmethod
        def beep():
            calls.append(None)

    monkeypatch.setattr(search_replace, "QApplication", _FakeApplication)

    search_replace.beep()

    assert len(calls) == 1
