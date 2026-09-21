"""保存が「どう書くか」——一時ファイルの置き場・名前と、ディスクへの押し出し.

:func:`~editor_app.fileio.write_encoded_file` が**ディスクへ書く唯一の場所**で、
既定では「同じフォルダに一時ファイルを作って :func:`os.replace` で置き換える」
方式を取る。既にあるテストは**その結果**（中身・パーミッション・リンクの姿・
失敗したときにゴミを残さないこと）を見ているが、**書き方そのもの**——

- 一時ファイルを**保存先と同じフォルダに**作ること
- その一時ファイルを**隠しファイルの名前に**すること
- 名前を差し替える**前に**中身をディスクへ押し出す (:func:`os.fsync`) こと

——は、どれも壊しても既存のテストが 1 件も赤くならなかった（今回、変異を
先に当てて確かめた）。いずれも利用者の最優先事項「ファイルが壊れて
開けなくなる／保存できなくなる」に直結する:

- **同じフォルダでないと、そもそも保存できない。** :func:`os.replace` は
  ファイルシステムをまたげない（``OSError: [Errno 18] Invalid cross-device
  link``）。利用者は rclone マウントや NAS の共有フォルダで作業しており、
  そこは ``/tmp`` とは別のファイルシステムになる。一時ファイルの置き場が
  ``/tmp`` へずれると、**手元のフォルダでは成功し、rclone のフォルダだけ
  必ず失敗する**という、いちばん気づきにくい壊れ方をする。
- **隠しファイルでないと、同期に拾われる。** 書いている途中の
  ``名前.txt.xxxx.tmp`` が見えていると、rclone / NAS の同期がその一瞬を
  拾って、消えるだけのファイルを同期先に作る。
- **押し出していないと、電源断で中身が飛ぶ。** 名前の差し替えだけが先に
  ディスクへ届き、中身が届いていない状態で電源が落ちると、**保存したはず
  のファイルが空** になる。ハードリンクの経路（直接上書き）では
  :func:`~editor_app.fileio.write_encoded_file` の説明が「電源が落ちた時
  だけ壊れうる」と明言しているとおり、押し出しがそこの唯一の備えになる。

ここでは ``os.replace`` / ``os.fsync`` を**本物に通しつつ記録する**形で
差し替えて（動きは変えない）、書き方そのものを縛る。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from editor_app import fileio


def _record_replace(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, Path]]:
    """``os.replace`` の呼ばれ方を記録する（本物の置き換えはそのまま行う）。"""
    calls: list[tuple[Path, Path]] = []
    real = os.replace

    def spy(src, dst):
        calls.append((Path(src), Path(dst)))
        return real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    return calls


def _record_order(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """``os.fsync`` と ``os.replace`` の**順番**を記録する。"""
    order: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def fsync_spy(fd):
        order.append("fsync")
        return real_fsync(fd)

    def replace_spy(src, dst):
        order.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync_spy)
    monkeypatch.setattr(os, "replace", replace_spy)
    return order


def _hardlink_supported(tmp_path: Path) -> bool:
    """このファイルシステムでハードリンクが張れるか（``test_file_links.py`` と同じ予防線）。"""
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
# 一時ファイルの置き場と名前
# ---------------------------------------------------------------------------


def test_temporary_file_is_created_next_to_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一時ファイルは、保存先と**同じフォルダ**に作られる。

    ``/tmp`` などへずれると :func:`os.replace` がファイルシステムをまたげず
    ``EXDEV`` で失敗する。手元では通ってしまうので、テストで固定しておかないと
    **rclone / NAS のフォルダでだけ保存できない**状態に気づけない。
    """
    path = tmp_path / "sample.txt"

    calls = _record_replace(monkeypatch)
    fileio.write_text_file(path, "本文\n", "utf-8", "\n")

    assert len(calls) == 1
    source, destination = calls[0]
    assert destination == path
    assert source.parent == path.parent


def test_temporary_file_is_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """書いている途中の一時ファイルは隠しファイル（``.`` で始まる名前）。

    見える名前だと、rclone / NAS の同期や他のツールが**一瞬だけ存在する
    ファイル**を拾ってしまう。
    """
    path = tmp_path / "sample.txt"

    calls = _record_replace(monkeypatch)
    fileio.write_text_file(path, "本文\n", "utf-8", "\n")

    source, _destination = calls[0]
    assert source.name.startswith(".")
    assert source.name.endswith(".tmp")


def test_temporary_file_name_says_which_file_it_belongs_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一時ファイルの名前には、保存先のファイル名が入っている。

    保存が失敗した瞬間に電源が落ちるなど、後始末が効かない形でゴミが残った
    とき、**どのファイルの書きかけか**が名前から分かるようにしてある
    （利用者が手で片付けられる唯一の手掛かり）。
    """
    path = tmp_path / "日報.txt"

    calls = _record_replace(monkeypatch)
    fileio.write_text_file(path, "本文\n", "utf-8", "\n")

    source, _destination = calls[0]
    assert path.name in source.name


# ---------------------------------------------------------------------------
# ディスクへの押し出し（電源断で中身だけが飛ばないように）
# ---------------------------------------------------------------------------


def test_bytes_reach_the_disk_before_the_name_is_swapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """中身をディスクへ押し出してから、名前を差し替える。

    順番が逆（または押し出しが無い）だと、名前の差し替えだけがディスクへ
    届いた状態で電源が落ちたとき、**保存したはずのファイルが空**になる。
    """
    path = tmp_path / "sample.txt"

    order = _record_order(monkeypatch)
    fileio.write_text_file(path, "本文\n", "utf-8", "\n")

    assert "fsync" in order
    assert "replace" in order
    assert order.index("fsync") < order.index("replace")


def test_hardlink_overwrite_also_reaches_the_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ハードリンクの直接上書き経路でも、中身をディスクへ押し出す。

    この経路は名前の差し替えを使えない（リンクが切れてしまう）ので、
    :func:`~editor_app.fileio.write_encoded_file` の説明どおり「電源が
    落ちた時だけ壊れうる」形になっている。押し出しが**その唯一の備え**
    なので、無くなると「壊れうる場面」が電源断以外にも広がる。
    """
    if not _hardlink_supported(tmp_path):
        pytest.skip("このファイルシステムではハードリンクが張れない")

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes("古い\n".encode("utf-8"))
    os.link(a, b)

    order = _record_order(monkeypatch)
    fileio.write_encoded_file(a, "新しい\n".encode("utf-8"))

    assert "fsync" in order
    # 直接上書きなので、名前の差し替えは使わない（リンクが切れるため）。
    assert "replace" not in order
    assert a.read_bytes() == b.read_bytes() == "新しい\n".encode("utf-8")
