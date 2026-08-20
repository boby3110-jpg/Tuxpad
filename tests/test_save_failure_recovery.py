"""保存が失敗した**あと**の状態を見張る（43・45 回目の申し送りの続き）.

**利用者の最優先事項は「ファイルが壊れて開けなくなる」ことを避けること**。
書き込み権限が無い・NAS が切れた・ディスクが一杯——実機では保存の失敗が
実際に起きる。既存の :mod:`test_save_file` は :func:`write_encoded_file`
単体レベルで「一時ファイルが残らない・元のファイルが無傷」までは縛って
いるが、**そこから 1 段上の :meth:`~editor_app.file_commands.FileCommandsMixin.save_editor`
を通した経路で、失敗後にタブがどう見えるか**は見張られていなかった。

失敗後に**利用者が気付ける状態**（未保存マークが残る）と、**もう一度保存
できる状態**（次の保存が黙って二重に控えを取ったり、書けてもいないのに
「書けた」と応えたりしない）——この 2 つが崩れると、利用者は「保存
できたはず」の版を失ってしまう。

ここで縛る動きは 3 つ:

- **失敗した保存の後、タブは未保存マークのままで、ディスクの中身は無傷。**
  ``_write_editor`` は :func:`write_encoded_file` の例外を捕まえて
  :meth:`_report_save_failure` を出したあと ``False`` を返す（そこで止まる
  ので ``set_modified(False)`` が呼ばれない）——この経路を縛る。
- **失敗した保存の後にもう一度保存すると、二重に控えを作らない。**
  日本語レガシー文字コードのタブでは、書き込み直前に「開いたときの姿」を
  控える (引き継ぎ ⑦)。失敗した保存でも控えは取れるので
  （バックアップ置き場は保存先とは別の場所）、次の保存で**もう一度**
  控えると「開いたときの姿」ではなく「もう 1 回失敗した後の姿」で
  容量が埋まっていく。1 回目で ``backed_up_path`` が刻まれるので、
  2 回目は控えを取り直さない——ここを縛る。
- **失敗した保存の後にもう一度保存が通ると、ディスクの中身は新しい方に
  なり、タブの未保存マークも落ちる。** 1 回目が失敗したことを引きずって
  2 回目まで固まってしまうと、以後どうやっても保存できないタブが出来て
  しまう。

:func:`write_encoded_file` そのものの失敗後の後始末（一時ファイルの
掃除）は :mod:`test_save_file` の
``test_write_cleans_up_the_temp_file_when_replacing_fails`` などが縛って
いる。ここでは重複させず、**GUI 経由の 1 段上のふるまい**だけを見る。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_app import backups
from editor_app.editor import document_text
from editor_app.main_window import MainWindow


#: 日本語レガシー文字コード（cp932）で書かれた本文。控え（引き継ぎ ⑦）の
#: 対象になる文字コードを狙って選んでいる。素直に読める普通の日本語を
#: 使い、判定の拮抗（⑤）で余計なダイアログが出ないようにしている。
JAPANESE = "こんにちは。\n日本語のテキストです。\n"


def _edit(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（他のテストと同じやり方）。"""
    editor.selectAll()
    editor.insertPlainText(text)


@pytest.fixture
def informed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """「保存できません」等のお知らせダイアログを捕まえる。

    ヘッドレスでは誰も応答できないため、モーダルを開いたままにすると
    テスト全体が固まる。既存の :mod:`test_backups` と同じ差し替えを使う。
    """
    calls: list[tuple[str, str]] = []

    def fake(_parent, _icon, title, text, *_args, **_kwargs):
        calls.append((title, text))
        return None

    monkeypatch.setattr("editor_app.file_commands.show_message_box", fake)
    return calls


# ---------------------------------------------------------------------------
# 失敗しても、タブの未保存マークとディスクの中身は動かない
# ---------------------------------------------------------------------------


def test_save_failure_keeps_tab_marked_and_disk_untouched(
    window: MainWindow, tmp_path: Path, informed, monkeypatch
) -> None:
    """書き込みが失敗したら、タブは未保存のまま・ディスクの中身も動かない.

    :func:`write_encoded_file` は例外を投げるだけ（一時ファイルの後始末は
    その関数の中で完結する）。ここは 1 段上の :meth:`save_editor` が
    その例外を受けたときのふるまいを縛る:

    - ``save_editor`` は ``False`` を返す（「保存できた」に化けない）。
    - タブは ``is_modified`` のまま（未保存マークが × のまま残る）。
    - ディスクの中身は、書き込む前と 1 バイトも変わらない。
    - 「保存できません」のお知らせが 1 度だけ出る（黙って握り潰さない）。
    """
    path = tmp_path / "doc.txt"
    original = "original\n".encode("utf-8")
    path.write_bytes(original)

    editor = window.open_path(path)
    _edit(editor, "edited\n")
    assert editor.is_modified

    def refuse(*_args, **_kwargs):
        raise PermissionError("書き込めません")

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse)

    assert window.save_editor(editor) is False
    assert editor.is_modified is True
    assert path.read_bytes() == original
    assert len(informed) == 1
    assert informed[0][0] == "保存できません"


def test_save_failure_does_not_change_encoding_or_signature(
    window: MainWindow, tmp_path: Path, informed, monkeypatch
) -> None:
    """失敗した保存で、タブの文字コードや「見分け札」も動かさない.

    ``_write_editor`` の成功経路の末尾は
    ``editor.encoding = target_encoding`` や
    ``editor.remember_disk_state()`` を呼ぶが、失敗経路では**そこへ到達
    しないので、これらが動かないこと**を縛る。動いてしまうと、
    ディスクは元のままなのにタブだけが「新しい姿を書いた」つもりに
    なり、以後の外部変更判定が狂う（本当に外部で書き換えられても
    「変わっていない」に見える／自分の元のバイト列を「外部変更」と
    誤認する）。
    """
    path = tmp_path / "u16.txt"
    original = "﻿こんにちは\n".encode("utf-16")
    path.write_bytes(original)

    editor = window.open_path(path)
    original_encoding = editor.encoding
    original_signature = editor.disk_signature
    _edit(editor, "書き換え\n")

    def refuse(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse)

    assert window.save_editor(editor) is False
    assert editor.encoding == original_encoding
    assert editor.disk_signature == original_signature
    assert path.read_bytes() == original


# ---------------------------------------------------------------------------
# 二重に控えを作らない
# ---------------------------------------------------------------------------


def _isolated_backup_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """テスト用に、控えの置き場を ``tmp_path`` の中へ隔離する.

    :func:`backups.backup_root` は ``XDG_CACHE_HOME`` に従う。実機の
    ``~/.cache/tuxpad/backups/`` を汚さないよう、テスト中は ``tmp_path``
    へ差し替える。
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    return tmp_path / "xdg-cache" / "tuxpad" / "backups"


def test_save_failure_does_not_create_a_second_backup_on_retry(
    window: MainWindow, tmp_path: Path, informed, monkeypatch
) -> None:
    """1 回目で控えが取れたなら、2 回目で**もう一度**控えない.

    引き継ぎ ⑦ の「初めて書き込む直前の 1 回だけ控える」規則は、
    書き込み自体が失敗しても崩れてはいけない:

    - 1 回目: 控えを取る（成功）→ 書き込む（失敗）。タブに
      「控え済み」の印 (``backed_up_path``) が刻まれる。
    - 2 回目: ``backed_up_path == path`` を見て**控えを取り直さない**。
      書き込みが今度は通ったとき、控えは 1 件だけのまま（＝「開いた
      ときの姿」が残る）。

    ここが破れると、失敗を挟むたびに「もう一度失敗した後の姿」が控えの
    枠を食い、いちばん戻したい「開いた時点の姿」が容量の掃除で押し出さ
    れてしまう。
    """
    _isolated_backup_root(tmp_path, monkeypatch)

    path = tmp_path / "sjis.txt"
    path.write_bytes(JAPANESE.encode("cp932"))

    editor = window.open_path(path)
    assert editor.encoding == "cp932"
    assert backups.should_back_up(editor.encoding)

    _edit(editor, "書き換え 1\n")

    # 1 回目: 書き込みだけ失敗させる。控えは実物を通す。
    real_write = None  # not used; we simply refuse
    calls = {"count": 0}

    def refuse_once(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("書き込めません")
        # 2 回目からは本物の書き込みへ回す。
        from editor_app.fileio import write_encoded_file as real
        return real(*_args, **_kwargs)

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse_once)

    assert window.save_editor(editor) is False
    assert editor.is_modified is True
    assert editor.backed_up_path == path.resolve()

    # 控えが 1 件、開いた時点のバイト列で存在する。
    entries_after_first = backups.list_backups(path.resolve())
    assert len(entries_after_first) == 1
    assert entries_after_first[0].data_path.read_bytes() == JAPANESE.encode("cp932")

    # 2 回目の保存（今度は通す）。書き込みは 1 度きり呼ばれ、
    # **控えは増えない**ことを縛る。
    assert window.save_editor(editor) is True
    assert editor.is_modified is False
    assert path.read_bytes() == "書き換え 1\n".encode("cp932")

    entries_after_second = backups.list_backups(path.resolve())
    assert len(entries_after_second) == 1
    assert entries_after_second[0].data_path == entries_after_first[0].data_path
    assert entries_after_second[0].data_path.read_bytes() == JAPANESE.encode("cp932")


def test_save_failure_when_backup_also_fails_still_leaves_no_mark(
    window: MainWindow, tmp_path: Path, informed, monkeypatch
) -> None:
    """控えが取れなかった保存の失敗は、``backed_up_path`` を刻まない.

    控え置き場（``~/.cache/tuxpad/backups/``）自体が書けない状況では、
    :meth:`_backup_before_first_write` は例外を握り潰して**印を付けずに**
    戻る（「印を付けるのは控えられたときだけ」の規則）。書き込みも失敗
    した場合、2 回目の保存では**改めて控えを取りに行けるべき**——
    そうでないと、いちど控え置き場が塞がっただけで以後永久に控えが
    取れなくなる。
    """
    _isolated_backup_root(tmp_path, monkeypatch)

    path = tmp_path / "sjis.txt"
    path.write_bytes(JAPANESE.encode("cp932"))

    editor = window.open_path(path)
    _edit(editor, "書き換え\n")

    def refuse_backup(*_args, **_kwargs):
        raise OSError("控え置き場が書けません")

    def refuse_write(*_args, **_kwargs):
        raise PermissionError("書き込めません")

    monkeypatch.setattr("editor_app.file_commands.backup_file", refuse_backup)
    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse_write)

    assert window.save_editor(editor) is False
    assert editor.is_modified is True
    # 控えが取れていない以上、印は残らない（次回に取り直せる状態）。
    assert editor.backed_up_path is None


# ---------------------------------------------------------------------------
# 失敗の次の保存が通ったら、素直に完了する
# ---------------------------------------------------------------------------


def test_save_recovers_after_a_failed_attempt(
    window: MainWindow, tmp_path: Path, informed, monkeypatch
) -> None:
    """1 回目が失敗しても、2 回目は普通に通って完了する.

    失敗が「以後どうやっても保存できないタブ」を残さないことを縛る。
    ここが崩れると、利用者は編集内容を丸ごと失う（別の場所へ
    「名前を付けて保存」する道はあるが、それを毎回強いるのは酷）。
    """
    path = tmp_path / "doc.txt"
    path.write_bytes(b"original\n")

    editor = window.open_path(path)
    _edit(editor, "書き換え\n")

    calls = {"count": 0}

    def refuse_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("書き込めません")
        from editor_app.fileio import write_encoded_file as real
        return real(*args, **kwargs)

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse_once)

    assert window.save_editor(editor) is False
    assert editor.is_modified is True
    assert path.read_bytes() == b"original\n"

    assert window.save_editor(editor) is True
    assert editor.is_modified is False
    assert path.read_bytes() == "書き換え\n".encode("utf-8")
    # 「保存できません」のお知らせは 1 度きり（2 回目で通ったので、
    # 通ったあとに「できません」が出るのは自己矛盾）。
    assert len(informed) == 1


def test_save_failure_does_not_apply_nbsp_downgrade(
    window: MainWindow, tmp_path: Path, informed, monkeypatch
) -> None:
    """書き込みが失敗したら、NBSP の半角空白への落とし込みも起きない.

    ``_apply_no_break_space_downgrade`` は「書けたので本文をディスクに揃える」
    仕上げなので、**書けていないのに本文だけを揺らしてはいけない**
    （書き込み前に落としてしまうと、利用者が UTF-8 で保存し直す逃げ道を
    選んでも NBSP が戻らなくなる。既存コメントの通り）。

    NBSP が落ちない Shift-JIS で書かれたファイルに NBSP を差し込み、
    書き込みだけ失敗させたときの本文がどうなるかを縛る。
    """
    path = tmp_path / "nbsp.txt"
    path.write_bytes("普通の空白 で始まる\n".encode("cp932"))

    editor = window.open_path(path)
    editor.selectAll()
    editor.insertPlainText("NBSP を含む本文\n")
    assert " " in document_text(editor)

    def refuse(*_args, **_kwargs):
        raise PermissionError("書き込めません")

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse)

    assert window.save_editor(editor) is False
    # 本文は書き換わっていない（NBSP が半角空白に化けていない）。
    assert " " in document_text(editor)
    assert editor.is_modified is True
