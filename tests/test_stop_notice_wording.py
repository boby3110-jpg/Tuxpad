"""**保存を**止めたときの案内の文面のテスト（70 回目・`test_restore_wording.py` の隣）.

同じ晩の別のルーティーンが `tests/test_restore_wording.py` で
**復元（控えから戻すとき）**の文面を固めた。このファイルが見るのは、
その隣にある**保存の経路**——**保存しようとして止められたとき**の案内で、
そちらでは見ていない:

- **保存直前に外部変更を見つけたが、ディスクの内容を読めなかったとき**
  （差分を出せないので、従来どおりの「保存しませんでした」1 枚に落ちる）
- **保存しようとしたらファイルが消えていたとき**

どちらも**「それでも上書きする」を用意していない**止め方なので、
**文面が利用者にとって唯一の出口**になる。とくに逃げ道
（「ファイル」→「名前を付けて保存」で別のファイル／別の保存先へ）が
文面から落ちると、利用者は**書いたものをどこにも保存できない**
（そのまま閉じれば失う）。実際に候補を当てたところ、既存の 1838 件も、
`test_restore_wording.py` の 22 件も、**この 2 枚の文面は 1 文字も
縛っていなかった**。

あわせて、復元の一覧の**初期選択**（どの行から始まるか）もここで見る。
`test_restore_wording.py` の `asked` フィクスチャは `prompt_choice` の
`current` を捨てているので、**一覧をよく見ずに Enter を押したときに
どの控えが選ばれるか**は、まだ誰も見ていなかった。

文面そのものを固定するので、**言い回しを変えるときはここも直すこと**
（字面を変える自由は残したいので、判断の分かれ目になる語だけを見ている）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from editor_app import backups
from editor_app.main_window import MainWindow

JAPANESE = "日本語のテキスト\n2 行目\n"

#: 保存直前のチェックが差分を出せなかったときのダイアログ（`file_commands`）。
SAVE_DIALOG = "editor_app.file_commands.show_message_box"
#: 差分を見せて行き先を選ばせるダイアログ（こちらは出ないことを確かめる）。
CHANGE_PROMPT = "editor_app.file_commands.prompt_external_change"


@pytest.fixture
def sjis_file(tmp_path: Path) -> Path:
    """Shift-JIS のファイル（控えの対象になる文字コード）。"""
    path = tmp_path / "見積書.txt"
    path.write_bytes(JAPANESE.encode("cp932"))
    return path


@pytest.fixture
def notices(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """お知らせダイアログを (題名, 本文) で受け取る。

    塞がないとヘッドレスで本物のモーダルが開いて固まる。
    """
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        SAVE_DIALOG,
        lambda _parent, _icon, title, text, *a, **k: shown.append((title, text)),
    )
    return shown


class Prompts:
    """控えの一覧と確認ダイアログを捕まえる。

    `test_restore_wording.py` の ``asked`` と違い、**一覧の初期選択
    (``current``)** も覚えておく——利用者が一覧をよく見ずに Enter を
    押したときに決まるのはこの値なのに、今まで誰も見ていなかった。
    """

    def __init__(self) -> None:
        self.label = ""
        self.items: list[str] = []
        self.current: int | None = None
        self.index: int | None = 0
        self.confirm_text = ""
        self.confirm = True


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> Prompts:
    state = Prompts()

    def fake_choice(_parent, _title, label, items, current):
        state.label = label
        state.items = list(items)
        state.current = current
        return state.index

    def fake_destructive(_parent, _title, text, _action_label):
        state.confirm_text = text
        return state.confirm

    monkeypatch.setattr("editor_app.file_commands.prompt_choice", fake_choice)
    monkeypatch.setattr("editor_app.file_commands.prompt_destructive", fake_destructive)
    return state


def _retime(data_path: Path, saved_at: datetime) -> None:
    """控えの索引 (JSON) の日時だけを書き換える。

    ``backup_file`` は「今」で控えるので、続けて 2 件作っても**表示上は
    同じ秒**になってしまい（一覧に出るのは秒までの文字列）、「どの行から
    始まるか」を見分けられない。索引は本番の実装がそのまま読むファイルな
    ので、ここを書き換えても別経路にはならない
    （`tests/test_backup_listing.py` と同じ手）。
    """
    index_path = data_path.with_suffix(backups.INDEX_SUFFIX)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index[backups.INDEX_SAVED_AT_KEY] = saved_at.isoformat(timespec="microseconds")
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")


#: 一覧に出るはずの日時（新しい方・古い方）。表示は秒まで。
NEWER = datetime(2026, 2, 2, 15, 30, 45)
OLDER = datetime(2026, 1, 1, 9, 0, 0)


@pytest.fixture
def two_backups(window: MainWindow, sjis_file: Path):
    """同じファイルの控えを 2 件作り、日時を離してからそのファイルを開く。

    **このタブ自身が控えたのではない**形にしておく（アプリを開き直して
    から復元する、という普通の使い方と同じ）。
    """
    newer = backups.backup_file(sjis_file)
    sjis_file.write_bytes("書き換えた後\n".encode("cp932"))
    older = backups.backup_file(sjis_file)
    # 作った順と日時の新旧をわざと逆にする（並べ替えが効いていないと
    # 「初期選択の行」と「一番新しい控え」が同じ行になり、取り違えを見逃す）。
    _retime(newer, NEWER)
    _retime(older, OLDER)
    entries = backups.list_backups(sjis_file)
    assert [entry.saved_at for entry in entries] == [NEWER, OLDER]
    return window.open_path(sjis_file)


def _refuse(message: str):
    """呼ばれたら ``OSError`` を投げるだけの差し替え。"""

    def fail(*_args, **_kwargs):
        raise OSError(message)

    return fail


# ----------------------------------------------------------------------
# 控えの一覧は、どの行から始まるか（prompt_choice の初期選択）
# ----------------------------------------------------------------------
def test_the_list_starts_on_the_newest_backup(
    window: MainWindow, prompts, two_backups
) -> None:
    """初期選択は**一番新しい控え**（失うものが最も少ない行）。

    一覧は新しい順に並ぶので初期選択は 0 番。ここが末尾（一番古い控え）に
    なっていると、一覧をよく見ずに Enter を押した利用者が**いちばん古い
    内容でファイルを上書きする**——その間の編集を全部失う形になる。
    並び順そのものと一覧の文面は `test_backups.py` /
    `test_restore_wording.py` が見ているので、ここでは「何番目から
    始まるか」だけを見る。
    """
    prompts.index = None  # 一覧を見るだけ

    assert window.restore_from_backup() is False

    assert prompts.current == 0
    assert prompts.items[0].startswith("2026-02-02 15:30:45")


# ----------------------------------------------------------------------
# 戻せる控えが無いときの知らせ（控えを取る条件の説明）
# ----------------------------------------------------------------------
def test_the_no_backup_notice_says_which_encodings_get_a_backup(
    window: MainWindow, tmp_path: Path, notices, prompts
) -> None:
    """控えを取るのが**日本語の文字コードのファイル**だけだと言う。

    `test_restore_wording.py` は「最初に保存する直前」と文字コードの名前
    （Shift-JIS 等）を見ているが、**それを束ねる「日本語の文字コード」の
    一言**は縛られていない。ここが落ちると文が途中で切れて
    「（Shift-JIS・EUC-JP・JIS）のファイルを開いて…」から始まる形になり、
    UTF-8 のファイルで控えが無い理由が読み取れなくなる。
    """
    path = tmp_path / "utf8.txt"
    path.write_text(JAPANESE, encoding="utf-8")
    window.open_path(path)

    assert window.restore_from_backup() is False

    message = notices[0][1]
    assert "控えを取るのは、日本語の文字コード" in message


# ----------------------------------------------------------------------
# 保存直前に外部変更を見つけて止めたときの知らせ（差分を出せなかった場合）
# ----------------------------------------------------------------------
def _stopped_by_an_unreadable_external_change(
    window: MainWindow, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """外部で変わったが**ディスクの内容を読めない**状況を作って保存させる。

    差分が作れないので、実装は従来どおりの「保存しませんでした」1 枚に
    落ちる（`tests/test_external_change_before_save.py` が経路を固めて
    いる。ここで見るのはその 1 枚の文面）。
    """
    editor = window.open_path(path)
    editor.selectAll()
    editor.insertPlainText("こちらの編集\n")
    monkeypatch.setattr(CHANGE_PROMPT, lambda *a, **k: None)
    path.write_text("別の PC が書いた内容\n", encoding="utf-8")
    monkeypatch.setattr(
        "editor_app.file_commands.read_text_file_detail",
        _refuse("文字コードを判定できません"),
    )
    assert window.save_editor(editor) is False


def test_the_external_change_notice_offers_a_way_out(
    window: MainWindow, tmp_path: Path, notices, monkeypatch
) -> None:
    """**逃げ道（「名前を付けて保存」）を必ず案内する。**

    このダイアログには「それでも上書きする」が無い。つまり利用者は、
    文面に書いてある手順を知らないかぎり**編集内容をどこにも保存できない**
    （そのまま閉じれば失う）。止めることと逃げ道を示すことは一組。
    """
    path = tmp_path / "共有メモ.txt"
    path.write_text("最初の内容\n", encoding="utf-8")
    _stopped_by_an_unreadable_external_change(window, path, monkeypatch)

    assert "名前を付けて保存" in notices[-1][1]


def test_the_external_change_notice_says_the_save_was_stopped(
    window: MainWindow, tmp_path: Path, notices, monkeypatch
) -> None:
    """保存を**中止した**ことを言う。

    「このまま保存すると失われます」だけだと、**保存できたのかどうか**が
    読み取れない（題名は「保存しませんでした」だが、本文が逆のことを
    匂わせていると利用者は本文を信じる）。保存できたと思って閉じると、
    編集内容はそのまま消える。
    """
    path = tmp_path / "共有メモ.txt"
    path.write_text("最初の内容\n", encoding="utf-8")
    _stopped_by_an_unreadable_external_change(window, path, monkeypatch)

    assert "中止しました" in notices[-1][1]


def test_the_external_change_notice_names_the_file(
    window: MainWindow, tmp_path: Path, notices, monkeypatch
) -> None:
    """どのファイルで起きたのかを言う。

    この止め方は「すべて保存」や終了時にも通るので、**ウィンドウ内の
    どのタブで起きたのか**が文面にないと探し当てられない。
    """
    path = tmp_path / "共有メモ.txt"
    path.write_text("最初の内容\n", encoding="utf-8")
    _stopped_by_an_unreadable_external_change(window, path, monkeypatch)

    assert "共有メモ.txt" in notices[-1][1]


# ----------------------------------------------------------------------
# 保存しようとしたらファイルが消えていたときの知らせ
# ----------------------------------------------------------------------
def test_the_vanished_file_notice_offers_a_way_out(
    window: MainWindow, tmp_path: Path, notices, monkeypatch
) -> None:
    """消えていたときも、逃げ道（保存先を選び直す）を案内する。

    こちらも「それでも作り直す」は用意していない止め方なので、文面が
    唯一の出口。消えた理由（別の PC が消した・NAS が切れた）によっては
    同じ場所に保存し直したくないので、**保存先を選び直す**と言う。
    消えたこと自体は
    `test_external_change_before_save.py::test_missing_file_falls_back_to_a_plain_message`
    が見ている（そちらは「見つかりません」だけ）。
    """
    path = tmp_path / "共有メモ.txt"
    path.write_text("最初の内容\n", encoding="utf-8")
    editor = window.open_path(path)
    editor.selectAll()
    editor.insertPlainText("こちらの編集\n")
    monkeypatch.setattr(CHANGE_PROMPT, lambda *a, **k: None)
    path.write_text("別の PC が書いた内容\n", encoding="utf-8")

    def vanished(*_args, **_kwargs):
        raise FileNotFoundError(path)

    monkeypatch.setattr("editor_app.file_commands.read_text_file_detail", vanished)

    assert window.save_editor(editor) is False

    message = notices[-1][1]
    assert "名前を付けて保存" in message
    assert "保存先を選び直して" in message
