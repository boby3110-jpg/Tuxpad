"""機能 2「開く」のテスト."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from editor_app import fileio
from editor_app.main_window import MainWindow


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    path = tmp_path / "sample.txt"
    path.write_text("あいうえお\n2行目\n", encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# fileio: 文字コード / 改行コードの判定
# ----------------------------------------------------------------------
def test_read_utf8(sample: Path) -> None:
    text, encoding, newline = fileio.read_text_file(sample)
    assert text == "あいうえお\n2行目\n"
    assert encoding == "utf-8"
    assert newline == "\n"


def test_read_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "bom.txt"
    path.write_bytes("日本語".encode("utf-8-sig"))
    text, encoding, _newline = fileio.read_text_file(path)
    assert text == "日本語"
    assert encoding == "utf-8-sig"


def test_read_utf16(tmp_path: Path) -> None:
    path = tmp_path / "u16.txt"
    path.write_bytes("日本語\n".encode("utf-16"))  # BOM 付き (LE)
    text, encoding, _newline = fileio.read_text_file(path)
    assert text == "日本語\n"
    assert encoding == "utf-16-le"


def test_crlf_is_detected_and_normalized(tmp_path: Path) -> None:
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"a\r\nb\r\n")
    text, _encoding, newline = fileio.read_text_file(path)
    assert newline == "\r\n"
    assert text == "a\nb\n"  # バッファ内では LF に統一する


def test_cr_only_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "cr.txt"
    path.write_bytes(b"a\rb\r")
    text, _encoding, newline = fileio.read_text_file(path)
    assert newline == "\r"
    assert text == "a\nb\n"


def test_no_newline_defaults_to_lf(tmp_path: Path) -> None:
    path = tmp_path / "oneline.txt"
    path.write_text("abc", encoding="utf-8")
    _text, _encoding, newline = fileio.read_text_file(path)
    assert newline == "\n"


def test_shift_jis_is_detected(tmp_path: Path) -> None:
    """Shift-JIS は機能 8 (charset-normalizer 併用) で判定できるようになった。

    charset-normalizer は数文字程度の短いバイト列だと統計的な判定が
    不安定になりやすい（実際に「日本語」3文字だけだと中国語系の文字コードに
    誤判定されることを確認済み）ため、判定精度を安定させられる程度の
    ある程度まとまった長さの日本語文で検証する。
    """
    path = tmp_path / "sjis.txt"
    text = "日本語のテキストファイルの文字コード自動判定を確認するためのテストです。" * 5
    path.write_bytes(text.encode("cp932"))

    decoded_text, encoding, _newline = fileio.read_text_file(path)

    assert decoded_text == text
    assert encoding == "cp932"


def test_totally_undetectable_encoding_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """charset-normalizer も含めてどの文字コードとしても推定できない場合はエラーにする。

    charset-normalizer は現実的なバイト列に対してはほぼ必ず何らかの
    候補を返す（後方互換の単バイト系コーデックまで含めて試すため）ので、
    「何も推定できなかった」場合の挙動は `from_bytes` をモックして検証する。
    """
    path = tmp_path / "garbage.bin"
    path.write_bytes(b"\x80\x81\x82\x83")  # 有効な UTF-8 ではないバイト列

    class _NoCandidate:
        def best(self):
            return None

    monkeypatch.setattr(fileio, "from_bytes", lambda _data: _NoCandidate())

    with pytest.raises(fileio.EncodingDetectionError):
        fileio.read_text_file(path)


# ----------------------------------------------------------------------
# fileio.resolve_path（タブが持つパスの正規化）
# ----------------------------------------------------------------------
def test_resolve_path_follows_symlinks_and_makes_it_absolute(tmp_path: Path) -> None:
    """リンク越し・相対のパスが、実体の絶対パスに揃う。

    ここが揃っていないと「同じファイルを開いているタブ」の判定
    （単純なパスの比較）が別物と答え、同じファイルのタブが 2 つできる。
    """
    real = tmp_path / "real.txt"
    real.write_text("本文\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    assert fileio.resolve_path(link) == real.resolve()
    assert fileio.resolve_path(str(link)) == real.resolve()


def test_resolve_path_returns_the_path_as_given_when_it_cannot_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正規化できなかったら、渡されたパスをそのまま返す（例外にしない）。"""

    def unresolvable(self, *_args, **_kwargs):
        raise OSError(36, "File name too long")

    monkeypatch.setattr(Path, "resolve", unresolvable)

    assert fileio.resolve_path(tmp_path / "a.txt") == tmp_path / "a.txt"
    assert fileio.resolve_path("relative/a.txt") == Path("relative/a.txt")


def test_resolve_path_survives_a_symlink_loop(tmp_path: Path) -> None:
    """輪になったシンボリックリンクでも、例外を投げずに諦める。

    ``Path.resolve()`` は Python 3.11 / 3.12 では、リンクが輪になっていると
    ``OSError`` ではなく ``RuntimeError("Symlink loop from ...")`` を投げる
    （3.13 以降は投げない）。``OSError`` だけを握っていると、この 1 行が
    :meth:`open_path` の try より**手前**にあるせいで、そのまま外へ飛ぶ。
    """
    a = tmp_path / "loop_a"
    b = tmp_path / "loop_b"
    a.symlink_to(b)
    b.symlink_to(a)

    assert fileio.resolve_path(a) == a


def test_opening_a_symlink_loop_reports_it_instead_of_crashing(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """輪になったリンクを開こうとしたら、「開けません」で済ませる。

    読み込みの失敗と違い、パスの正規化はエラーを捕まえている try の外なので、
    ここで例外が漏れると**ダイアログすら出ない**（Qt のスロットの中で
    投げっぱなしになる）。ファイルマネージャからの D&D や
    コマンドライン引数で渡されうる経路。
    """
    a = tmp_path / "loop_a"
    b = tmp_path / "loop_b"
    a.symlink_to(b)
    b.symlink_to(a)

    shown: list[str] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda _parent, _icon, title, *args, **kwargs: shown.append(title),
    )

    before = window.tabs.count()
    assert window.open_path(a) is None
    assert shown == ["開けません"]
    assert window.tabs.count() == before


# ----------------------------------------------------------------------
# MainWindow.open_path
# ----------------------------------------------------------------------
def test_open_reuses_empty_startup_tab(window: MainWindow, sample: Path) -> None:
    assert window.tabs.count() == 1
    editor = window.open_path(sample)
    assert editor is not None
    assert window.tabs.count() == 1  # 空タブが使い回される
    assert editor.toPlainText() == "あいうえお\n2行目\n"
    assert editor.path == sample.resolve()
    assert editor.encoding == "utf-8"
    assert not editor.is_modified


def test_open_adds_new_tab_when_current_is_dirty(
    window: MainWindow, sample: Path
) -> None:
    window.current_editor().setPlainText("編集中")
    editor = window.open_path(sample)
    assert window.tabs.count() == 2
    assert window.current_editor() is editor


# ----------------------------------------------------------------------
# MainWindow.open_path: 他のウィンドウで既に開いているファイル
# （実機フィードバックにより追加。以前はウィンドウ内しか探しておらず、
# 別ウィンドウで既に開いているファイルを Ctrl+O や D&D で開くと重複して
# タブができ、片方で保存後もう片方（古い内容）を保存すると先の保存が
# 失われる不具合があった）
# ----------------------------------------------------------------------
def test_open_path_switches_to_tab_already_open_in_other_window(
    window: MainWindow, make_window, sample: Path
) -> None:
    other = make_window()
    other.open_path(sample)
    other_tab_count = other.tabs.count()

    editor = window.open_path(sample)

    assert editor is other.current_editor()
    assert other.tabs.count() == other_tab_count  # 重複して開かれていない
    assert window.find_editor_by_path(sample.resolve()) is None  # window 側は増えない


def test_open_path_raises_other_window_when_file_found_there(
    window: MainWindow, make_window, sample: Path, qtbot
) -> None:
    other = make_window()
    other.open_path(sample)
    other.showMinimized()

    window.open_path(sample)

    assert other.isMinimized() is False


def test_open_path_does_not_raise_self_when_file_found_in_current_window(
    window: MainWindow, sample: Path
) -> None:
    """自分自身のウィンドウで見つかった場合は、わざわざ前面に出す処理は
    走らない（既存の挙動のまま、副作用が増えないことの確認）。"""
    window.open_path(sample)  # 1 回目：新規に開く
    editor = window.open_path(sample)  # 2 回目：同じウィンドウ内で見つかる

    assert editor.path == sample.resolve()
    assert window.tabs.count() == 1


def test_tab_title_and_window_title_show_file_name(
    window: MainWindow, sample: Path
) -> None:
    window.open_path(sample)
    assert window.tabs.tabText(window.tabs.currentIndex()) == "sample.txt"
    assert window.windowTitle().startswith("sample.txt - ")


def test_opening_same_file_twice_switches_to_existing_tab(
    window: MainWindow, sample: Path
) -> None:
    first = window.open_path(sample)
    window.new_file()
    assert window.current_editor() is not first
    second = window.open_path(sample)
    assert second is first
    assert window.current_editor() is first
    assert window.tabs.count() == 2  # タブは増えない


def test_open_two_files_makes_two_tabs(window: MainWindow, tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("A\n", encoding="utf-8")
    b.write_text("B\n", encoding="utf-8")
    opened = window.open_paths([a, b])
    assert len(opened) == 2
    assert window.tabs.count() == 2
    assert [e.display_name for e in window.editors()] == ["a.txt", "b.txt"]


def test_open_missing_file_shows_error_and_keeps_tabs(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda *args, **kwargs: calls.append(args[2]),
    )
    result = window.open_path(tmp_path / "missing.txt")
    assert result is None
    assert calls  # エラーダイアログが出ている
    assert window.tabs.count() == 1
    assert window.current_editor().is_empty_untitled()


def test_open_file_dialog_opens_selection(
    window: MainWindow, sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # QFileDialog はサイドバーに NAS 等を追加する都合上インスタンスベースの
    # API（exec() + selectedFiles()）を使っているので、そちらをモックする。
    monkeypatch.setattr(
        QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(sample)])
    opened = window.open_file_dialog()
    assert len(opened) == 1
    assert window.current_editor().path == sample.resolve()


def test_open_file_dialog_cancelled(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Rejected
    )
    assert window.open_file_dialog() == []
    assert window.tabs.count() == 1


def test_open_action_has_ctrl_o(window: MainWindow) -> None:
    assert window.action_open.shortcut().toString() == "Ctrl+O"


def test_open_discards_undo_history(window, sample: Path) -> None:
    """ファイルを開いた直後は undo で「開く前」に戻れないこと。

    ``EditorWidget.set_content`` は本文を差し替えたあとに undo 履歴を
    捨てている。捨てないと、開いたばかりのタブで Ctrl+Z を 1 回押した
    だけで**本文が丸ごと空（開く前の状態）になる**。しかもそれは
    「編集した」扱いなので、そのまま上書き保存すると**ファイルの中身が
    消える**。新規タブを使い回して開く経路（``is_empty_untitled``）で
    特に踏みやすい。
    """
    editor = window.open_path(sample)
    assert editor.toPlainText() == "あいうえお\n2行目\n"

    assert not editor.document().isUndoAvailable()

    editor.undo()  # 効かないこと（本文が変わらない）
    assert editor.toPlainText() == "あいうえお\n2行目\n"
    assert not editor.is_modified
