"""「バックアップから復元」が**失敗した後**に残る状態を縛るテスト.

背景（48 回目への申し送り）: 45〜47 回目で「保存」「名前を付けて保存」
「文字コードを指定して開き直す」の**失敗経路の「あと」**を 1 段上の層から
見張るテストを足してきた。**同じ発想を復元（7-B）に当てはめる**のがこの回。

`restore_editor_from_backup` の失敗経路は 4 つあり、既存の
`test_backups.py` はどれも「ファイルが変わっていないこと」「知らせが出る
こと」までしか見ていない。**その後にタブと控えへ何が残ったか**——特に
次の Ctrl+S がどう振る舞うか——は誰も縛っていなかった:

- **控えを取ろうとして失敗した**（キャッシュ置き場が書けない等）。ここで
  タブに「もう控えた」の印
  (:attr:`~editor_app.editor.EditorWidget.backed_up_path`) が付いてしまうと、
  **控えを 1 つも取っていないのに安全網が外れる**。次の保存は控えずに
  上書きし、いちばん戻したい「開いたときの姿」が永久に失われる。
  この 4 つの中でいちばん重い。
- **控えは取れたが書き戻しに失敗した**。取った控えはそのまま残るべきで
  （＝復元をやり直せる）、印も付いたままであるべき——**次の保存が同じ
  中身をもう一度控えると、上書き後の姿ばかりが溜まり、容量の掃除で古い
  控えが押し出される**（`_backup_before_first_write` の説明どおり）。
- **選んだ控えのファイルが消えていた**。控えを取る手前で戻るので、印も
  控えの数も動いてはいけない。
- **書き戻せた後で読み直しだけ失敗した**。ここだけはファイルの側が
  変わっている。**タブが知っているディスクの姿
  (:attr:`~editor_app.editor.EditorWidget.disk_signature`) を更新しては
  いけない**——更新すると、画面に残った「書き戻す前の本文」を次の
  Ctrl+S が**何も聞かずに**上書きし、せっかく戻したファイルがまた壊れる。
  未保存マークも同じ理由で落としてはいけない。

ヘッドレスなので、ダイアログの差し替えは `test_backups.py` と同じ書き方を
なぞる（`prompt_choice` / `prompt_destructive` / `show_message_box` を
`editor_app.file_commands` の名前で差し替える）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_app import backups
from editor_app.editor import document_text
from editor_app.fileio import read_text_file_detail, write_encoded_file
from editor_app.main_window import MainWindow

#: 控えの対象になる本文（`test_backups.py` と同じ）。
JAPANESE = "こんにちは。\n日本語のテキストです。\n"

#: 保存で上書きする側の本文。**バイト数が元と違う**ものを選んである
#: （見分け札 `(更新日時, サイズ)` が確実に変わるので、更新日時の分解能に
#: 左右されずに「ディスクが変わった」を作れる）。
DAMAGED = "壊れた内容\n"

#: 差分付きの 3 択（保存直前の外部変更チェックが出すもの）。
CHANGE_PROMPT = "editor_app.file_commands.prompt_external_change"


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（`test_backups.py` と同じ）。"""
    editor.selectAll()
    editor.insertPlainText(text)


@pytest.fixture
def sjis_file(tmp_path: Path) -> Path:
    """Shift-JIS で書かれたファイル（控えの対象になる）。"""
    path = tmp_path / "sample.txt"
    path.write_bytes(JAPANESE.encode("cp932"))
    return path


@pytest.fixture
def chosen(monkeypatch: pytest.MonkeyPatch):
    """控えの一覧と確認ダイアログを塞ぐ（`test_backups.py` と同じ）。"""

    class Chosen:
        items: list[str] = []
        index: int | None = 0
        confirm: bool = True
        confirmed: bool = False

    state = Chosen()

    def fake_choice(_parent, _title, _label, items, _current):
        state.items = list(items)
        return state.index

    def fake_destructive(_parent, _title, _text, _action_label):
        state.confirmed = True
        return state.confirm

    monkeypatch.setattr("editor_app.file_commands.prompt_choice", fake_choice)
    monkeypatch.setattr("editor_app.file_commands.prompt_destructive", fake_destructive)
    return state


@pytest.fixture
def informed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """お知らせダイアログを捕まえる（塞がないとヘッドレスで固まる）。"""
    calls: list[tuple[str, str]] = []

    def fake(_parent, _icon, title, text, *_args, **_kwargs):
        calls.append((title, text))
        return None

    monkeypatch.setattr("editor_app.file_commands.show_message_box", fake)
    return calls


@pytest.fixture
def opened_with_a_backup(window: MainWindow, sjis_file: Path):
    """「前に控えが取られたファイルを、今このタブで開いた」状態を作る。

    控えは :func:`~editor_app.backups.backup_file` を直接呼んで作る（＝
    本番と同じ関数）。**このタブ自身が控えたのではない**ので
    :attr:`~editor_app.editor.EditorWidget.backed_up_path` は ``None`` の
    まま——アプリを開き直した後に復元する、という普通の使い方と同じ形。

    この形でないと「控えの印がどう動いたか」を見分けられない
    （同じタブで一度保存すると、印は初めから付いてしまう）。
    """
    backups.backup_file(sjis_file)
    editor = window.open_path(sjis_file)
    assert editor.backed_up_path is None
    assert len(backups.list_backups(sjis_file)) == 1
    return editor


def _refuse(exc: BaseException):
    """常にその例外を投げる差し替え。"""

    def fail(*_args, **_kwargs):
        raise exc

    return fail


def _snapshot_bytes(path: Path) -> list[bytes]:
    """今そのファイルに残っている控えの中身（新しい順）。"""
    return [entry.data_path.read_bytes() for entry in backups.list_backups(path)]


# ----------------------------------------------------------------------
# 控えが取れずに中止した後（安全網は外れていないこと）
# ----------------------------------------------------------------------
def test_backup_failure_leaves_the_safety_net_armed(
    window: MainWindow, sjis_file: Path, opened_with_a_backup, chosen, informed, monkeypatch
) -> None:
    """控えを取れずに復元を中止したら、「もう控えた」の印を付けてはいけない。

    印を付けてしまうと、**1 つも控えていないのに次の保存が控えを飛ばす**。
    復元を試して失敗しただけで、このタブの安全網が黙って外れることになる。
    """
    editor = opened_with_a_backup
    monkeypatch.setattr("editor_app.file_commands.backup_file", _refuse(OSError("書けません")))

    assert window.restore_from_backup() is False

    assert editor.backed_up_path is None
    assert len(backups.list_backups(sjis_file)) == 1  # 控えも増えていない
    assert informed and informed[0][0] == "復元できません"


def test_the_next_save_still_takes_its_snapshot_after_a_backup_failure(
    window: MainWindow, sjis_file: Path, opened_with_a_backup, chosen, informed, monkeypatch
) -> None:
    """控えが取れずに中止した後でも、次の保存はちゃんと控えてから上書きする。"""
    editor = opened_with_a_backup
    original = sjis_file.read_bytes()
    monkeypatch.setattr("editor_app.file_commands.backup_file", _refuse(OSError("書けません")))

    assert window.restore_from_backup() is False

    monkeypatch.setattr("editor_app.file_commands.backup_file", backups.backup_file)
    edit_text(editor, DAMAGED)
    assert window.save_editor(editor) is True

    assert len(backups.list_backups(sjis_file)) == 2
    assert _snapshot_bytes(sjis_file)[0] == original  # 上書き直前の姿が残った
    assert editor.backed_up_path == sjis_file


# ----------------------------------------------------------------------
# 選んだ控えが消えていて中止した後
# ----------------------------------------------------------------------
def test_missing_backup_leaves_the_mark_and_the_snapshots_alone(
    window: MainWindow, sjis_file: Path, opened_with_a_backup, chosen, informed, monkeypatch
) -> None:
    """控えのファイル自体が消えていた場合は、控えを取る手前で戻る。

    印も控えの数も動かないこと（**控えを取る前**の中止なので、ここで印が
    付くと控え 0 件のまま安全網が外れる）。
    """
    editor = opened_with_a_backup

    def vanish(path):
        entries = backups.list_backups(path)
        entries[0].data_path.unlink()
        return entries

    monkeypatch.setattr("editor_app.file_commands.list_backups", vanish)

    assert window.restore_from_backup() is False

    assert editor.backed_up_path is None
    assert informed and informed[0][0] == "復元できません"


# ----------------------------------------------------------------------
# 控えは取れたが書き戻しに失敗した後
# ----------------------------------------------------------------------
def test_write_failure_keeps_the_snapshot_it_just_took(
    window: MainWindow, sjis_file: Path, opened_with_a_backup, chosen, informed, monkeypatch
) -> None:
    """書き戻しに失敗しても、直前に取った「今の中身」の控えは残す。

    残しておけば復元をやり直せる。捨ててしまうと、**書き込みが途中まで
    進んでいた場合に戻せる先が無くなる**。
    """
    current = sjis_file.read_bytes()
    monkeypatch.setattr(
        "editor_app.file_commands.write_encoded_file", _refuse(PermissionError("書けません"))
    )

    assert window.restore_from_backup() is False

    assert len(backups.list_backups(sjis_file)) == 2
    assert current in _snapshot_bytes(sjis_file)
    assert informed and informed[0][0] == "復元できません"


def test_write_failure_does_not_snapshot_the_same_bytes_again(
    window: MainWindow, sjis_file: Path, opened_with_a_backup, chosen, informed, monkeypatch
) -> None:
    """書き戻しに失敗した後は、印が付いたままであること。

    ディスクの中身は今取った控えと同じなのだから、次の保存が**もう一度
    同じバイト列を控える**のは無駄なだけでなく害がある——上書き後の姿
    ばかりが溜まり、いちばん戻したい古い控えが容量の掃除で押し出される
    （:meth:`~editor_app.file_commands.FileCommandsMixin._backup_before_first_write`）。
    """
    editor = opened_with_a_backup
    monkeypatch.setattr(
        "editor_app.file_commands.write_encoded_file", _refuse(PermissionError("書けません"))
    )

    assert window.restore_from_backup() is False
    assert editor.backed_up_path == sjis_file

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", write_encoded_file)
    edit_text(editor, DAMAGED)
    assert window.save_editor(editor) is True

    assert len(backups.list_backups(sjis_file)) == 2  # 増えていない


def test_write_failure_leaves_the_tab_exactly_as_it_was(
    window: MainWindow, sjis_file: Path, opened_with_a_backup, chosen, informed, monkeypatch
) -> None:
    """書き戻せなかったのだから、タブ側は 1 つも動いていないこと。"""
    editor = opened_with_a_backup
    edit_text(editor, DAMAGED)  # 未保存の編集を持ったまま復元を試す
    before = (
        document_text(editor),
        editor.encoding,
        editor.newline,
        editor.is_modified,
        editor.disk_signature,
        editor.path,
    )
    monkeypatch.setattr(
        "editor_app.file_commands.write_encoded_file", _refuse(PermissionError("書けません"))
    )

    assert window.restore_from_backup() is False

    assert (
        document_text(editor),
        editor.encoding,
        editor.newline,
        editor.is_modified,
        editor.disk_signature,
        editor.path,
    ) == before


# ----------------------------------------------------------------------
# 書き戻せた後、読み直しだけ失敗した後
# ----------------------------------------------------------------------
def test_reload_failure_keeps_the_tab_out_of_step_with_the_disk(
    window: MainWindow, sjis_file: Path, opened_with_a_backup, chosen, informed, monkeypatch
) -> None:
    """読み直しだけ失敗したときは、**見分け札を更新しないこと**。

    ここはファイルの側だけが控えの内容に戻り、画面には書き戻す前の本文が
    残っている、という食い違った状態。見分け札を今のディスクの姿へ更新
    すると、**次の Ctrl+S が何も聞かずにその本文で上書きし**、せっかく
    戻したファイルがまた壊れる。案内文が「そのまま保存するとファイルが
    元へ戻ってしまいます」と言っている以上、止められる仕掛けは要る。
    """
    editor = opened_with_a_backup
    edit_text(editor, DAMAGED)
    window.save_editor(editor)  # ディスク＝壊れた内容、見分け札もその姿
    known = editor.disk_signature
    monkeypatch.setattr(
        "editor_app.file_commands.read_text_file_detail", _refuse(OSError("読み直せません"))
    )

    assert window.restore_from_backup() is False

    # ファイルは戻っているのに、タブが知っているのは戻す前の姿のまま。
    assert sjis_file.read_bytes() == JAPANESE.encode("cp932")
    assert editor.disk_signature == known

    # だから次の保存は黙って上書きせず、外部変更として止まる。
    monkeypatch.setattr("editor_app.file_commands.read_text_file_detail", read_text_file_detail)
    prompted: list[str] = []

    def fake_prompt(_parent, *, name: str, diff_text: str) -> str | None:
        prompted.append(name)
        return None  # キャンセル

    monkeypatch.setattr(CHANGE_PROMPT, fake_prompt)
    edit_text(editor, DAMAGED)

    assert window.save_editor(editor) is False
    assert prompted == [sjis_file.name]
    assert sjis_file.read_bytes() == JAPANESE.encode("cp932")  # 戻したままである


def test_reload_failure_keeps_the_unsaved_mark(
    window: MainWindow, sjis_file: Path, opened_with_a_backup, chosen, informed, monkeypatch
) -> None:
    """読み直しに失敗したのなら、未保存マークも落としてはいけない。

    本文は書き戻す前のまま画面に残っている。「保存済み」に見せると、
    タブを閉じるときの確認が出ず、その編集が黙って消える。
    """
    editor = opened_with_a_backup
    edit_text(editor, DAMAGED)
    assert editor.is_modified is True
    monkeypatch.setattr(
        "editor_app.file_commands.read_text_file_detail", _refuse(OSError("読み直せません"))
    )

    assert window.restore_from_backup() is False

    assert editor.is_modified is True
    assert document_text(editor) == DAMAGED


# ----------------------------------------------------------------------
# 取りやめた後（控えを取る手前で戻る 2 つの経路）
# ----------------------------------------------------------------------
def test_cancelling_the_confirmation_leaves_the_mark_alone(
    window: MainWindow, sjis_file: Path, opened_with_a_backup, chosen, informed
) -> None:
    """確認を取りやめたら、控えの印は付かない（安全網は張られたまま）。"""
    editor = opened_with_a_backup
    chosen.confirm = False

    assert window.restore_from_backup() is False

    assert editor.backed_up_path is None
    assert len(backups.list_backups(sjis_file)) == 1


def test_cancelling_the_list_leaves_the_mark_alone(
    window: MainWindow, sjis_file: Path, opened_with_a_backup, chosen, informed
) -> None:
    """一覧を取りやめたときも同じ（確認すら出ていない）。"""
    editor = opened_with_a_backup
    chosen.index = None

    assert window.restore_from_backup() is False

    assert chosen.confirmed is False
    assert editor.backed_up_path is None
    assert len(backups.list_backups(sjis_file)) == 1
