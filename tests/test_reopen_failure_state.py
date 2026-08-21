"""「文字コードを指定して開き直す」の**失敗した後**のタブの状態を縛るテスト.

背景（47 回目への申し送り）: 46 回目に「名前を付けて保存」「文字コードを指定して
保存」の失敗経路について、成功経路の末尾で動く 6 行が失敗経路で誤って動いていない
かを 1 段上の層から見張るテストを足した。**同じ発想を「開き直し」にも当てはめる**
のがこの回の狙い。

`reopen_editor_with_encoding` の説明には「選んだ文字コードで読めなければ、
**タブには一切触らず**理由を伝える」とある（`file_commands.py` の docstring）。
既存のテスト (`test_encoding_ambiguity.py`) はこれを「本文」「エディタが覚えて
いる文字コード」の 2 つでしか見ておらず、次のような**「一切触っていない」を
実は縛れていない**状態が残っていた:

- **拮抗の印** (`encoding_alternatives`) が消えていないか
- **改行コード** (`newline`) が書き換わっていないか
- **未保存マーク** (`is_modified`) が動いていないか
- **ディスクの見分け札** (`disk_signature`) が更新されていないか
  （書いてもいないのに「今のディスクと同じだ」に印を付けると、
  次に外部で書き換わっても保存直前チェックが気づけなくなる）
- **カーソル位置**が動いていないか
- **ステータスバーの文字コード表示**（拮抗の印を含む）が動いていないか
- **タブのパス** (`editor.path`) が動いていないか

これは失敗経路（読めなかった）だけでなく、その手前の 3 つの「戻る」経路
——キャンセル・未保存の破棄を No で断った・保存していないタブ——にも
同じ縛りが要る。全てで同じ穴が空いていると、失敗経路への変異は他の 3 経路の
テストでも赤くならず、変異検査で「本当に縛れているか」を確かめる意味が薄れる。

ヘッドレスなので、モーダルダイアログの差し替えは
`test_encoding_ambiguity.py` と同じ書き方をなぞる（`prompt_choice` と
`show_message_box` を `editor_app.file_commands` の名前で差し替える）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from editor_app.main_window import AMBIGUOUS_ENCODING_MARK, MainWindow

#: cp932 でも euc_jp でも読める代表例（`test_encoding_ambiguity.py` と同じ）。
#: 判定は euc_jp、負けた cp932 が「拮抗の印」の元になる。
AMBIGUOUS_BYTES = "あいうえお\n".encode("euc_jp")

#: UTF-16 として読めないが utf-8 として読める本文（読み直しを失敗させる材料）。
UTF8_TEXT = "日本語のテキスト\n"


def _write_bytes(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _mock_choice(monkeypatch: pytest.MonkeyPatch, encoding: str | None) -> None:
    """文字コード選択ダイアログを差し替える。

    `encoding` が None ならキャンセルしたことにする。それ以外は、その
    コーデック名を選んだことにする（一覧上での並び順に依存しないよう、
    実装が使う `describe_encoding` を通して探す）。
    """
    from editor_app import fileio

    def fake_prompt(parent, title, label, items, current):
        if encoding is None:
            return None
        return items.index(fileio.describe_encoding(encoding))

    monkeypatch.setattr("editor_app.file_commands.prompt_choice", fake_prompt)


def _mock_message_box(
    monkeypatch: pytest.MonkeyPatch, answer: QMessageBox.StandardButton | None = None
) -> list[tuple[str, str]]:
    """`show_message_box` を差し替え、(題名, 本文) を記録して返す。"""
    captured: list[tuple[str, str]] = []

    def fake_box(parent, icon, title, text, *args):
        captured.append((title, text))
        return answer

    monkeypatch.setattr("editor_app.file_commands.show_message_box", fake_box)
    return captured


def _snapshot(window: MainWindow, editor) -> dict:
    """あとで「触っていない」を確かめるための状態一式を控える。"""
    return {
        "text": editor.toPlainText(),
        "encoding": editor.encoding,
        "encoding_alternatives": editor.encoding_alternatives,
        "newline": editor.newline,
        "is_modified": editor.is_modified,
        "disk_signature": editor.disk_signature,
        "cursor_position": editor.textCursor().position(),
        "encoding_label": window._encoding_label.text(),
        "encoding_tooltip": window._encoding_label.toolTip(),
        "path": editor.path,
    }


def _assert_untouched(window: MainWindow, editor, before: dict) -> None:
    """`_snapshot` で控えた状態の**全項目**が今も同じであることを確かめる。"""
    now = _snapshot(window, editor)
    # 1 項目ずつ比べる。まとめて比べると、どれが動いたか分からなくなる
    # （変異検査のときに「どの部分の穴か」を切り分けたいので、あえて分ける）。
    assert now["text"] == before["text"]
    assert now["encoding"] == before["encoding"]
    assert now["encoding_alternatives"] == before["encoding_alternatives"]
    assert now["newline"] == before["newline"]
    assert now["is_modified"] == before["is_modified"]
    assert now["disk_signature"] == before["disk_signature"]
    assert now["cursor_position"] == before["cursor_position"]
    assert now["encoding_label"] == before["encoding_label"]
    assert now["encoding_tooltip"] == before["encoding_tooltip"]
    assert now["path"] == before["path"]


# ----------------------------------------------------------------------
# (1) 選んだ文字コードで**読めなかった**とき
# ----------------------------------------------------------------------
def test_read_failure_leaves_every_observable_state(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """読み直しに失敗しても、タブから見える状態は全て元のままであること。

    「タブには一切触らず」（`reopen_editor_with_encoding` の docstring）を、
    本文と文字コード以外の 8 項目でも縛る。既存の
    `test_reopen_with_an_impossible_encoding_keeps_the_tab` は
    `toPlainText()` と `editor.encoding` の 2 項目しか見ていない。
    """
    path = _write_bytes(tmp_path, "sample.txt", UTF8_TEXT.encode("utf-8"))
    editor = window.open_path(path)
    before = _snapshot(window, editor)

    _mock_choice(monkeypatch, "utf-16-le")  # UTF-16 として読めない中身
    captured = _mock_message_box(monkeypatch)

    assert window.reopen_editor_with_encoding(editor) is False
    assert len(captured) == 1
    assert captured[0][0] == "開き直せません"

    _assert_untouched(window, editor, before)


def test_read_failure_keeps_the_ambiguity_mark(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """読み直しに失敗しても、拮抗の印は消えないこと。

    「利用者が自分で選んだ以上、判定の拮抗は解決している（印を消す）」の
    分岐 (`_reload_editor_content` の呼び出し) は成功したときだけ通る。
    失敗経路で `encoding_alternatives = ()` を通してしまうと、
    **失敗したのに拮抗の印だけが黙って消える**（ステータスバーからも
    消える）ため、次に開くまで判定の危うさに気づけなくなる。
    """
    path = _write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    assert editor.encoding_alternatives == ("cp932",)
    assert AMBIGUOUS_ENCODING_MARK in window._encoding_label.text()

    _mock_choice(monkeypatch, "utf-16-le")  # 読めない
    _mock_message_box(monkeypatch)
    assert window.reopen_editor_with_encoding(editor) is False

    assert editor.encoding_alternatives == ("cp932",)
    assert AMBIGUOUS_ENCODING_MARK in window._encoding_label.text()


def test_read_failure_after_confirmed_discard_keeps_is_modified(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未保存を捨てる確認に「はい」と答えても、**読めなければ**未保存は残ること。

    利用者が Yes と答えても、実際に本文を差し替えるのは読み直しに成功した
    後だけ（`_reload_editor_content` の中で `set_content` が入れ替える）。
    失敗経路で `editor.set_modified(False)` を通してしまうと、
    **書けてもいないのに「未保存の編集は消えた」ことになってしまう**——
    タブを閉じるときの確認が出なくなり、編集内容が黙って消える。
    """
    path = _write_bytes(tmp_path, "sample.txt", UTF8_TEXT.encode("utf-8"))
    editor = window.open_path(path)
    editor.selectAll()
    editor.insertPlainText("編集中の文\n")
    assert editor.is_modified is True

    _mock_choice(monkeypatch, "utf-16-le")  # 読めない
    _mock_message_box(monkeypatch, QMessageBox.StandardButton.Yes)  # 捨ててよい
    assert window.reopen_editor_with_encoding(editor) is False

    # 読めなかった＝本文は差し替わっていない＝未保存の編集は残っている。
    assert editor.is_modified is True
    assert editor.toPlainText() == "編集中の文\n"


def test_read_failure_does_not_refresh_the_disk_signature(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """読み直しに失敗しても、ディスクの見分け札を取り直さないこと。

    見分け札 (`disk_signature`) は「このタブが最後に見たディスクの姿」で、
    保存直前の外部変更チェックの根拠。失敗経路で取り直してしまうと、
    **書いてもいないのに「今のディスクと同じだ」に印を付けた**ことになり、
    次に外部で書き換わっても保存直前チェックが気づけなくなる。

    ここでは、開いた後にディスクを書き換えて古い札を残した上で、
    失敗経路が札を更新していないことを確かめる（更新していれば札は
    新しい値になり、書き換え前と等しくなくなる）。
    """
    path = _write_bytes(tmp_path, "sample.txt", UTF8_TEXT.encode("utf-8"))
    editor = window.open_path(path)
    old_signature = editor.disk_signature
    assert old_signature is not None

    # 外部から書き換える（サイズが変わるように内容も変える）。
    path.write_bytes((UTF8_TEXT + "外で足された行\n").encode("utf-8"))

    _mock_choice(monkeypatch, "utf-16-le")  # 読めない
    _mock_message_box(monkeypatch)
    assert window.reopen_editor_with_encoding(editor) is False

    assert editor.disk_signature == old_signature


# ----------------------------------------------------------------------
# (2) キャンセル（文字コードを選ばずに閉じた）
# ----------------------------------------------------------------------
def test_cancel_does_not_refresh_the_disk_signature(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """キャンセルでも、ディスクの見分け札を取り直さないこと。

    キャンセル経路 (`prompt_choice` が None) は「触らずに戻る」枝の代表。
    既存の `test_reopen_cancelled_leaves_the_tab_untouched` は本文・文字
    コード・拮抗の印までは見ているが、見分け札は見ていない。
    """
    path = _write_bytes(tmp_path, "sample.txt", UTF8_TEXT.encode("utf-8"))
    editor = window.open_path(path)
    old_signature = editor.disk_signature
    path.write_bytes((UTF8_TEXT + "外で足された行\n").encode("utf-8"))

    _mock_choice(monkeypatch, None)  # キャンセル
    assert window.reopen_editor_with_encoding(editor) is False

    assert editor.disk_signature == old_signature


def test_cancel_leaves_every_observable_state(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """キャンセルなら、タブから見える状態は全て元のままであること。"""
    path = _write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    editor.set_modified(True)
    before = _snapshot(window, editor)

    _mock_choice(monkeypatch, None)
    assert window.reopen_editor_with_encoding(editor) is False

    _assert_untouched(window, editor, before)


# ----------------------------------------------------------------------
# (3) 未保存の編集を「破棄しない」と答えたとき
# ----------------------------------------------------------------------
def test_declining_the_discard_leaves_every_observable_state(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未保存の編集を捨てないと答えたら、タブから見える状態は全て元のまま。"""
    path = _write_bytes(tmp_path, "ambiguous.txt", AMBIGUOUS_BYTES)
    editor = window.open_path(path)
    # 未保存の編集を作っておく（`is_modified` が True になっていること）。
    editor.selectAll()
    editor.insertPlainText("まだ保存していない編集\n")
    assert editor.is_modified is True
    before = _snapshot(window, editor)

    _mock_choice(monkeypatch, "cp932")
    _mock_message_box(monkeypatch, QMessageBox.StandardButton.No)
    assert window.reopen_editor_with_encoding(editor) is False

    _assert_untouched(window, editor, before)


def test_declining_the_discard_does_not_refresh_the_disk_signature(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未保存を捨てないと答えても、ディスクの見分け札は取り直さないこと。"""
    path = _write_bytes(tmp_path, "sample.txt", UTF8_TEXT.encode("utf-8"))
    editor = window.open_path(path)
    editor.selectAll()
    editor.insertPlainText("編集中\n")
    old_signature = editor.disk_signature
    path.write_bytes((UTF8_TEXT + "外で足された行\n").encode("utf-8"))

    _mock_choice(monkeypatch, "cp932")
    _mock_message_box(monkeypatch, QMessageBox.StandardButton.No)
    assert window.reopen_editor_with_encoding(editor) is False

    assert editor.disk_signature == old_signature


# ----------------------------------------------------------------------
# (4) まだ保存していない（`path is None`）タブ
# ----------------------------------------------------------------------
def test_unsaved_tab_leaves_every_observable_state(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保存前のタブで呼んでも、タブから見える状態は全て元のままであること。

    このタブは `path` が None のため見分け札も None だが、印を消したり、
    無題タブの拮抗フラグを勝手に触ったりしていないかを合わせて確かめる。
    """
    editor = window.current_editor()
    assert editor.path is None
    # 適当に書いておいて `is_modified` を立てる（既定値だと動いても気づかない）。
    editor.insertPlainText("下書き\n")
    before = _snapshot(window, editor)

    _mock_choice(monkeypatch, "cp932")
    _mock_message_box(monkeypatch)
    assert window.reopen_editor_with_encoding(editor) is False

    _assert_untouched(window, editor, before)
