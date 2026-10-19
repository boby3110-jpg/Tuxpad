"""控えの**読み取り側**のテスト（引き継ぎ ⑦ の続き・68 回目）.

`tests/test_backups.py` は「上書きされるバイト列がデコードを挟まずに残るか」
＝**控える側**を厚く見ている。こちらはその裏側——**溜まった控えのうち、
どれが一覧に出るか／利用者はそれを何で見分けて選ぶか**——を見張る。

ここが効かないと、最後の安全網は次の形で静かに破れる:

- **一覧に出ない。** 索引 (JSON) が 1 件でも読めない形だと、そこで例外に
  なって**控えの一覧ごと出なくなる**（読めない 1 件を飛ばすだけのはず）。
  戻せる控えがディスクにあるのに、利用者には「控えはありません」に見える。
- **置き場が見つからない。** 置き場は ``XDG_CACHE_HOME`` に従うが、
  **空の値**（設定してあるが中身が無い環境変数）を真に受けると、
  相対パスの置き場を作ってしまう。控えは残るのに、次に起動した場所が
  違えば引けなくなる。
- **選び間違える。** 復元の一覧に出るのは**日時と大きさだけ**で、
  どのファイルの控えかは見分けられない（`backups` のモジュール説明）。
  ここから秒や日付や大きさが落ちると、**利用者は別の時点の控えで
  今のファイルを上書きする**。復元自体は取り消せるが、
  「戻したい内容がどれだったか」は分からなくなる。

Qt には依存しない（`editor_app.backups` は GUI から切り離してある）。
復元の一覧に**実際に並ぶ文字列**の形は、`tests/test_backups.py` の
``test_the_restore_list_shows_the_date_time_and_size_of_each_backup`` で見る。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from editor_app import backups


def _write_index(data_path: Path, index) -> None:
    """控えの索引 (JSON) を、そのまま書き換える。"""
    data_path.with_suffix(backups.INDEX_SUFFIX).write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )


def _two_backups(tmp_path: Path) -> tuple[Path, Path, Path]:
    """同じファイルの控えを 2 件作り、(元のファイル, 1 件目, 2 件目) を返す。"""
    path = tmp_path / "sample.txt"
    path.write_text("一\n", encoding="utf-8")
    first = backups.backup_file(path)
    path.write_text("二\n", encoding="utf-8")
    second = backups.backup_file(path)
    return path, first, second


# ----------------------------------------------------------------------
# どの控えが一覧に出るか（読めない索引で全部を落とさない）
# ----------------------------------------------------------------------
def test_an_index_missing_its_keys_drops_only_that_entry(tmp_path: Path) -> None:
    """**キーが欠けている索引は、その 1 件を飛ばすだけ。**

    `test_unreadable_index_drops_only_that_entry` は「JSON として読めない」
    形だけを見ていた。JSON としては読めるのに中身が足りない索引——
    **書き込みが途中で切れた**、**古い形式のまま残っている**——では
    `KeyError` になり、受け止めていないと `list_backups` が例外で抜ける。
    そうなると**ディスクには残っている他の控えも一覧に出ず**、利用者には
    「控えはありません」と同じに見える（戻せるのに戻せない）。
    """
    path, broken, kept = _two_backups(tmp_path)
    _write_index(broken, {})

    (entry,) = backups.list_backups(path)

    assert entry.data_path == kept
    assert entry.data_path.read_bytes() == "二\n".encode()


def test_an_index_whose_values_are_empty_drops_only_that_entry(tmp_path: Path) -> None:
    """**値が null の索引も、その 1 件を飛ばすだけ。**

    キーはあるが値が入っていない形（`Path(None)` は `TypeError`）。
    上と同じ理由で、ここで例外を通すと一覧が丸ごと出なくなる。
    """
    path, broken, kept = _two_backups(tmp_path)
    _write_index(
        broken, {backups.INDEX_SOURCE_KEY: None, backups.INDEX_SAVED_AT_KEY: None}
    )

    (entry,) = backups.list_backups(path)

    assert entry.data_path == kept


def test_a_bucket_of_only_broken_indexes_lists_nothing(tmp_path: Path) -> None:
    """読めない索引しか無ければ、空の一覧が返る（例外にはしない）。

    この形のときに `restore_from_backup` が「控えはありません」と伝えて
    終われるのは、ここが例外を投げないからである。
    """
    path, first, second = _two_backups(tmp_path)
    _write_index(first, {})
    _write_index(second, [])

    assert backups.list_backups(path) == []


# ----------------------------------------------------------------------
# 置き場（控えを探しに行く先）
# ----------------------------------------------------------------------
def test_an_empty_xdg_cache_home_falls_back_to_the_home_cache(monkeypatch) -> None:
    """**空の ``XDG_CACHE_HOME`` は「設定されていない」と同じに扱う。**

    freedesktop の決まりでもそうなっている。空の値をそのまま繋ぐと
    `tuxpad/backups` という**相対パス**になり、控えは「そのとき
    アプリを起動した場所」の下に散る。**控えたこと自体は成功する**ので
    気づけず、復元しようとした時だけ「控えはありません」になる。

    `test_backup_root_follows_xdg_cache_home` は環境変数を**消した**
    場合だけを見ており、空の値はここまで誰も通っていなかった。
    """
    monkeypatch.setenv("XDG_CACHE_HOME", "")

    assert backups.cache_root() == Path.home() / ".cache" / "tuxpad"
    assert backups.backup_root().is_absolute()


# ----------------------------------------------------------------------
# 利用者が見て選ぶ 1 行（日時と大きさ）
# ----------------------------------------------------------------------
def test_kilobytes_are_measured_in_1024_byte_units() -> None:
    """KB は 1024 で割る（1000 ではない）。

    `test_size_is_described_in_readable_units` が使っている 2048 は
    **1024 で割っても 1000 で割っても "2.0 KB"** になるため、桁の取り方を
    1000 に変えても赤くならなかった。差が出る大きさで確かめる。
    """
    assert backups.describe_size(3072) == "3.0 KB"
    assert backups.describe_size(10240) == "10.0 KB"


def test_the_unit_switches_exactly_at_1024_bytes_and_at_1_mb() -> None:
    """単位の切り替わりはちょうど 1024 バイト・ちょうど 1 MB。

    境目そのもの（`<` を `<=` にしただけ）でも、一覧には "1024 B" や
    "1024.0 KB" という**桁を数え直さないと読めない表記**が出てしまう。
    大きさは控えを見分ける手がかりの半分なので、読める形に固定する。
    """
    assert backups.describe_size(1023) == "1023 B"
    assert backups.describe_size(1024) == "1.0 KB"
    assert backups.describe_size(1024 * 1024 - 1) == "1024.0 KB"
    assert backups.describe_size(1024 * 1024) == "1.0 MB"


def _entry(saved_at: datetime, size: int) -> backups.BackupEntry:
    """一覧の 1 行を作るための、控え 1 件ぶんの値。"""
    return backups.BackupEntry(
        data_path=Path("/cache/tuxpad/backups/0123456789abcdef/x.bak"),
        source_path=Path("/home/user/memo.txt"),
        saved_at=saved_at,
        size=size,
    )


def test_the_line_shows_the_date_the_time_to_the_second_and_the_size() -> None:
    """一覧の 1 行は「年月日 時分秒（大きさ）」。

    `test_restore_menu_lists_backups_newest_first` は
    ``chosen.items == [describe_entry(entry) for entry in entries]`` と
    **同じ関数どうしを見比べている**ので、この 1 行から何が落ちても
    赤くならない。ここで形そのものを書き留めておく。
    """
    assert backups.describe_entry(_entry(datetime(2026, 9, 27, 18, 15, 59), 2048)) == (
        "2026-09-27 18:15:59（2.0 KB）"
    )


def test_the_line_shows_how_big_the_backup_is() -> None:
    """大きさが出る（日時だけにはしない）。

    同じ日に何度も保存したファイルでは、日時だけでは「どの時点か」を
    思い出せないことがある。**大きさの違い**（編集して増えた／減った）が、
    戻したい控えを選ぶもう半分の手がかりになる。
    """
    line = backups.describe_entry(_entry(datetime(2026, 9, 27, 18, 15, 59), 512))

    assert backups.describe_size(512) in line
    assert "512 B" in line


def test_two_backups_in_the_same_minute_are_told_apart() -> None:
    """同じ分の中の 2 件が、一覧で別の行になる（秒まで出す）。

    控えが増えるのは「保存する直前」なので、**1 分のうちに 2 件**は
    普通に起こる。秒が落ちると 2 行が字面まで同じになり、利用者は
    **どちらを選んでいるか分からないまま**今のファイルを上書きする。
    """
    early = backups.describe_entry(_entry(datetime(2026, 9, 27, 18, 15, 2), 100))
    late = backups.describe_entry(_entry(datetime(2026, 9, 27, 18, 15, 47), 100))

    assert "18:15:02" in early
    assert "18:15:47" in late
    assert early != late


def test_backups_from_different_days_are_told_apart() -> None:
    """別の日の控えが、一覧で日付から見分けられる。

    控えは既定で 30 日ぶん残る（`RETENTION_DAYS`）ので、一覧には
    **何日も前の控え**が並ぶ。日付が落ちると、時刻だけが同じで中身は
    まったく違う行が隣り合い、**別の日の内容で上書きしかねない**。
    """
    today = backups.describe_entry(_entry(datetime(2026, 9, 27, 18, 15, 59), 100))
    last_week = backups.describe_entry(_entry(datetime(2026, 9, 20, 18, 15, 59), 100))

    assert today.startswith("2026-09-27 ")
    assert last_week.startswith("2026-09-20 ")
    assert today != last_week


@pytest.mark.parametrize("size", [0, 1, 1023, 1024, 1024 * 1024, 300 * 1024 * 1024])
def test_the_line_never_comes_out_empty(size: int) -> None:
    """どの大きさでも、一覧に出す 1 行は空にならない。

    一覧の行が空文字だと、その控えは**選べるのに見えない**（一覧の中で
    行が消えたように見え、番号がずれて別の控えを選ぶ）。
    """
    line = backups.describe_entry(_entry(datetime(2026, 9, 27, 18, 15, 59), size))

    assert line.strip()
    assert "（" in line and "）" in line
