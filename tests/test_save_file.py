"""機能 3「保存 / 上書き保存」のテスト."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from editor_app import fileio
from editor_app.main_window import MainWindow


def edit_text(editor, text: str) -> None:
    """本文を書き換えて「未保存」状態にする。

    ``setPlainText()`` は変更フラグを立てない (読み込み用の API) ので、
    利用者がキーボードで打ったのと同じ扱いになる ``insertPlainText()`` を使う。
    """
    editor.selectAll()
    editor.insertPlainText(text)


def mock_save_dialog(monkeypatch: pytest.MonkeyPatch, path: Path | None) -> None:
    """「名前を付けて保存」ダイアログをモックする。

    ``QFileDialog`` はサイドバーに NAS 等を追加する都合上インスタンスベースの
    API（exec() + selectedFiles()）を使っているので、そちらをモックする。
    ``path`` が ``None`` ならキャンセル（Rejected）を再現する。
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


# ----------------------------------------------------------------------
# fileio: バイト列への書き戻し
# ----------------------------------------------------------------------
def test_encode_utf8_lf() -> None:
    assert fileio.encode_text("あいう\n2行目\n", "utf-8", "\n") == "あいう\n2行目\n".encode()


def test_encode_restores_crlf() -> None:
    data = fileio.encode_text("1行目\n2行目\n", "utf-8", "\r\n")
    assert data == "1行目\r\n2行目\r\n".encode()


def test_encode_restores_cr() -> None:
    assert fileio.encode_text("a\nb", "utf-8", "\r") == b"a\rb"


def test_encode_utf8_sig_has_bom() -> None:
    data = fileio.encode_text("あ", "utf-8-sig", "\n")
    assert data.startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [
        ("utf-16-le", b"\xff\xfe"),
        ("utf-16-be", b"\xfe\xff"),
        ("utf-32-le", b"\xff\xfe\x00\x00"),
        ("utf-32-be", b"\x00\x00\xfe\xff"),
    ],
)
def test_encode_adds_bom_for_utf16_32(encoding: str, bom: bytes) -> None:
    """コーデック自身が BOM を付けない文字コードでは fileio が補う。"""
    data = fileio.encode_text("あ", encoding, "\n")
    assert data.startswith(bom)
    assert data == bom + "あ".encode(encoding)


def test_encode_error_for_unrepresentable_char() -> None:
    with pytest.raises(fileio.EncodingError) as excinfo:
        fileio.encode_text("絵文字🍣", "cp932", "\n")
    assert "🍣" in str(excinfo.value)


@pytest.mark.parametrize("encoding", fileio.SELECTABLE_ENCODINGS)
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_round_trip(tmp_path: Path, encoding: str, newline: str) -> None:
    """書き出したファイルを読み直すと、本文・文字コード・改行が一致する。

    一覧は :data:`fileio.SELECTABLE_ENCODINGS`（＝「文字コードを指定して
    保存」で利用者に選ばせるもの）**そのまま**にしてある。ここに直接
    書き並べていた頃は cp932 / euc_jp / iso2022_jp が抜けていて、
    **選ばせておきながら読み直せない文字コードが 2 つ**紛れていた
    （JIS と EUC-JP）。選択肢が増えたらここも自動で増えるようにしておく。
    """
    path = tmp_path / "round.txt"
    text = "1行目\n二行目\nthird\n"
    fileio.write_text_file(path, text, encoding, newline)

    read_text, read_encoding, read_newline = fileio.read_text_file(path)
    assert read_text == text
    assert read_encoding == encoding
    assert read_newline == newline


def test_write_replaces_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "exists.txt"
    path.write_text("古い内容\n", encoding="utf-8")
    fileio.write_text_file(path, "新しい内容\n", "utf-8", "\n")
    assert path.read_text(encoding="utf-8") == "新しい内容\n"


def test_write_keeps_permissions(tmp_path: Path) -> None:
    path = tmp_path / "mode.txt"
    path.write_text("x\n", encoding="utf-8")
    path.chmod(0o640)
    fileio.write_text_file(path, "y\n", "utf-8", "\n")
    assert path.stat().st_mode & 0o777 == 0o640


def test_new_file_gets_normal_permissions(tmp_path: Path) -> None:
    """新規作成したファイルが、普通に作ったファイルと同じ見え方になる。

    書き込みは ``tempfile.mkstemp()`` の一時ファイル経由で行うが、
    ``mkstemp()`` は本人以外に一切見せない ``0o600`` で作る。パーミッションを
    直さずに :func:`os.replace` すると、**「名前を付けて保存」で作った
    ファイルだけが ``0o600``** になり、共有フォルダ (NAS 等) に置いても
    他の利用者・他の PC から読めない、という壊れ方をする。
    比較対象を同じテスト内で作り、「普通に書いたファイルと同じ」ことを固定する。
    """
    written = tmp_path / "new.txt"
    fileio.write_text_file(written, "本文\n", "utf-8", "\n")

    plain = tmp_path / "plain.txt"
    plain.write_text("本文\n", encoding="utf-8")

    assert written.stat().st_mode & 0o777 == plain.stat().st_mode & 0o777


def test_new_file_permissions_follow_umask(tmp_path: Path) -> None:
    """新規ファイルのパーミッションが umask に従う（他人に読ませない設定も尊重する）。"""
    original = os.umask(0o077)
    try:
        path = tmp_path / "private.txt"
        fileio.write_text_file(path, "本文\n", "utf-8", "\n")
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        os.umask(original)


def test_default_file_mode_does_not_change_umask(tmp_path: Path) -> None:
    """umask の読み取りが、プロセスの umask を変えたまま帰らない。

    umask は「読むだけ」の API が無く、一度書き換えて戻すしかないため、
    戻し忘れるとアプリ全体の以後のファイル作成に影響する。
    """
    original = os.umask(0o027)
    try:
        assert fileio.default_file_mode() == 0o640
        assert os.umask(0o027) == 0o027
    finally:
        os.umask(original)


def test_save_as_new_file_is_readable_by_others(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「名前を付けて保存」で新規作成したファイルが他の利用者からも読める。"""
    original = os.umask(0o022)
    try:
        path = tmp_path / "保存.txt"
        editor = window.current_editor()
        edit_text(editor, "本文\n")
        mock_save_dialog(monkeypatch, path)

        assert window.save_file_as() is True
        assert path.stat().st_mode & 0o777 == 0o644
    finally:
        os.umask(original)


def test_write_leaves_no_temp_file(tmp_path: Path) -> None:
    fileio.write_text_file(tmp_path / "a.txt", "本文\n", "utf-8", "\n")
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_write_through_symlink_keeps_the_symlink(tmp_path: Path) -> None:
    """シンボリックリンク越しに保存しても、リンクが実体ファイルに化けない。

    書き込みは「一時ファイルを作って :func:`os.replace` で置き換える」方式
    なので、リンクそのものを置き換えてしまうと、リンク先の実体は古いまま
    リンクが普通のファイルに変わってしまう（共有フォルダへのリンクを
    経由して開いている場合に、保存したはずの内容がリンク先へ届かない）。
    ``MainWindow._write_editor`` が保存先を ``Path.resolve()`` してから
    渡すことでこれを避けている。ここではその前提が崩れていないことを固定する。
    """
    real = tmp_path / "real.txt"
    real.write_text("元の内容\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    fileio.write_text_file(link.resolve(), "書き換え\n", "utf-8", "\n")

    assert link.is_symlink()
    assert real.read_text(encoding="utf-8") == "書き換え\n"


def test_save_through_symlink_updates_the_target(
    window: MainWindow, tmp_path: Path
) -> None:
    """リンク越しに開いたファイルを保存すると、リンク先の実体が更新される。"""
    real = tmp_path / "real.txt"
    real.write_text("元の内容\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    editor = window.open_path(link)
    edit_text(editor, "書き換え\n")

    assert window.save_editor(editor) is True
    assert link.is_symlink()
    assert real.read_text(encoding="utf-8") == "書き換え\n"


def test_write_does_not_break_original_on_encode_error(tmp_path: Path) -> None:
    """保存できない文字があっても、元のファイルは壊さない。"""
    path = tmp_path / "sjis.txt"
    fileio.write_text_file(path, "元の内容\n", "cp932", "\n")
    with pytest.raises(fileio.EncodingError):
        fileio.write_text_file(path, "🍣\n", "cp932", "\n")
    assert path.read_bytes() == "元の内容\n".encode("cp932")
    assert [p.name for p in tmp_path.iterdir()] == ["sjis.txt"]


def test_write_cleans_up_the_temp_file_when_replacing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """置き換えに失敗しても、書きかけの一時ファイルを残さない。

    保存は「同じフォルダに一時ファイルを作って :func:`os.replace` で
    置き換える」方式なので、置き換えで失敗した瞬間に後始末をしないと
    ``.名前.xxxx.tmp`` が**保存に失敗するたびに 1 個ずつ溜まっていく**
    （利用者からは隠しファイルなので気づけず、共有フォルダがゴミで埋まる）。
    ディスクが一杯・書き込み権限が無い・NAS が切れた、で実際に起こる。
    """
    path = tmp_path / "sample.txt"
    original = "元の内容\n".encode("cp932")
    path.write_bytes(original)

    def no_space(_src, _dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", no_space)

    with pytest.raises(OSError):
        fileio.write_text_file(path, "新しい内容\n", "cp932", "\n")

    assert [p.name for p in tmp_path.iterdir()] == ["sample.txt"]
    assert path.read_bytes() == original  # 元のファイルは無傷


def test_write_cleans_up_the_temp_file_when_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl+C などで中断されたときも、一時ファイルを残さない。

    ``KeyboardInterrupt`` / ``SystemExit`` は ``Exception`` を継承していないので、
    後始末を ``except Exception`` で書くとここだけ素通りしてゴミが残る
    （``write_text_file`` が ``except BaseException`` にしてあるのはこのため）。
    """
    path = tmp_path / "中断.txt"

    def interrupted(_src, _dst):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupted)

    with pytest.raises(KeyboardInterrupt):
        fileio.write_text_file(path, "本文\n", "utf-8", "\n")

    assert list(tmp_path.iterdir()) == []


def test_write_onto_a_directory_fails_without_leaving_a_temp_file(
    tmp_path: Path,
) -> None:
    """保存先がフォルダだった場合も、例外を返すだけで痕跡を残さない。

    （``os.replace`` を差し替えずに、本物の失敗で後始末を確かめる経路。）
    """
    target = tmp_path / "フォルダ"
    target.mkdir()

    with pytest.raises(OSError):
        fileio.write_text_file(target, "本文\n", "utf-8", "\n")

    assert [p.name for p in tmp_path.iterdir()] == ["フォルダ"]
    assert target.is_dir()


# ----------------------------------------------------------------------
# MainWindow: 上書き保存
# ----------------------------------------------------------------------
def test_save_existing_file(window: MainWindow, tmp_path: Path) -> None:
    path = tmp_path / "doc.txt"
    path.write_text("最初\n", encoding="utf-8")

    editor = window.open_path(path)
    edit_text(editor, "書き換えた\n")
    assert editor.is_modified

    assert window.save_file() is True
    assert path.read_text(encoding="utf-8") == "書き換えた\n"
    assert editor.is_modified is False


def test_save_keeps_encoding_and_newline(window: MainWindow, tmp_path: Path) -> None:
    """CRLF・BOM 付き UTF-16 のファイルは、保存してもその形式を保つ。"""
    path = tmp_path / "u16.txt"
    path.write_bytes(b"\xff\xfe" + "あ\r\nい\r\n".encode("utf-16-le"))

    editor = window.open_path(path)
    assert (editor.encoding, editor.newline) == ("utf-16-le", "\r\n")
    edit_text(editor, "あ\nい\nう\n")
    assert window.save_file() is True

    data = path.read_bytes()
    assert data.startswith(b"\xff\xfe")
    assert data == b"\xff\xfe" + "あ\r\nい\r\nう\r\n".encode("utf-16-le")


def test_save_untitled_falls_back_to_save_as(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """無題タブの Ctrl+S は「名前を付けて保存」になる。"""
    target = tmp_path / "新規.txt"
    mock_save_dialog(monkeypatch, target)

    editor = window.current_editor()
    edit_text(editor, "無題の内容\n")
    assert window.save_file() is True

    assert target.read_text(encoding="utf-8") == "無題の内容\n"
    assert editor.path == target.resolve()
    assert editor.is_modified is False
    assert window.tabs.tabText(0) == "新規.txt"
    assert window.windowTitle().startswith("新規.txt")


def test_save_as_cancel_does_nothing(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_save_dialog(monkeypatch, None)
    editor = window.current_editor()
    edit_text(editor, "保存しない\n")

    assert window.save_file_as() is False
    assert editor.path is None
    assert editor.is_modified is True
    assert list(tmp_path.iterdir()) == []


def test_save_as_without_a_chosen_file_does_nothing(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「決定」されたのにファイル名が 1 つも返ってこなくても落ちない。

    ``QFileDialog`` は Accepted でも ``selectedFiles()`` が空になることが
    ある（デスクトップ環境側の「開く/保存」ダイアログ＝ポータル経由の
    ネイティブダイアログで、名前を入れずに決定された場合など）。ここで
    受け止め損ねると ``selected[0]`` が ``IndexError`` になり、**保存しようと
    しただけでアプリが落ちて、未保存の本文がまとめて失われる**。
    """
    monkeypatch.setattr(
        QFileDialog, "exec", lambda self: QFileDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [])
    editor = window.current_editor()
    edit_text(editor, "保存されない\n")

    assert window.save_file_as() is False
    assert editor.path is None
    assert editor.is_modified is True
    assert list(tmp_path.iterdir()) == []


def test_save_as_switches_path(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """別名保存すると、以後の上書き保存は新しいファイルに向く。"""
    original = tmp_path / "original.txt"
    original.write_text("本文\n", encoding="utf-8")
    copy = tmp_path / "copy.txt"
    mock_save_dialog(monkeypatch, copy)

    editor = window.open_path(original)
    edit_text(editor, "別名で保存\n")
    assert window.save_file_as() is True

    assert copy.read_text(encoding="utf-8") == "別名で保存\n"
    assert original.read_text(encoding="utf-8") == "本文\n"
    assert editor.path == copy.resolve()

    edit_text(editor, "さらに編集\n")
    assert window.save_file() is True
    assert copy.read_text(encoding="utf-8") == "さらに編集\n"
    assert original.read_text(encoding="utf-8") == "本文\n"


def test_save_as_refuses_a_file_open_in_another_tab(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既に別のタブで開いているファイルには「名前を付けて保存」できない。

    保存できてしまうと同じファイルを指すタブが 2 つでき、あとから保存した方が
    先の変更を黙って消してしまう（実際に消えることを確認したうえで塞いだ）。
    """
    target = tmp_path / "開いている.txt"
    target.write_text("元の内容\n", encoding="utf-8")
    opened = window.open_path(target)

    other = window.new_file()
    edit_text(other, "別のタブの内容\n")

    warnings: list[str] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda _parent, _icon, title, *a, **k: warnings.append(title),
    )
    mock_save_dialog(monkeypatch, target)

    assert window.save_editor_as(other) is False
    assert warnings == ["保存できません"]
    # ファイルも、先に開いていたタブも、保存しようとしたタブも元のまま。
    assert target.read_text(encoding="utf-8") == "元の内容\n"
    assert other.path is None
    assert other.is_modified is True
    assert [e for e in window.editors() if e.path == target.resolve()] == [opened]


def test_save_as_refuses_a_file_open_in_another_window(
    make_window, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """別ウィンドウのタブで開いているファイルでも同じく保存を止める。"""
    target = tmp_path / "他ウィンドウ.txt"
    target.write_text("元の内容\n", encoding="utf-8")

    win_a = make_window()
    win_b = make_window()
    win_a.open_path(target)

    editor = win_b.current_editor()
    edit_text(editor, "別ウィンドウの内容\n")

    monkeypatch.setattr("editor_app.file_commands.show_message_box", lambda *a, **k: None)
    mock_save_dialog(monkeypatch, target)

    assert win_b.save_editor_as(editor) is False
    assert target.read_text(encoding="utf-8") == "元の内容\n"
    assert editor.path is None


def test_save_as_allows_overwriting_the_same_tabs_own_file(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自分自身が開いているファイルへの上書きは、今までどおりできる。"""
    target = tmp_path / "自分.txt"
    target.write_text("元の内容\n", encoding="utf-8")

    editor = window.open_path(target)
    edit_text(editor, "書き換えた\n")
    mock_save_dialog(monkeypatch, target)

    assert window.save_editor_as(editor) is True
    assert target.read_text(encoding="utf-8") == "書き換えた\n"


def test_save_error_shows_dialog(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """書き込みに失敗したらダイアログを出し、未保存のままにする。"""
    shown: list[str] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda _p, _icon, title, text, *a, **k: shown.append(title),
    )
    editor = window.current_editor()
    editor.path = tmp_path / "存在しないディレクトリ" / "a.txt"
    edit_text(editor, "書けない\n")

    assert window.save_file() is False
    assert shown == ["保存できません"]
    assert editor.is_modified is True


def test_save_stops_when_the_text_cannot_even_be_encoded(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """バイト列に直す段階の失敗も、理由を添えて知らせて**書きに行かない**。

    「この文字コードでは表せない文字がある」(``EncodingError``) とは別の
    失敗——例えば Python が知らないコーデック名が付いてしまった場合
    （判定が charset-normalizer に回ると、こちらの一覧に無い名前が
    返ることがある）。ここを握り潰すと**空のバイト列で上書きしかねない**
    ので、ファイルには一切触れないまま止める。
    """
    target = tmp_path / "変な文字コード.txt"
    target.write_text("元の内容\n", encoding="utf-8")
    editor = window.open_path(target)
    editor.encoding = "そんなコーデックはない"
    edit_text(editor, "書き換えた\n")

    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "editor_app.file_commands.show_message_box",
        lambda _p, _icon, title, text, *a, **k: shown.append((title, text)),
    )

    assert window.save_editor(editor) is False

    assert target.read_text(encoding="utf-8") == "元の内容\n"
    assert editor.is_modified is True
    assert shown and shown[0][0] == "保存できません"
    # 「保存できない文字がある」の案内（行番号の一覧）と取り違えないこと。
    assert "そんなコーデックはない" in shown[0][1]


def test_save_with_no_tabs_returns_false(window: MainWindow) -> None:
    window.tabs.clear()
    assert window.save_file() is False
    assert window.save_file_as() is False


def test_file_dialogs_start_in_the_current_tabs_directory(
    window: MainWindow, tmp_path: Path
) -> None:
    """ファイルダイアログは、いま開いているファイルと同じ場所から始める。

    同じフォルダの隣のファイルを開く / そこへ保存するのが一番多い操作なので、
    毎回ホームから辿り直させない。**現在のタブに行き先が無いときだけ**
    ホームへ落とす。
    """
    target = fileio.resolve_path(tmp_path / "既にあるファイル.txt")
    target.write_text("本文\n", encoding="utf-8")
    opened = window.open_path(target)

    # 開いているファイルの場所から始まる（「開く」も「名前を付けて保存」も）
    assert window._dialog_directory() == target.parent
    assert window._save_dialog_path(opened) == target

    # 行き先の無い無題タブに切り替えたときだけホームへ落とす
    untitled = window.new_file()
    assert window.current_editor() is untitled
    assert window._dialog_directory() == Path.home()
    assert window._save_dialog_path(untitled) == Path.home() / untitled.display_name


# ----------------------------------------------------------------------
# MainWindow: 未保存の変更の確認
# ----------------------------------------------------------------------
def test_maybe_save_unmodified_is_ok(window: MainWindow) -> None:
    assert window.maybe_save(window.current_editor()) is True


@pytest.mark.parametrize(
    ("button", "expected"),
    [
        (QMessageBox.StandardButton.Discard, True),
        (QMessageBox.StandardButton.Cancel, False),
    ],
)
def test_maybe_save_discard_and_cancel(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, button, expected: bool
) -> None:
    monkeypatch.setattr("editor_app.main_window.show_message_box", lambda *a, **k: button)
    editor = window.current_editor()
    edit_text(editor, "未保存\n")
    assert window.maybe_save(editor) is expected


def test_maybe_save_saves(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "保存する.txt"
    monkeypatch.setattr(
        "editor_app.main_window.show_message_box",
        lambda *a, **k: QMessageBox.StandardButton.Save,
    )
    mock_save_dialog(monkeypatch, path)
    editor = window.current_editor()
    edit_text(editor, "保存された\n")

    assert window.maybe_save(editor) is True
    assert path.read_text(encoding="utf-8") == "保存された\n"


def test_maybe_save_returns_false_when_save_fails(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「保存」を選んでもダイアログをキャンセルしたら、閉じてはいけない。"""
    monkeypatch.setattr(
        "editor_app.main_window.show_message_box",
        lambda *a, **k: QMessageBox.StandardButton.Save,
    )
    mock_save_dialog(monkeypatch, None)
    editor = window.current_editor()
    edit_text(editor, "未保存\n")
    assert window.maybe_save(editor) is False


def test_close_event_cancel_keeps_window_open(
    make_window, monkeypatch: pytest.MonkeyPatch
) -> None:
    # closeEvent はチェックボックス付きのダイアログを使うので、
    # モックするのは show_message_box_with_checkbox の方
    # （戻り値は (押されたボタン, チェックされたか) のタプル）。
    monkeypatch.setattr(
        "editor_app.main_window.show_message_box_with_checkbox",
        lambda *a, **k: (QMessageBox.StandardButton.Cancel, False),
    )
    win = make_window()
    edit_text(win.current_editor(), "未保存\n")

    assert win.close() is False
    assert win.isVisible() is True


def test_close_event_discard_closes(
    make_window, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "editor_app.main_window.show_message_box_with_checkbox",
        lambda *a, **k: (QMessageBox.StandardButton.Discard, False),
    )
    win = make_window()
    edit_text(win.current_editor(), "未保存\n")

    assert win.close() is True
    assert win.isVisible() is False


def test_close_event_without_changes_closes(make_window) -> None:
    win = make_window()
    assert win.close() is True
