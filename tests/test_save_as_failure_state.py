"""「名前を付けて保存」「文字コードを指定して保存」の**失敗した後**のタブの
状態を見張る（46 回目の申し送りの続き）.

45 回目に足した :mod:`test_save_failure_recovery` は
:func:`write_encoded_file` の失敗経路（実際にディスクへ書きに行った時点で
落ちる場合）を見張った。ここは**その手前の関門で落ちる経路**——

- :meth:`save_editor_as` のダイアログをキャンセルした場合
- :meth:`save_editor_as` の保存先が別のタブで開かれていた場合
  （:meth:`_warn_path_open_in_another_tab`）
- :meth:`save_editor_with_encoding` を無題タブに対して行い、選んだ
  文字コードで表せない文字が本文にあった場合
  （:meth:`save_editor_as` 経由で :meth:`_write_editor` が
  ``EncodingError`` を投げる経路）
- :meth:`_write_editor` の ``EncodingError`` の失敗経路
  （既存 :mod:`test_unencodable_characters` は本文と ``is_modified`` は
  縛っていたが、**文字コードの拮抗の印**や**「見分け札」の更新**の有無は
  縛っていない）

——のうしろで**利用者から見える状態が半端に書き換わっていないか**を
縛る。

**なぜここが穴になっていたか**: 成功経路の末尾は 6 行ある
（:attr:`~editor_app.editor.EditorWidget.path` / :attr:`.encoding` /
:attr:`.encoding_alternatives` / :meth:`.remember_disk_state` /
:meth:`.set_modified` / :meth:`~FileCommandsMixin._refresh_file_watches`）。
失敗経路では**これらが 1 つも動かない**ことが約束だが、既存テストは
「本文」「``is_modified``」「ディスクの中身」までは縛っていても、
**拮抗の印・見分け札・``path`` そのもの**は縛れていなかった。

いちばん危ないのは、失敗経路で ``editor.remember_disk_state()`` が動いて
しまうこと——**ディスクに書けていないのに「今のディスクの姿と一致した」**
という印を付ける形になり、以後の外部変更判定が黙って狂う（本当に外部で
書き換えられても「変わっていない」に見える）。**利用者の最優先事項
「ファイルが壊れて開けなくなる」を避ける**という目的からいちばん遠い。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from editor_app import backups, fileio
from editor_app.main_window import MainWindow


#: cp932 でも euc_jp でも読めてしまう代表例
#: （:mod:`test_encoding_ambiguity` と同じ材料）。EUC-JP のひらがなの
#: 1 バイト目 ``0xA4`` が cp932 では半角の読点 ``､`` として成立するため、
#: どちらの読み方も「日本語としてありそう」に見え、判定は拮抗する。
AMBIGUOUS_BYTES = "あいうえお\n".encode("euc_jp")


def _edit(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（他のテストと同じやり方）。"""
    editor.selectAll()
    editor.insertPlainText(text)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """「保存できません」等のお知らせダイアログを捕まえる。

    ヘッドレスでは誰も応答できないため、モーダルを開いたままにするとテスト
    全体が固まる。既存の :mod:`test_backups` と同じ差し替えを使う。
    """
    calls: list[tuple[str, str]] = []

    def fake(_parent, _icon, title, text, *_args, **_kwargs):
        calls.append((title, text))
        return None

    monkeypatch.setattr("editor_app.file_commands.show_message_box", fake)
    return calls


def _mock_save_dialog(monkeypatch: pytest.MonkeyPatch, path: Path | None) -> None:
    """「名前を付けて保存」ダイアログを差し替える。

    ``path`` が ``None`` ならキャンセルを再現し、そうでなければその
    パスを選んだことにする。既存 :mod:`test_save_file` と同じ実装。
    """
    if path is None:
        monkeypatch.setattr(
            QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Rejected
        )
    else:
        monkeypatch.setattr(
            QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Accepted
        )
        monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(path)])


def _mock_choice(monkeypatch: pytest.MonkeyPatch, encoding: str) -> None:
    """文字コード選択ダイアログを差し替える（そのコーデックを選んだことにする）。"""

    def fake(_parent, _title, _label, items, _current):
        return items.index(fileio.describe_encoding(encoding))

    monkeypatch.setattr("editor_app.file_commands.prompt_choice", fake)


# ---------------------------------------------------------------------------
# 「名前を付けて保存」のダイアログをキャンセルしても、控えの印は残る
# ---------------------------------------------------------------------------


def test_save_as_cancel_keeps_backed_up_path(
    window: MainWindow, tmp_path: Path, captured, monkeypatch
) -> None:
    """ダイアログをキャンセルしても、``backed_up_path`` の印は動かさない.

    「このタブはこのファイルを控え済み」という印
    （:attr:`~editor_app.editor.EditorWidget.backed_up_path`）は、初回保存の
    直前に**成功したときだけ**刻む
    （:meth:`~editor_app.file_commands.FileCommandsMixin._backup_before_first_write`）。
    ダイアログのキャンセルはそこまで到達しないので、この印は 1 バイトも
    動かしてはいけない——動かすと、**同じファイルに対する 2 回目の保存で
    もう一度控えを取り**、控えの枠が「開いたときの姿」ではなく
    「もう 1 回失敗した後の姿」で埋まっていく。
    """
    # 引き継ぎ ⑦ の対象は日本語レガシー文字コードのタブだけ。
    path = tmp_path / "sjis.txt"
    path.write_bytes("最初の日本語\n".encode("cp932"))

    editor = window.open_path(path)
    _edit(editor, "書き換え\n")
    assert window.save_editor(editor) is True  # 1 回目で控えが刻まれる
    assert editor.backed_up_path == path.resolve()

    _mock_save_dialog(monkeypatch, None)  # ダイアログをキャンセル
    assert window.save_editor_as(editor) is False

    # キャンセルで控えの印を捨てないこと（次の Ctrl+S で 2 度目の控えを
    # 取り直さない）。
    assert editor.backed_up_path == path.resolve()


# ---------------------------------------------------------------------------
# 「名前を付けて保存」で別のタブで開いているパスへ保存しようとしても、
# タブの拮抗の印は動かない
# ---------------------------------------------------------------------------


def test_save_as_refuse_keeps_encoding_alternatives(
    window: MainWindow, tmp_path: Path, captured, monkeypatch
) -> None:
    """既に別のタブで開いているパスへの保存は :meth:`_warn_path_open_in_another_tab`
    で止まるが、**タブの拮抗の印** (``encoding_alternatives``) は動かさない.

    拮抗の印はステータスバーに「?」として出ている
    (:data:`~editor_app.main_window.AMBIGUOUS_ENCODING_MARK`)。ここが黙って
    消えると、利用者は「文字コードが確定した」と読み違え、間違ったまま
    編集して保存してしまう恐れがある——利用者の最優先事項
    （ファイルを壊さない）にいちばん近い印を、失敗した保存で消しては
    いけない。
    """
    ambiguous_path = tmp_path / "ambiguous.txt"
    ambiguous_path.write_bytes(AMBIGUOUS_BYTES)
    editor = window.open_path(ambiguous_path)
    assert editor.encoding_alternatives == ("cp932",)

    # 別のタブで既に開いているファイルの候補を作る（保存先の衝突を再現）。
    other_path = tmp_path / "他のタブ.txt"
    other_path.write_bytes("他の内容\n".encode("utf-8"))
    window.open_path(other_path)

    # そのパスへ「名前を付けて保存」しようとする（別のタブが保有中なので止まる）。
    window.tabs.setCurrentWidget(editor)
    _mock_save_dialog(monkeypatch, other_path)

    assert window.save_editor_as(editor) is False
    assert captured and captured[0][0] == "保存できません"

    # 拮抗の印は動かさない（利用者は「?」を見て文字コードを確かめようと
    # している最中かもしれない）。
    assert editor.encoding_alternatives == ("cp932",)


# ---------------------------------------------------------------------------
# 保存できない文字があった失敗の後、タブの拮抗の印・「見分け札」・パスは動かない
# ---------------------------------------------------------------------------


def test_encoding_error_keeps_disk_signature(
    window: MainWindow, tmp_path: Path, captured, monkeypatch
) -> None:
    """保存できない文字で ``_write_editor`` が失敗しても、``disk_signature`` を
    更新しない.

    ``remember_disk_state()`` は成功経路の末尾でだけ呼ぶ。失敗経路で
    呼ばれてしまうと、**ディスクへは書いていないのに「今のディスクの姿と
    一致した」印**を付けたことになり、以後この編集が「外部変更」と
    誤認されたり、逆に本物の外部変更が「変わっていない」に見えたりする。

    保存の失敗そのものはディスクを触らないので、単に
    ``editor.disk_signature`` の値を見ても、失敗経路で
    ``remember_disk_state()`` を呼ぼうが呼ばまいが**同じ値**になる
    （ディスクの中身が動いていないので）——それでは黙って狂う経路を
    捕まえられない。**外部で書き換わっている状態**で失敗経路を走らせ、
    そこで ``remember_disk_state`` が動いてしまうと**その新しいバイト列で
    控えが更新される**——その差を見て縛る。

    ここは :meth:`_check_external_change` の関門も通す必要があるので、
    差分ダイアログで「上書き」を選んだ想定にして先へ進める。それでも
    ``encode_text`` の ``EncodingError`` で止まる。
    """
    from editor_app.dialogs import EXTERNAL_CHANGE_OVERWRITE

    path = tmp_path / "sjis.txt"
    path.write_bytes("最初の日本語\n".encode("cp932"))
    editor = window.open_path(path)
    original_signature = editor.disk_signature

    _edit(editor, "書き換え♡\n")  # ♡ は cp932 で表せない

    # 外部でファイルの中身が変わっている状態を作り、差分ダイアログでは
    # 「ディスクの内容を上書きする」を選んだことにして先へ進める。
    path.write_bytes("外部が書いた別の内容\n".encode("cp932"))
    monkeypatch.setattr(
        "editor_app.file_commands.prompt_external_change",
        lambda *_a, **_k: EXTERNAL_CHANGE_OVERWRITE,
    )

    assert window.save_editor(editor) is False  # EncodingError で止まる
    assert captured and captured[0][0] == "保存できません"

    # 見分け札は「開いた時点で読んだ姿」のまま（失敗した保存で更新しない）。
    # 外部で書き換えられた後の姿へ差し替わっていたら、以後の外部変更判定が
    # 黙って狂う（今の食い違いを見逃す）。
    assert editor.disk_signature == original_signature
    assert editor.disk_signature != fileio.file_signature(path)


def test_encoding_error_keeps_encoding_alternatives(
    window: MainWindow, tmp_path: Path, captured
) -> None:
    """保存できない文字で ``_write_editor`` が失敗しても、拮抗の印は動かない.

    拮抗した文字コードの判定 (``encoding_alternatives``) は「?」印として
    利用者に見えている。**編集して保存しようとして失敗したから消える**
    のは筋が通らない（消してよいのは、その文字コードで**実際に書けた**
    ときだけ）。
    """
    path = tmp_path / "ambiguous.txt"
    path.write_bytes(AMBIGUOUS_BYTES)  # euc_jp / cp932 が拮抗
    editor = window.open_path(path)
    assert editor.encoding_alternatives == ("cp932",)

    # ♡ は euc_jp で表せない（cp932 でも表せない）。
    _edit(editor, "書き換え♡\n")
    assert window.save_editor(editor) is False
    assert captured and captured[0][0] == "保存できません"

    # 印は動かさない（実際に書けたわけではないので、まだ推測のまま）。
    assert editor.encoding_alternatives == ("cp932",)


def test_write_failure_keeps_encoding_alternatives(
    window: MainWindow, tmp_path: Path, captured, monkeypatch
) -> None:
    """書き込みの失敗（:func:`write_encoded_file` の例外）でも、拮抗の印は動かない.

    :meth:`test_encoding_error_keeps_encoding_alternatives` は encode の
    手前 (``EncodingError``) で止まる経路を縛る。こちらは encode を通した
    後、実際にディスクへ書きに行った時点で失敗する経路
    （書き込み権限が無い・NAS が切れた・ディスクが一杯 等）を縛る。
    """
    path = tmp_path / "ambiguous.txt"
    path.write_bytes(AMBIGUOUS_BYTES)  # euc_jp / cp932 が拮抗
    editor = window.open_path(path)
    assert editor.encoding_alternatives == ("cp932",)

    _edit(editor, "書き換え\n")  # encode は通る（拮抗のままの文字コードで書ける）

    def refuse(*_args, **_kwargs):
        raise PermissionError("書き込めません")

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse)

    assert window.save_editor(editor) is False
    assert captured and captured[0][0] == "保存できません"

    # 印は動かさない（書けていないので、まだ推測のまま）。
    assert editor.encoding_alternatives == ("cp932",)


def test_encoding_error_via_save_as_keeps_path(
    window: MainWindow, tmp_path: Path, captured, monkeypatch
) -> None:
    """「名前を付けて保存」で保存できない文字に当たっても、``editor.path`` は動かない.

    成功経路では ``editor.path = resolved`` が末尾にあるが、失敗経路では
    そこへ到達しない。ここが破れると、書けてもいない新しいパスへ**タブが
    紐付き直り**、以後の Ctrl+S も外部変更判定もその新しいパスに対して
    走る——利用者にとっては「保存できないと言われたのに、以後は別の場所を
    指し始める」形の不透明な壊れ方になる。
    """
    src = tmp_path / "元.txt"
    src.write_bytes("最初の日本語\n".encode("cp932"))
    editor = window.open_path(src)
    original_path = editor.path
    _edit(editor, "書き換え♡\n")  # ♡ は cp932 で表せない

    target = tmp_path / "別名.txt"
    _mock_save_dialog(monkeypatch, target)

    assert window.save_editor_as(editor) is False
    assert captured and captured[0][0] == "保存できません"

    # 保存先は「元のファイル」のまま。
    assert editor.path == original_path
    # 新しいファイルは作られていない（保存できなかったのだから当然だが、
    # 失敗経路で resolved を刻んでいたら、次の Ctrl+S で作られてしまう）。
    assert not target.exists()


# ---------------------------------------------------------------------------
# 「文字コードを指定して保存」を無題タブに対して行い、失敗したら文字コードは動かない
# ---------------------------------------------------------------------------


def test_save_with_encoding_untitled_unencodable_keeps_encoding(
    window: MainWindow, tmp_path: Path, captured, monkeypatch
) -> None:
    """無題タブ + 「文字コードを指定して保存」で失敗した場合、タブの
    文字コードは元のまま（利用者が「その 1 回だけ」選んだ文字コードを
    タブに焼き付けない）.

    無題タブ ``editor.path is None`` は :meth:`save_editor_as` に**エン
    コーディング付き**で回される。この経路で :meth:`_write_editor` の
    ``encode_text`` が :class:`~editor_app.fileio.EncodingError` を投げた
    とき、利用者は「その文字コードで保存できない」ことに気づいて
    ダイアログを閉じるだけ——選び直しや保存が完了したわけではない。

    タブの ``encoding`` を選んだ文字コードに書き換えてしまうと、次に
    :meth:`save_editor` (Ctrl+S) が呼ばれたときにも、無言でその文字コード
    で保存しようとする（同じ失敗を無言で繰り返す）。**利用者が明示的に
    選び直すまで、タブの文字コードは動かさない**——その約束をここで縛る。

    :meth:`save_editor_as` の**成功経路**では :meth:`_write_editor` が
    末尾で ``editor.encoding = target_encoding`` を刻むが、失敗経路では
    そこへ到達しないため、**それより手前で ``editor.encoding = encoding``
    を刻んでいるコードが紛れ込むと、この約束が黙って破れる**。実際に
    :meth:`save_editor_as` の入口や
    :meth:`save_editor_with_encoding` の入口へそういう 1 行を差し込むと、
    既存テストは全て緑のまま通ることを確認した（変異
    ``fc-save-as-preset-encoding``）。ここで塞ぐ。
    """
    editor = window.current_editor()
    assert editor.path is None
    original_encoding = editor.encoding  # 新規タブの既定（通常 utf-8）
    _edit(editor, "1行目\n♡が入っている\n")  # ♡ は cp932 で表せない

    target = tmp_path / "新規.txt"
    _mock_save_dialog(monkeypatch, target)
    _mock_choice(monkeypatch, "cp932")

    assert window.save_editor_with_encoding(editor) is False
    assert captured and captured[0][0] == "保存できません"

    # 選んだ文字コードは、実際に書けていない以上「そのタブの文字コード」に
    # 昇格させてはいけない。
    assert editor.encoding == original_encoding
    # 保存先も、書けていない以上刻まない（無題のまま）。
    assert editor.path is None
    # ディスクにも書かれていないこと（保険）。
    assert not target.exists()
    # 未保存マークも残る（何も保存されていないので当然）。
    assert editor.is_modified is True


def test_save_with_encoding_untitled_cancel_keeps_backup_mark(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    """無題タブに対して「文字コードを指定して保存」でダイアログをキャンセル
    しても、無関係な状態が動かない.

    無題タブは ``editor.path is None`` なので、そもそも「控え済み」の印
    (``backed_up_path``) は付いていない。キャンセルでそれを黙って
    書き換えてはいけない（None のまま）——次に別のパスへ保存し直したとき、
    :meth:`_backup_before_first_write` が改めて動けるようにするため。
    """
    editor = window.current_editor()
    assert editor.path is None
    assert editor.backed_up_path is None
    _edit(editor, "新しい日本語\n")

    _mock_save_dialog(monkeypatch, None)
    _mock_choice(monkeypatch, "cp932")

    assert window.save_editor_with_encoding(editor) is False

    # 控えの印は None のまま（保存先が決まっていないので刻めない）。
    assert editor.backed_up_path is None
    # 次の保存でもう一度控えを取りに行けるように、掃除は起きていない。
    # （ここは backups.list_backups を空で確かめる：まだ何も控えていない）。
    xdg = Path(tmp_path).joinpath("cache", "tuxpad", "backups")
    assert not xdg.exists() or not any(xdg.iterdir())
    # `_isolated_backup_cache` フィクスチャの置き場と一致（変わっていない
    # ことをフィクスチャ側で保証している）。
    _ = backups  # ここで backups モジュールを import する意味を残しておく
