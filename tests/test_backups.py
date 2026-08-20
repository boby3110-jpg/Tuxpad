"""上書き前の控えと、そこからの復元のテスト（引き継ぎ ⑦）.

⑤（開いた時点で拮抗を知らせる）と⑥（保存直前に「怪しい」と警告する）は
どちらも**判定の当たり外れに左右される目安**で、取りこぼしがある。ここは
その取りこぼしを受け止める**最後の安全網**なので、固定すべきことは
「気づけるか」ではなく次の 2 つ:

- **上書きされて消えるバイト列が、デコードを一切挟まずに残ること。**
  ここでテキストとして読み書きし直すと、控え自体が文字コード誤判定の
  被害を受けて元に戻せなくなる（＝控えた意味が無くなる）。
- **復元がアプリの中だけで完結すること。** 利用者の明確な指示で、
  「フォルダを開くので、あとは手作業でコピーしてください」にはしない。
  復元操作そのものも取り消せる（復元前の内容がまた控えに残る）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from editor_app import backups
from editor_app.editor import document_text
from editor_app.main_window import MainWindow

#: 日本語レガシー文字コードの本文（cp932 でも euc_jp でも読めてしまう類ではなく、
#: 素直に読める普通の日本語）。
JAPANESE = "こんにちは。\n日本語のテキストです。\n"


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする（test_save_file.py と同じ）。"""
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
    """控えの一覧（``prompt_choice``）と確認（``prompt_destructive``）を捕まえる。

    ``chosen.items`` に一覧へ出した文言が入り、``chosen.index`` で何番目を
    選ぶか（None ならキャンセル）、``chosen.confirm`` で確認ダイアログの
    答えを決められる。
    """

    class Chosen:
        items: list[str] = []
        label: str = ""
        confirm_text: str = ""
        index: int | None = 0
        confirm: bool = True
        confirmed: bool = False

    state = Chosen()

    def fake_choice(_parent, _title, _label, items, _current):
        state.items = list(items)
        return state.index

    def fake_destructive(_parent, _title, text, action_label):
        state.confirm_text = text
        state.label = action_label
        state.confirmed = True
        return state.confirm

    monkeypatch.setattr("editor_app.file_commands.prompt_choice", fake_choice)
    monkeypatch.setattr("editor_app.file_commands.prompt_destructive", fake_destructive)
    return state


@pytest.fixture
def informed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """「控えはありません」等のお知らせダイアログを捕まえる。"""
    calls: list[tuple[str, str]] = []

    def fake(_parent, _icon, title, text, *args, **kwargs):
        calls.append((title, text))
        return None

    monkeypatch.setattr("editor_app.file_commands.show_message_box", fake)
    return calls


# ----------------------------------------------------------------------
# backups: 控えの置き場と索引（Qt に依存しない部分）
# ----------------------------------------------------------------------
def test_backup_root_follows_xdg_cache_home(tmp_path: Path, monkeypatch) -> None:
    """置き場は ``XDG_CACHE_HOME`` に従う（無ければ ``~/.cache``）。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert backups.backup_root() == tmp_path / "xdg" / "tuxpad" / "backups"

    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    assert backups.backup_root() == tmp_path / "home" / ".cache" / "tuxpad" / "backups"


def test_backup_keeps_the_raw_bytes(tmp_path: Path) -> None:
    """**デコード・再エンコードを挟まない**（ここがこの機能の要）。

    文字コードとして解釈できないバイト列でも、1 バイトも変えずに残ること。
    """
    path = tmp_path / "broken.txt"
    data = b"\x82\xa0\x82\xa2\xff\xfe\x00 not valid in any codec \x81"
    path.write_bytes(data)

    copy = backups.backup_file(path)

    assert copy.read_bytes() == data


def test_backup_records_the_original_path_and_time(tmp_path: Path) -> None:
    """索引から「元の絶対パス」と「日時」を後で引ける。"""
    path = tmp_path / "sample.txt"
    path.write_bytes(JAPANESE.encode("cp932"))

    before = datetime.now()
    backups.backup_file(path)

    (entry,) = backups.list_backups(path)
    assert entry.source_path == path.resolve()
    assert before <= entry.saved_at <= datetime.now()
    assert entry.size == len(JAPANESE.encode("cp932"))


def test_backup_of_missing_file_does_nothing(tmp_path: Path) -> None:
    """まだディスクに無いファイル（無題タブの初回保存）では控えない。"""
    assert backups.backup_file(tmp_path / "not-yet.txt") is None
    assert backups.all_backups() == []


def test_backups_are_listed_newest_first(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    stamps = []
    for index in range(3):
        path.write_text(f"{index} 回目\n", encoding="utf-8")
        backups.backup_file(path)
        stamps.append(path.read_bytes())

    entries = backups.list_backups(path)

    assert [entry.data_path.read_bytes() for entry in entries] == list(reversed(stamps))
    assert entries[0].saved_at >= entries[-1].saved_at


def test_backups_of_other_files_are_not_listed(tmp_path: Path) -> None:
    """索引に入っている元パスで突き合わせる（別のファイルの控えは混ざらない）。"""
    one = tmp_path / "one.txt"
    other = tmp_path / "other.txt"
    one.write_text("一\n", encoding="utf-8")
    other.write_text("二\n", encoding="utf-8")
    backups.backup_file(one)
    backups.backup_file(other)

    (entry,) = backups.list_backups(one)
    assert entry.data_path.read_bytes() == "一\n".encode()


def test_unreadable_index_drops_only_that_entry(tmp_path: Path) -> None:
    """索引が壊れた控えは一覧から外れるだけ（他の控えは残る）。"""
    path = tmp_path / "sample.txt"
    path.write_text("一\n", encoding="utf-8")
    broken = backups.backup_file(path)
    path.write_text("二\n", encoding="utf-8")
    backups.backup_file(path)
    broken.with_suffix(backups.INDEX_SUFFIX).write_text("{ これは JSON ではない", encoding="utf-8")

    (entry,) = backups.list_backups(path)
    assert entry.data_path.read_bytes() == "二\n".encode()


# ----------------------------------------------------------------------
# backups: 掃除（期限切れ・容量超過）
# ----------------------------------------------------------------------
def test_prune_removes_expired_backups(tmp_path: Path) -> None:
    """保持期間を過ぎた控えを捨てる（索引も一緒に消える）。"""
    path = tmp_path / "sample.txt"
    path.write_text("古い\n", encoding="utf-8")
    old = backups.backup_file(path, now=datetime.now() - timedelta(days=40))
    path.write_text("新しい\n", encoding="utf-8")
    fresh = backups.backup_file(path)
    # 索引の日時（＝掃除が見る日時）も古くしておく。
    _backdate(old, datetime.now() - timedelta(days=40))

    removed = backups.prune()

    assert removed == [old]
    assert not old.exists()
    assert not old.with_suffix(backups.INDEX_SUFFIX).exists()
    assert fresh.exists()


def test_prune_removes_oldest_until_under_the_size_limit(tmp_path: Path) -> None:
    """容量の上限を超えたら、**古いものから**捨てる。"""
    path = tmp_path / "sample.txt"
    made = []
    for index in range(4):
        path.write_bytes(bytes([index]) * 100)
        made.append(backups.backup_file(path))

    removed = backups.prune(max_total_bytes=250)

    # 100 バイト × 4 = 400 → 250 に収まるまで古い方から 2 つ消える。
    assert removed == made[:2]
    assert [entry.data_path for entry in backups.list_backups(path)] == [made[3], made[2]]


def test_prune_keeps_the_backup_just_taken(tmp_path: Path) -> None:
    """**控えた瞬間にその控えを掃除が持っていかない**（安全網を張った直後に外さない）。

    1 つのファイルが上限より大きい場合に起きる。上限を守るより「今この
    ファイルを戻せること」の方が大事。
    """
    path = tmp_path / "big.txt"
    path.write_bytes(b"x" * 500)

    copy = backups.backup_file(path)

    assert copy.exists()
    assert backups.prune(protected=copy, max_total_bytes=10) == []
    assert copy.exists()


def test_prune_after_backup_uses_the_configured_limits(tmp_path: Path, monkeypatch) -> None:
    """控えを取るたびに掃除する（専用のバックグラウンド処理は持たない）。

    上限は定数 1 か所（``RETENTION_DAYS`` / ``MAX_TOTAL_BYTES``）で変えられる。
    """
    monkeypatch.setattr(backups, "MAX_TOTAL_BYTES", 150)
    path = tmp_path / "sample.txt"
    path.write_bytes(b"a" * 100)
    first = backups.backup_file(path)
    path.write_bytes(b"b" * 100)
    second = backups.backup_file(path)

    assert not first.exists()
    assert second.exists()


def test_prune_removes_empty_directories(tmp_path: Path) -> None:
    """控えが 1 つも無くなったディレクトリは残さない。"""
    path = tmp_path / "sample.txt"
    path.write_text("一\n", encoding="utf-8")
    copy = backups.backup_file(path)
    _backdate(copy, datetime.now() - timedelta(days=90))

    backups.prune()

    assert list(backups.backup_root().iterdir()) == []


def test_two_backups_in_the_same_moment_both_survive(tmp_path: Path) -> None:
    """同じ時刻に 2 回控えても、**前の控えを踏み潰さない**。

    名前は日時から作るので、時計の精度より速く 2 回控えると名前がぶつかる。
    黙って上書きすると、いちばん戻したい方が消えかねない。
    """
    path = tmp_path / "sample.txt"
    moment = datetime(2026, 8, 17, 18, 15, 59)
    path.write_bytes(b"one")
    first = backups.backup_file(path, now=moment)
    path.write_bytes(b"two")
    second = backups.backup_file(path, now=moment)

    assert first != second
    assert first.read_bytes() == b"one"
    assert second.read_bytes() == b"two"


def test_backup_refuses_rather_than_overwrite_when_no_name_is_free(
    tmp_path: Path, monkeypatch
) -> None:
    """名前をずらしきっても空きが無いなら、**踏み潰さずに諦める**。

    起きるはずのない場合だが、ここで黙って上書きすると「いちばん戻したい
    控え」を消しかねない。失敗として投げ、呼び出し側（保存なら続行、
    復元なら中止）に判断させる。
    """
    monkeypatch.setattr(backups, "_MAX_NAME_ATTEMPTS", 2)
    path = tmp_path / "sample.txt"
    path.write_bytes(b"one")
    moment = datetime(2026, 8, 17, 18, 15, 59)
    taken = [backups.backup_file(path, now=moment), backups.backup_file(path, now=moment)]

    with pytest.raises(OSError):
        backups.backup_file(path, now=moment)

    # 先に取ってあった 2 件は 1 バイトも変わっていない。
    assert [copy.read_bytes() for copy in taken] == [b"one", b"one"]


def test_prune_keeps_going_when_an_empty_directory_cannot_be_removed(
    tmp_path: Path, monkeypatch
) -> None:
    """空になった置き場を消せなくても、掃除そのものは成功として終わる。

    ディレクトリが消せない（読み取り専用・別プロセスが握っている等）のは
    控えの中身とは関係が無い。ここで例外を上げると、**控えを取るたびに
    掃除も一緒に走る**ので保存の経路まで巻き込まれる。
    """
    path = tmp_path / "sample.txt"
    path.write_text("一\n", encoding="utf-8")
    copy = backups.backup_file(path)
    _backdate(copy, datetime.now() - timedelta(days=90))

    def refuse(_self):
        raise OSError("消せません")

    monkeypatch.setattr(Path, "rmdir", refuse)

    assert backups.prune() == [copy]

    # 控え本体は消えていて、空になった置き場だけが残る。
    assert not copy.exists()
    assert [d.name for d in backups.backup_root().iterdir()] == [copy.parent.name]


def test_prune_on_an_empty_cache_does_nothing(tmp_path: Path) -> None:
    """まだ一度も控えていない（置き場すら無い）状態でも落ちない。"""
    assert not backups.backup_root().exists()
    assert backups.prune() == []
    assert backups.all_backups() == []


def test_size_is_described_in_readable_units() -> None:
    """一覧に出す大きさは、桁を数えなくても読める表記にする。"""
    assert backups.describe_size(512) == "512 B"
    assert backups.describe_size(2048) == "2.0 KB"
    assert backups.describe_size(3 * 1024 * 1024) == "3.0 MB"


def _backdate(data_path: Path, moment: datetime) -> None:
    """控えの索引に入っている日時を書き換える（掃除のテスト用）。"""
    index_path = data_path.with_suffix(backups.INDEX_SUFFIX)
    import json

    index = json.loads(index_path.read_text(encoding="utf-8"))
    index[backups.INDEX_SAVED_AT_KEY] = moment.isoformat(timespec="microseconds")
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")


# ----------------------------------------------------------------------
# 7-A: 保存の直前に控える
# ----------------------------------------------------------------------
def test_first_save_backs_up_the_bytes_on_disk(window: MainWindow, sjis_file: Path) -> None:
    """日本語レガシー文字コードのファイルは、初めての保存の直前に控えられる。

    控えは**保存前のディスクの中身とバイト単位で一致**すること。
    """
    original = sjis_file.read_bytes()
    editor = window.open_path(sjis_file)
    edit_text(editor, "書き換えました\n")

    assert window.save_editor(editor) is True

    (entry,) = backups.list_backups(sjis_file)
    assert entry.data_path.read_bytes() == original
    assert sjis_file.read_bytes() != original  # 保存自体は済んでいる


def test_utf8_file_is_not_backed_up(window: MainWindow, tmp_path: Path) -> None:
    """対象は日本語レガシー文字コードだけ（UTF-8 は誤判定で壊れようがない）。"""
    path = tmp_path / "utf8.txt"
    path.write_text(JAPANESE, encoding="utf-8")
    editor = window.open_path(path)
    edit_text(editor, "書き換えました\n")

    assert window.save_editor(editor) is True

    assert backups.list_backups(path) == []


def test_second_save_does_not_add_a_backup(window: MainWindow, sjis_file: Path) -> None:
    """控えるのは「そのタブがそのファイルへ初めて書き込む直前」の 1 回だけ。

    毎回控えると、**上書きされた後の姿ばかりが溜まり**、いちばん戻したい
    「開いたときの姿」が容量の掃除で押し出されてしまう。
    """
    original = sjis_file.read_bytes()
    editor = window.open_path(sjis_file)

    edit_text(editor, "1 回目\n")
    window.save_editor(editor)
    edit_text(editor, "2 回目\n")
    window.save_editor(editor)

    (entry,) = backups.list_backups(sjis_file)
    assert entry.data_path.read_bytes() == original


def test_untitled_first_save_creates_no_backup(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    """まだディスクに無いファイルへの初回保存では、失うものが無いので控えない。"""
    target = tmp_path / "new.txt"
    editor = window.current_editor()
    edit_text(editor, JAPANESE)
    editor.encoding = "cp932"
    monkeypatch.setattr(
        "editor_app.file_commands.FileCommandsMixin._save_dialog_path", lambda _s, _e: target
    )
    _accept_save_dialog(monkeypatch, target)

    assert window.save_editor_as(editor) is True

    assert backups.list_backups(target) == []


def test_cancelled_save_creates_no_backup(
    window: MainWindow, sjis_file: Path, monkeypatch
) -> None:
    """保存を取りやめたときは控えも増えない（控えるのは関門を全て通った後）。"""
    editor = window.open_path(sjis_file)
    edit_text(editor, "\x1b$B 化けているように見える本文\n")  # ⑥ の警告に引っかかる形
    monkeypatch.setattr(
        "editor_app.file_commands.prompt_suspicious_text", lambda *_a, **_k: False
    )

    assert window.save_editor(editor) is False

    assert backups.list_backups(sjis_file) == []


def test_save_continues_when_the_backup_cannot_be_taken(
    window: MainWindow, sjis_file: Path, monkeypatch
) -> None:
    """控えが取れなくても保存は止めない（保存できない方が損失が大きい）。"""
    editor = window.open_path(sjis_file)
    edit_text(editor, "書き換えました\n")

    def refuse(*_args, **_kwargs):
        raise OSError("キャッシュ置き場に書けません")

    monkeypatch.setattr("editor_app.file_commands.backup_file", refuse)

    assert window.save_editor(editor) is True
    assert sjis_file.read_text(encoding="cp932") == "書き換えました\n"
    # 印は付けない（次の保存でもう一度試みられる）。
    assert editor.backed_up_path is None


def test_save_as_backs_up_the_file_being_overwritten(
    window: MainWindow, sjis_file: Path, tmp_path: Path, monkeypatch
) -> None:
    """別のファイルへ「名前を付けて保存」しても、**上書きされる側**を控える。

    保存先が変われば「このタブはもう控えた」という記録も無効になる
    （:attr:`~editor_app.editor.EditorWidget.backed_up_path`）。
    """
    victim = tmp_path / "victim.txt"
    victim_bytes = "上書きされる側\n".encode("cp932")
    victim.write_bytes(victim_bytes)

    editor = window.open_path(sjis_file)
    window.save_editor(editor)  # 1 回目の控え（sample.txt 側）
    _accept_save_dialog(monkeypatch, victim)

    assert window.save_editor_as(editor) is True

    (entry,) = backups.list_backups(victim)
    assert entry.data_path.read_bytes() == victim_bytes


# ----------------------------------------------------------------------
# 7-B: アプリの中だけで完結する復元
# ----------------------------------------------------------------------
def test_restore_menu_lists_backups_newest_first(
    window: MainWindow, sjis_file: Path, chosen
) -> None:
    """一覧は新しい順（日時と大きさが読める形で並ぶ）。"""
    editor = window.open_path(sjis_file)
    edit_text(editor, "1 回目\n")
    window.save_editor(editor)
    editor.backed_up_path = None  # 2 件目を作る（本来は初回のみ）
    edit_text(editor, "2 回目\n")
    window.save_editor(editor)
    chosen.index = None  # 一覧を見るだけ

    assert window.restore_from_backup() is False

    assert len(chosen.items) == 2
    entries = backups.list_backups(sjis_file)
    assert chosen.items == [backups.describe_entry(entry) for entry in entries]
    assert entries[0].saved_at >= entries[1].saved_at


def test_restore_without_any_backup_says_so(
    window: MainWindow, tmp_path: Path, informed, chosen
) -> None:
    """控えが 1 件も無ければ、その旨を伝えて何もしない。"""
    path = tmp_path / "utf8.txt"
    path.write_text(JAPANESE, encoding="utf-8")
    window.open_path(path)

    assert window.restore_from_backup() is False

    assert informed and "控え" in informed[0][1]
    assert chosen.items == []


def test_restore_on_untitled_tab_says_so(window: MainWindow, informed, chosen) -> None:
    """まだ保存していないタブには戻す先が無い。"""
    assert window.restore_from_backup() is False

    assert informed and "保存されていない" in informed[0][1]
    assert chosen.items == []


def test_restore_writes_back_the_exact_bytes_and_reloads(
    window: MainWindow, sjis_file: Path, chosen
) -> None:
    """復元は (a) 今の内容を控え直し (b) 生のバイト列で書き戻し (c) 読み直す。

    文字コードの判定もやり直されるので、元のバイト列に戻った以上は
    正しく Shift-JIS として付き直る。
    """
    original = sjis_file.read_bytes()
    editor = window.open_path(sjis_file)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)
    damaged = sjis_file.read_bytes()

    assert window.restore_from_backup() is True

    # (b) 選んだ控えのバイト列が、そのままファイルへ戻っている
    assert sjis_file.read_bytes() == original
    # (c) タブも読み直され、文字コードが判定し直されている
    assert document_text(editor) == JAPANESE
    assert editor.encoding == "cp932"
    assert editor.is_modified is False
    # (a) 復元で消えた「壊れた内容」も控えに残っている（復元自体をやり直せる）
    assert damaged in [entry.data_path.read_bytes() for entry in backups.list_backups(sjis_file)]


def test_restore_asks_before_replacing(window: MainWindow, sjis_file: Path, chosen) -> None:
    """一覧で選んだ後に、必ず「今の内容は失われます」の確認を挟む。"""
    editor = window.open_path(sjis_file)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)

    window.restore_from_backup()

    assert chosen.confirmed is True
    assert "失われます" in chosen.confirm_text
    assert chosen.label != "OK"  # ふつうの操作と同じに見えるボタンにしない


def test_cancelling_the_confirmation_changes_nothing(
    window: MainWindow, sjis_file: Path, chosen
) -> None:
    """確認をキャンセルしたら、ファイルもタブも控えの一覧も変わらない。"""
    editor = window.open_path(sjis_file)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)
    before_bytes = sjis_file.read_bytes()
    before_backups = [entry.data_path for entry in backups.list_backups(sjis_file)]
    chosen.confirm = False

    assert window.restore_from_backup() is False

    assert sjis_file.read_bytes() == before_bytes
    assert document_text(editor) == "壊れた内容\n"
    assert [entry.data_path for entry in backups.list_backups(sjis_file)] == before_backups


def test_cancelling_the_list_changes_nothing(
    window: MainWindow, sjis_file: Path, chosen
) -> None:
    """一覧をキャンセルしたら確認すら出さない。"""
    editor = window.open_path(sjis_file)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)
    chosen.index = None

    assert window.restore_from_backup() is False

    assert chosen.confirmed is False
    assert sjis_file.read_text(encoding="cp932") == "壊れた内容\n"


def test_restore_stops_when_the_current_content_cannot_be_backed_up(
    window: MainWindow, sjis_file: Path, chosen, informed, monkeypatch
) -> None:
    """今の内容を控えられないなら復元しない（やり直せない上書きになるため）。"""
    editor = window.open_path(sjis_file)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)
    damaged = sjis_file.read_bytes()

    def refuse(*_args, **_kwargs):
        raise OSError("キャッシュ置き場に書けません")

    monkeypatch.setattr("editor_app.file_commands.backup_file", refuse)

    assert window.restore_from_backup() is False

    assert sjis_file.read_bytes() == damaged
    assert informed and informed[0][0] == "復元できません"


def test_restore_reports_a_write_failure_and_leaves_the_file_alone(
    window: MainWindow, sjis_file: Path, chosen, informed, monkeypatch
) -> None:
    """書き戻しそのものが失敗したら、そのまま知らせる（黙って成功にしない）。

    ここで ``True`` を返すと、タブは復元できたつもりのまま進んでしまう。
    """
    editor = window.open_path(sjis_file)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)
    damaged = sjis_file.read_bytes()

    def refuse(*_args, **_kwargs):
        raise PermissionError("書き込めません")

    monkeypatch.setattr("editor_app.file_commands.write_encoded_file", refuse)

    assert window.restore_from_backup() is False

    assert sjis_file.read_bytes() == damaged
    assert document_text(editor) == "壊れた内容\n"
    assert informed and informed[0][0] == "復元できません"


def test_restore_says_the_file_is_already_back_when_only_the_reload_fails(
    window: MainWindow, sjis_file: Path, chosen, informed, monkeypatch
) -> None:
    """**書き戻せた後に読み直しだけ失敗した場合は、案内の中身が変わる。**

    ファイル自体はもう控えの内容に戻っているのに「変更していません」と
    伝えると、利用者はもう一度復元しようとする。さらに悪いのは、**画面に
    残っている書き戻す前の本文をそのまま保存されてしまう**こと（せっかく
    戻したファイルが、また壊れた内容で上書きされる）。そこまで伝える。
    """
    original = sjis_file.read_bytes()
    editor = window.open_path(sjis_file)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)

    def refuse(*_args, **_kwargs):
        raise OSError("読み直せません")

    monkeypatch.setattr("editor_app.file_commands.read_text_file_detail", refuse)

    assert window.restore_from_backup() is False

    # ファイルは戻っている。タブは書き戻す前のまま（読み直せなかったので）。
    assert sjis_file.read_bytes() == original
    assert document_text(editor) == "壊れた内容\n"
    assert informed and informed[0][0] == "復元できません"
    message = informed[0][1]
    assert "書き戻しました" in message
    assert "ファイル自体は控えの内容に戻っています" in message
    assert "そのまま保存すると" in message  # 画面の本文で上書きしないよう促す


def test_restore_stops_when_the_backup_itself_is_gone(
    window: MainWindow, sjis_file: Path, chosen, informed, monkeypatch
) -> None:
    """控えのファイル自体が読めなくなっていたら、書き戻さずに知らせる。

    一覧に出してから利用者が選ぶまでの間に消える（別のウィンドウの掃除に
    当たった等）ことはありうる。**読めないまま空を書き戻さない**こと。
    """
    editor = window.open_path(sjis_file)
    edit_text(editor, "壊れた内容\n")
    window.save_editor(editor)
    damaged = sjis_file.read_bytes()

    def vanish(path):
        entries = backups.list_backups(path)
        entries[0].data_path.unlink()
        return entries

    monkeypatch.setattr("editor_app.file_commands.list_backups", vanish)

    assert window.restore_from_backup() is False

    assert sjis_file.read_bytes() == damaged
    assert informed and informed[0][0] == "復元できません"


def _accept_save_dialog(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """「名前を付けて保存」のファイルダイアログを、``target`` を選んだことにする。"""
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "exec", lambda _self: QFileDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda _self: [str(target)])
