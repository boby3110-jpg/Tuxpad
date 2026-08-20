"""保存の途中でリンクの類（ハードリンク・シンボリックリンク）を壊さないことを見張る.

**利用者の最優先事項は「ファイルが壊れて開けなくなる」ことを避けること**。
リンクの類は「同じファイルを 2 か所から見える」ようにしてある状態なので、
片方から保存したとき**もう片方だけが古い中身のまま**残るのは、見た目には
気づかないまま「同じはずのファイル」が食い違うという壊れ方になる。
利用者は NAS / rclone を使っており、共有フォルダに硬い・柔らかいリンクを
張って複数の名前から同じファイルを触ることが実際にある。

ここで縛る動きは 2 種類。

- **ハードリンク**（``os.link``）: 保存後も ``st_nlink`` は減らず、
  同じ inode を指したまま **全ての名前が同じ新しい中身**を返す。
  既定の「一時ファイルへ書いて :func:`os.replace`」経路だと、``os.replace``
  はディレクトリ側の名前を差し替えるため、**片方の名前だけが新しい実体**
  を指し、もう片方は古い実体（書き換え前の中身）を指したまま残る。
  :func:`~editor_app.fileio.write_encoded_file` は ``st_nlink > 1`` の
  ときだけ**直接上書き**の経路へ切り替えることでこれを避けている。

- **シンボリックリンク**（``os.symlink``）: 呼び出し側は
  :func:`~editor_app.fileio.resolve_path` を通してから渡す約束
  （``test_save_file.py::test_write_through_symlink_keeps_the_symlink``
  が縛っている）だが、**万一素の :func:`~editor_app.fileio.write_encoded_file`
  にリンクが渡っても**、:func:`os.replace` がリンクを普通のファイルへ
  置き換えてしまう事故を防ぐ最後の砦がある。ここではその最後の砦を縛る。

なお、パーミッションが引き継がれること・umask に従うことは既に
``test_save_file.py`` の ``test_write_keeps_permissions`` などが見ている。
ここではそちらでは見ていない「**リンクそのものの姿**」を縛る。
"""

from __future__ import annotations

import os
from pathlib import Path

from editor_app import fileio


def _hardlink_supported(tmp_path: Path) -> bool:
    """このファイルシステムでハードリンクが張れるかを軽く確かめる.

    ``tmpfs`` はハードリンクを許すが、環境によっては（Windows の一部・
    一部の FUSE マウント）許さない。tests は主に Linux で走るのでほぼ常に
    True になるが、支えていないところで無関係な失敗を吐かないように
    予防線を入れておく。
    """
    a = tmp_path / ".probe.a"
    b = tmp_path / ".probe.b"
    try:
        a.write_bytes(b"")
        os.link(a, b)
    except (OSError, NotImplementedError):
        return False
    finally:
        for p in (a, b):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
    return True


# ---------------------------------------------------------------------------
# ハードリンク
# ---------------------------------------------------------------------------


def test_hardlink_keeps_both_names_pointing_to_the_new_bytes(tmp_path: Path) -> None:
    """ハードリンクが張られた実体を保存すると、**両方の名前が新しい中身**を返す.

    ``os.replace`` はディレクトリ側の名前だけを差し替えるので、既定の
    「一時ファイル + :func:`os.replace`」経路だと**もう片方の名前は古い実体
    （書き換え前の中身）**を指したまま残る（inode が別々になる）。
    ``st_nlink > 1`` のときは直接上書きの経路へ切り替えているので、
    inode は同じままで、両方の名前が同じ新しい中身を返す。
    """
    if not _hardlink_supported(tmp_path):
        import pytest  # noqa: PLC0415 - 予防線が要る場面だけ import する
        pytest.skip("このファイルシステムではハードリンクが張れない")

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"original\n")
    os.link(a, b)
    assert a.stat().st_ino == b.stat().st_ino
    assert a.stat().st_nlink == 2

    fileio.write_encoded_file(a, b"changed\n")

    # 名前どうしは同じ実体を指したまま.
    assert a.stat().st_ino == b.stat().st_ino
    assert a.stat().st_nlink == 2
    assert b.stat().st_nlink == 2
    # そしてどちらの名前で読んでも新しい中身が返る（利用者からは
    # 「同じファイル」なので、片方だけ古いのは事故）.
    assert a.read_bytes() == b"changed\n"
    assert b.read_bytes() == b"changed\n"
    # 一時ファイルの跡が残らないことも見る（既定経路と同じ約束）.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt", "b.txt"]


def test_three_way_hardlink_stays_together(tmp_path: Path) -> None:
    """3 本のハードリンクでも全て同じ実体を指したまま更新される.

    2 本だけの縛りだと ``st_nlink > 1`` の判定が「ちょうど 2」で決め打ちに
    されている壊し方（``> 1`` を ``== 2`` に書き換える等）を捕まえられない。
    3 本目を足して、判定が「複数」を正しく見ているかを縛る。
    """
    if not _hardlink_supported(tmp_path):
        import pytest  # noqa: PLC0415
        pytest.skip("このファイルシステムではハードリンクが張れない")

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    c = tmp_path / "c.txt"
    a.write_bytes(b"seed\n")
    os.link(a, b)
    os.link(a, c)
    assert a.stat().st_nlink == 3

    fileio.write_encoded_file(a, b"three\n")

    assert a.stat().st_nlink == 3
    assert a.stat().st_ino == b.stat().st_ino == c.stat().st_ino
    assert a.read_bytes() == b.read_bytes() == c.read_bytes() == b"three\n"


def test_hardlink_write_keeps_permissions(tmp_path: Path) -> None:
    """ハードリンクの直接上書き経路でも、実体のパーミッションはそのまま残る.

    直接上書きは新規作成ではないので、パーミッションはそもそも変わらない
    はずだが、将来「実体を作り直してから link し直す」といった実装に
    書き換わったときに、パーミッションが 0o600 に化ける事故を先回りで
    捕まえる。
    """
    if not _hardlink_supported(tmp_path):
        import pytest  # noqa: PLC0415
        pytest.skip("このファイルシステムではハードリンクが張れない")

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"x\n")
    a.chmod(0o640)
    os.link(a, b)

    fileio.write_encoded_file(a, b"y\n")

    # どちらから見ても同じパーミッション（同じ実体なので当然）.
    assert a.stat().st_mode & 0o777 == 0o640
    assert b.stat().st_mode & 0o777 == 0o640


# ---------------------------------------------------------------------------
# シンボリックリンク（素の write_encoded_file に直接渡された場合の最後の砦）
# ---------------------------------------------------------------------------


def test_symlink_written_directly_is_kept(tmp_path: Path) -> None:
    """素の :func:`write_encoded_file` にシンボリックリンクを渡しても、
    リンクは普通のファイルに化けず、リンク先の実体が更新される.

    呼び出し側は必ず :func:`~editor_app.fileio.resolve_path` を通してから
    渡す約束だが、その約束が破れても壊れないよう、
    :func:`write_encoded_file` の入口で ``is_symlink`` を判定して実体へ
    付け替えている。ここではその最後の砦を縛る（呼び出し側の
    約束は ``test_save_file.py::test_write_through_symlink_keeps_the_symlink``
    が別に縛っている）。
    """
    real = tmp_path / "real.txt"
    real.write_bytes(b"old\n")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    fileio.write_encoded_file(link, b"new\n")

    assert link.is_symlink()
    assert real.read_bytes() == b"new\n"
    # 「リンクの隣に一時ファイルが残らない」も見ておく. リンクの入っている
    # ディレクトリで一時ファイルを作ってから実体側で書き換える経路なので、
    # 途中で例外が飛ぶと一時ファイルが残りうる（今は残らない）.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["link.txt", "real.txt"]


def test_broken_symlink_does_not_crash_the_writer(tmp_path: Path) -> None:
    """リンク先が無いシンボリックリンクへ書いても、例外で落ちない.

    ``Path.resolve()`` はリンク先が無くても投げないが、
    ``write_encoded_file`` の中で :meth:`Path.lstat` の後に開いたときに
    実体が無ければ**新規作成扱い**で書ける必要がある（利用者が「行き先の
    無いリンクを触ってしまった」に気づける形は、書けてから
    :func:`~editor_app.fileio.read_text_file` 側で判定される）。
    ここでは「少なくとも書き込みそのものは通る」ことを縛る.
    """
    missing_target = tmp_path / "missing.txt"  # 作らない.
    link = tmp_path / "link.txt"
    link.symlink_to(missing_target)

    fileio.write_encoded_file(link, b"new\n")

    # 実体が生まれる（＝リンク先が新しく作られた）.
    assert missing_target.exists()
    assert missing_target.read_bytes() == b"new\n"
    # リンクは残る.
    assert link.is_symlink()
