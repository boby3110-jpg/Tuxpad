"""「バックアップから復元」の**見える側**——ダイアログの文面そのもの（70 回目）.

48 回目（`test_restore_failure_state.py`）は「失敗した**後**に残る状態」を
見張るようにした。そこで見ていなかったのが**文面**で、既存のテストは

- お知らせの**題だけ**（`informed[0][0] == "復元できません"`）
- 本文のうち**「控え」の 2 文字だけ**（`"控え" in informed[0][1]`）

しか縛っていなかった。この経路は**止めるだけで逃げ道が無い**——控えが
無ければ何もしない、失敗したら中止する——ので、**文面が唯一の出口**に
なる。文面が落ちたとき、利用者には次のように見える:

- **どのファイルの話か分からない**（複数のタブを開いて作業しているので、
  「戻せる控えはありません」だけでは、どのタブに対して出たのか分からない）
- **なぜ控えが無いのかが分からない**ので、「控えを取る条件」を
  満たしていないだけなのか、控えが消えたのかを区別できない
- **いつの控えに戻すのかが分からない**まま、取り消せない置き換えを押す
- **ファイルが戻ったのか戻っていないのかが分からない**（この 2 つは
  その後やることが正反対になる。「変更していません」なら別の手で復元を
  試す、「戻っています」ならタブを閉じて開き直す——間違えると、画面に
  残っている壊れた本文で、せっかく戻ったファイルを上書きしてしまう）

どれも「文面を 1 行落とす」だけで起こり、**テストは全て緑のまま**だった。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from editor_app.main_window import MainWindow

#: 控えの対象になる、日本語レガシー文字コードの本文（`test_backups.py` と同じ）。
JAPANESE = "こんにちは。\n日本語のテキストです。\n"


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（`test_backups.py` と同じ）。"""
    editor.selectAll()
    editor.insertPlainText(text)


def _backdate(data_path: Path, moment) -> None:
    """控えの索引に入っている日時を書き換える（`test_backups.py` と同じ）。

    `backups.backup_file(now=...)` は控えの**ファイル名**しか動かさず、
    一覧に出る日時（索引の `saved_at`）には常に「今」が入る。日時の違う
    控えを 2 件用意するには、索引を直接書き換えるしかない。
    """
    import json

    from editor_app import backups

    index_path = data_path.with_suffix(backups.INDEX_SUFFIX)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index[backups.INDEX_SAVED_AT_KEY] = moment.isoformat(timespec="microseconds")
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def sjis_file(tmp_path: Path) -> Path:
    """Shift-JIS で書かれたファイル（控えの対象になる）。"""
    path = tmp_path / "sample.txt"
    path.write_bytes(JAPANESE.encode("cp932"))
    return path


class Shown:
    """`show_message_box` に渡された**印・題・本文の 3 つとも**控える。

    既存の `informed` フィクスチャ（`test_backups.py` 等）は題と本文しか
    覚えないので、**印（情報／警告／失敗）を取り違えても緑のまま**だった。
    お知らせか失敗かは、利用者が最初に目で受け取る情報なので一緒に見る。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[QMessageBox.Icon, str, str]] = []

    def __bool__(self) -> bool:
        return bool(self.calls)

    @property
    def icon(self) -> QMessageBox.Icon:
        return self.calls[0][0]

    @property
    def title(self) -> str:
        return self.calls[0][1]

    @property
    def text(self) -> str:
        return self.calls[0][2]


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> Shown:
    """お知らせ・失敗のダイアログを、印まで含めて捕まえる。"""
    captured = Shown()

    def fake(_parent, icon, title, text, *_args, **_kwargs):
        captured.calls.append((icon, title, text))
        return None

    monkeypatch.setattr("editor_app.file_commands.show_message_box", fake)
    return captured


class Asked:
    """控えの一覧（`prompt_choice`）と置き換えの確認（`prompt_destructive`）.

    `test_backups.py` の `chosen` は一覧の**項目**と確認の**本文・ボタン**しか
    覚えない。ここでは**一覧の題と問いかけの文**も控える（どのファイルの
    どの時点に戻すのかは、この 2 つにしか出ていない）。
    """

    def __init__(self) -> None:
        self.list_title = ""
        self.list_label = ""
        self.items: list[str] = []
        self.index: int | None = 0
        self.confirm_title = ""
        self.confirm_text = ""
        self.confirm_label = ""
        self.confirmed = False
        self.confirm = True


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> Asked:
    """復元の 2 つの小窓を、題・問いかけ・ボタンの文字まで控える。"""
    state = Asked()

    def fake_choice(_parent, title, label, items, _current):
        state.list_title = title
        state.list_label = label
        state.items = list(items)
        return state.index

    def fake_destructive(_parent, title, text, action_label):
        state.confirm_title = title
        state.confirm_text = text
        state.confirm_label = action_label
        state.confirmed = True
        return state.confirm

    monkeypatch.setattr("editor_app.file_commands.prompt_choice", fake_choice)
    monkeypatch.setattr("editor_app.file_commands.prompt_destructive", fake_destructive)
    return state


@pytest.fixture
def saved_once(window: MainWindow, sjis_file: Path, asked: Asked):
    """控えが 1 件ある状態（Shift-JIS のファイルを開いて 1 回上書き保存）。"""
    editor = window.open_path(sjis_file)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)
    return editor


# ----------------------------------------------------------------------
# 戻せる控えが無いときのお知らせ（_warn_no_backup）
# ----------------------------------------------------------------------
def test_no_backup_notice_names_the_file(
    window: MainWindow, tmp_path: Path, shown: Shown, asked: Asked
) -> None:
    """**どのファイルに控えが無いのかを言う。**

    タブを何枚も開いて作業しているので、「戻せる控えはありません」だけでは
    どのタブに対しての返事なのか分からない（押し間違えて別のタブで実行して
    しまったのか、本当に控えが無いのかも区別できない）。
    """
    path = tmp_path / "さくら.txt"
    path.write_text(JAPANESE, encoding="utf-8")
    window.open_path(path)

    assert window.restore_from_backup() is False

    assert shown
    assert "さくら.txt" in shown.text


def test_no_backup_notice_explains_when_backups_are_taken(
    window: MainWindow, tmp_path: Path, shown: Shown, asked: Asked
) -> None:
    """**なぜ控えが無いのかを言う**（控えを取る条件を添える）。

    控えを取るのは「日本語の文字コードのファイルを開いて、そのタブで
    **最初に**保存する直前の 1 回」だけ。この説明が無いと、利用者は
    「控えが消えた」のか「そもそも取られていない」のかを区別できず、
    安全網が外れているのかどうかを判断できない。
    """
    path = tmp_path / "utf8.txt"
    path.write_text(JAPANESE, encoding="utf-8")
    window.open_path(path)

    assert window.restore_from_backup() is False

    assert shown
    text = shown.text
    # 条件は「日本語レガシー文字コード」かつ「そのタブで最初の保存の直前」。
    assert "Shift-JIS" in text
    assert "最初に保存する" in text


def test_no_backup_notice_does_not_promise_utf8_backups(
    window: MainWindow, tmp_path: Path, shown: Shown, asked: Asked
) -> None:
    """**UTF-8 のファイルで控えを取るとは書かない。**

    控えを取るのは文字コード誤判定の取りこぼしを受け止めるためで、
    UTF-8 は対象外。ここで UTF-8 と案内すると、利用者は**取られていない
    控えを当てにして**上書きしてしまう（安全網があると思い込む方が、
    無いと知っているより危ない）。
    """
    path = tmp_path / "utf8.txt"
    path.write_text(JAPANESE, encoding="utf-8")
    window.open_path(path)

    assert window.restore_from_backup() is False

    assert shown
    assert "UTF-8" not in shown.text


def test_no_backup_notice_is_information_not_an_error(
    window: MainWindow, tmp_path: Path, shown: Shown, asked: Asked
) -> None:
    """控えが無いのは**失敗ではない**ので、赤い失敗の印で出さない。

    「控えを取る条件を満たしていなかった」だけなのに失敗の印で出ると、
    利用者は壊れたのかと思って探し回ることになる。
    """
    path = tmp_path / "utf8.txt"
    path.write_text(JAPANESE, encoding="utf-8")
    window.open_path(path)

    window.restore_from_backup()

    assert shown
    assert shown.icon == QMessageBox.Icon.Information


def test_no_backup_notice_says_which_command_it_answers(
    window: MainWindow, tmp_path: Path, shown: Shown, asked: Asked
) -> None:
    """題は、どのメニュー操作への返事なのかが分かる文字にする。"""
    path = tmp_path / "utf8.txt"
    path.write_text(JAPANESE, encoding="utf-8")
    window.open_path(path)

    window.restore_from_backup()

    assert shown
    assert shown.title == "バックアップから復元"


# **無題のタブの案内**（「まだ保存していないので控えもありません」）は、
# 既存の `test_backups.py::test_restore_on_untitled_tab_says_so` が
# 「保存されていない」を見ているので、ここでは重ねない（変異
# `rw-no-backup-untitled-no-save-hint` はそのテストが捕まえた）。


# ----------------------------------------------------------------------
# どの控えに戻すかを選ぶ一覧（prompt_choice）
# ----------------------------------------------------------------------
def test_backup_list_names_the_file(
    window: MainWindow, tmp_path: Path, asked: Asked
) -> None:
    """一覧の問いかけが、**どのファイルを戻すのか**を言う。

    一覧に並ぶのは日時と大きさだけなので、ファイル名はここにしか出ない。
    別のタブで実行してしまったことに気づける最後の場所でもある。
    """
    path = tmp_path / "あじさい.txt"
    path.write_bytes(JAPANESE.encode("cp932"))
    editor = window.open_path(path)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)
    asked.index = None  # 一覧を見るだけ

    window.restore_from_backup()

    assert "あじさい.txt" in asked.list_label


def test_backup_list_explains_what_the_entries_are(
    window: MainWindow, sjis_file: Path, asked: Asked, saved_once
) -> None:
    """一覧の問いかけが、並んでいる日時が**何の時点**なのかを言う。

    「2026-10-04 12:00:00」だけ見せられても、保存した時刻なのか控えた
    時刻なのか分からない（＝どれを選べばよいか決められない）。
    """
    asked.index = None

    window.restore_from_backup()

    assert "上書きされる直前" in asked.list_label


def test_backup_list_says_which_command_it_belongs_to(
    window: MainWindow, sjis_file: Path, asked: Asked, saved_once
) -> None:
    """一覧の題も、どの操作の途中なのかが分かる文字にする。"""
    asked.index = None

    window.restore_from_backup()

    assert asked.list_title == "バックアップから復元"


# ----------------------------------------------------------------------
# 置き換えの確認（prompt_destructive）——取り消せない操作の直前
# ----------------------------------------------------------------------
def test_confirmation_names_the_backup_being_restored(
    window: MainWindow, sjis_file: Path, asked: Asked, saved_once
) -> None:
    """**選んだ控えの日時を、確認にもう一度出す。**

    一覧で 1 行ずれて選んでしまう事故をここで止めるのが、この確認の役目。
    日時が出ていないと「何に戻すのか」を確かめようがなく、確認が
    「はい」を押すだけの儀式になる。
    """
    from editor_app import backups

    (entry,) = backups.list_backups(sjis_file)
    asked.index = 0

    window.restore_from_backup()

    assert asked.confirmed is True
    assert entry.saved_at.strftime("%Y-%m-%d %H:%M:%S") in asked.confirm_text


def test_confirmation_shows_the_backup_of_the_chosen_row(
    window: MainWindow, sjis_file: Path, asked: Asked
) -> None:
    """控えが複数あるとき、確認に出る日時は**選んだ行のもの**であること。

    先頭の行の日時を出してしまうと、確認が「1 行ずれ」を止められない
    どころか、**選んだ行とは別の控えに戻ると信じ込ませる**。
    """
    from datetime import datetime, timedelta

    from editor_app import backups

    # 日時がはっきり違う控えを 2 件作る（3 日前と今）。
    #
    # **`backup_file(now=...)` では日時はずれない。** `now` が決めるのは
    # 控えのファイル名のほうで、一覧に出る日時（索引の `saved_at`）は
    # 常に `datetime.now()` が入る。そのため同じ秒のうちに 2 件作ると、
    # 一覧でも確認でも**同じ日時の 2 行**になってしまい、「選んだ行の
    # 日時が出ているか」を確かめられない（最初にこれで取り違えた）。
    # 掃除のテストと同じく、索引の日時を直接書き換える。
    sjis_file.write_bytes("古い内容\n".encode("cp932"))
    old_copy = backups.backup_file(sjis_file)
    assert old_copy is not None
    _backdate(old_copy, datetime.now() - timedelta(days=3))
    sjis_file.write_bytes(JAPANESE.encode("cp932"))
    backups.backup_file(sjis_file)

    entries = backups.list_backups(sjis_file)  # 新しい順
    assert len(entries) == 2
    newer, older = entries

    window.open_path(sjis_file)
    asked.index = 1  # 古い方（3 日前）を選ぶ
    asked.confirm = False  # 文面だけ見る

    window.restore_from_backup()

    assert older.saved_at.strftime("%Y-%m-%d %H:%M:%S") in asked.confirm_text
    assert newer.saved_at.strftime("%Y-%m-%d %H:%M:%S") not in asked.confirm_text


def test_confirmation_timestamp_includes_the_date(
    window: MainWindow, sjis_file: Path, asked: Asked, saved_once
) -> None:
    """確認に出す日時には**日付まで**入れる。

    控えは容量の掃除に当たるまで残るので、何日も前のものが並ぶ。
    時刻だけだと「今日の 14:05」と「3 日前の 14:05」を見分けられない。
    """
    from editor_app import backups

    (entry,) = backups.list_backups(sjis_file)
    asked.confirm = False

    window.restore_from_backup()

    assert entry.saved_at.strftime("%Y-%m-%d") in asked.confirm_text


def test_confirmation_names_the_file_being_replaced(
    window: MainWindow, tmp_path: Path, asked: Asked
) -> None:
    """確認が、**どのファイルの今の内容が消えるのか**を言う。"""
    path = tmp_path / "すみれ.txt"
    path.write_bytes(JAPANESE.encode("cp932"))
    editor = window.open_path(path)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)
    asked.confirm = False

    window.restore_from_backup()

    assert "すみれ.txt" in asked.confirm_text


def test_confirmation_says_the_restore_itself_can_be_undone(
    window: MainWindow, sjis_file: Path, asked: Asked, saved_once
) -> None:
    """**この復元もやり直せる**ことを言う（押せなくなるのを防ぐ）。

    「今の内容は失われます」だけ読むと、間違っていたら終わりだと見える。
    実際は復元の直前にも今の内容を控えるので取り消せる。この一行が無いと、
    本当に戻したい人が確認の前で止まってしまう。
    """
    asked.confirm = False

    window.restore_from_backup()

    assert "やり直せます" in asked.confirm_text


def test_confirmation_button_says_what_it_does(
    window: MainWindow, sjis_file: Path, asked: Asked, saved_once
) -> None:
    """ボタンの文字は「何が起きるか」が読めるものにする。

    `prompt_destructive` の約束（ふつうの操作と同じに見えるボタンにしない）
    は「OK ではない」だけでは足りない。「続ける」のような、押した結果が
    読めない文字も同じように危ない。
    """
    asked.confirm = False

    window.restore_from_backup()

    assert asked.confirm_label not in ("OK", "続ける", "はい", "")
    assert "控え" in asked.confirm_label or "戻す" in asked.confirm_label


def test_confirmation_says_which_command_it_belongs_to(
    window: MainWindow, sjis_file: Path, asked: Asked, saved_once
) -> None:
    """確認の題も、どの操作の途中なのかが分かる文字にする。"""
    asked.confirm = False

    window.restore_from_backup()

    assert asked.confirm_title == "バックアップから復元"


# ----------------------------------------------------------------------
# 失敗の知らせ（_report_restore_failure）
# ----------------------------------------------------------------------
def test_failure_notice_includes_the_reason(
    window: MainWindow,
    sjis_file: Path,
    asked: Asked,
    shown: Shown,
    saved_once,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**失敗の理由（例外の文面）をそのまま見せる。**

    ここで理由を落とすと「復元できませんでした」だけになり、利用者は
    打つ手が無い（権限が足りないのか・ディスクが満杯なのか・控えが
    消えたのかで、次にやることが全く違う）。
    """

    def refuse(*_args, **_kwargs):
        raise PermissionError("書き込めません")

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse)

    assert window.restore_from_backup() is False

    assert shown
    assert "書き込めません" in shown.text


def test_failure_notice_says_the_file_was_left_alone(
    window: MainWindow,
    sjis_file: Path,
    asked: Asked,
    shown: Shown,
    saved_once,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**ファイルに手を付けていないことを言う。**

    これが無いと、利用者は「中途半端に書き換わったかもしれない」と
    疑うことになる（実際には 1 バイトも触っていない）。
    """

    def refuse(*_args, **_kwargs):
        raise PermissionError("書き込めません")

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse)

    window.restore_from_backup()

    assert shown
    assert "ファイルは変更していません" in shown.text


def test_failure_notice_does_not_claim_the_file_was_restored(
    window: MainWindow,
    sjis_file: Path,
    asked: Asked,
    shown: Shown,
    saved_once,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**復元できなかったのに「控えの内容に戻っている」と言わない。**

    この 2 つの案内は、その後やることが正反対になる。「戻っている」と
    誤って伝えると、利用者はタブを閉じて開き直し——**控えの内容だと
    思って、実は壊れたままの中身を正解として扱ってしまう**。
    """

    def refuse(*_args, **_kwargs):
        raise PermissionError("書き込めません")

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse)

    window.restore_from_backup()

    assert shown
    assert "控えの内容に戻っています" not in shown.text


def test_failure_notice_names_the_file(
    window: MainWindow,
    tmp_path: Path,
    asked: Asked,
    shown: Shown,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失敗の知らせも、**どのファイルの話か**を言う。"""
    path = tmp_path / "たんぽぽ.txt"
    path.write_bytes(JAPANESE.encode("cp932"))
    editor = window.open_path(path)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)

    def refuse(*_args, **_kwargs):
        raise PermissionError("書き込めません")

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse)

    window.restore_from_backup()

    assert shown
    assert "たんぽぽ.txt" in shown.text


# ----------------------------------------------------------------------
# 書き戻せたのに読み直せなかったときの知らせ（いちばん取り違えが危ない）
# ----------------------------------------------------------------------
def test_reload_failure_notice_includes_the_reason(
    window: MainWindow,
    sjis_file: Path,
    asked: Asked,
    shown: Shown,
    saved_once,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """読み直せなかった知らせにも、**理由をそのまま**添える。"""

    def refuse(*_args, **_kwargs):
        raise OSError("読み直せません")

    monkeypatch.setattr("editor_app.file_commands.read_text_file_detail", refuse)

    assert window.restore_from_backup() is False

    assert shown
    assert "読み直せません" in shown.text


def test_reload_failure_notice_names_the_file(
    window: MainWindow,
    tmp_path: Path,
    asked: Asked,
    shown: Shown,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """読み直せなかった知らせも、**どのファイルの話か**を言う。

    ここは「このタブを閉じて開き直してください」と頼む案内なので、
    どのタブのことか分からないと、そのとおりに動けない。
    """
    path = tmp_path / "れんげ.txt"
    path.write_bytes(JAPANESE.encode("cp932"))
    editor = window.open_path(path)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)

    def refuse(*_args, **_kwargs):
        raise OSError("読み直せません")

    monkeypatch.setattr("editor_app.file_commands.read_text_file_detail", refuse)

    window.restore_from_backup()

    assert shown
    assert "れんげ.txt" in shown.text


def test_reload_failure_notice_is_marked_as_a_failure(
    window: MainWindow,
    sjis_file: Path,
    asked: Asked,
    shown: Shown,
    saved_once,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """読み直せなかったのは**失敗**なので、ふつうのお知らせの印で出さない。

    この案内は「このまま保存するとファイルが元へ戻ってしまう」まで
    含んでいる。情報の印で出すと読み飛ばされ、画面に残った本文で
    せっかく戻ったファイルを上書きしてしまう。
    """

    def refuse(*_args, **_kwargs):
        raise OSError("読み直せません")

    monkeypatch.setattr("editor_app.file_commands.read_text_file_detail", refuse)

    window.restore_from_backup()

    assert shown
    assert shown.icon == QMessageBox.Icon.Critical
