"""アプリの外側と取り決めている文字列（版をまたいで変えられないもの）。

2026-10-20（80 回目）に、79 回目の申し送り「文字列リテラルは 1 件も見て
いない」に従って `src/` の文字列を数え上げた。``settings.py`` のキーと値は
`test_settings_file_contract.py` で固定したので、ここはそれ以外の
**「相手が居る」文字列**をまとめる。

どれも「書く側と読む側が同じ定数を見ている」ため、定数を書き換えても
1 つのプロセスの中では辻褄が合い、**テストは緑のまま通り抜ける**。
壊れるのは「前の版が残したものを読むとき」と「カーネル・デスクトップ環境
など別のソフトが決めた名前と突き合わせるとき」だけである。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from editor_app import backups
from editor_app import file_watch

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- 控え（バックアップ）のディスク上の形 ------------------------------
#
# 控えは利用者の ``~/.cache/tuxpad/backups`` に残り続ける。拡張子や索引の
# キーを変えると、**前の版が取った控えが一覧に出なくなる**（＝上書きで
# 壊したファイルを復元できない＝この機能の目的そのものを失う）。
# 既存のテストはどれも ``backups.BACKUP_SUFFIX`` のように定数を経由して
# いるので、定数の中身は誰も見ていなかった。


def test_backup_file_names_and_index_keys_are_the_documented_ones(tmp_path: Path) -> None:
    """控えを 1 件取り、ディスク上の名前と索引の中身を**直に**確かめる。"""
    source = tmp_path / "ひとつ.txt"
    source.write_text("もとの内容", encoding="utf-8")

    data_path = backups.backup_file(source)

    assert data_path is not None
    assert data_path.suffix == ".bak", "控えの拡張子（前の版の控えが一覧から外れる）"
    index_path = data_path.with_suffix(".json")
    assert index_path.is_file(), "索引の拡張子"

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert set(index) == {"source", "saved_at"}, "索引 (JSON) のキー"
    assert index["source"] == str(source.resolve())
    # 日時は ISO 8601（``datetime.fromisoformat`` で読み戻せる形）。
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}", index["saved_at"])


def test_backup_written_by_an_older_version_is_still_listed(tmp_path: Path) -> None:
    """**手で書いた**（＝前の版が書いたのと同じ形の）控えが読めること。

    定数を経由せず、名前も中身もこのテストに直接書いてある。ここが赤く
    なったら、利用者のディスクに既にある控えが読めなくなっている。
    """
    source = tmp_path / "むかしのファイル.txt"
    source.write_text("いま", encoding="utf-8")
    resolved = source.resolve()

    bucket = backups._bucket(resolved)
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / "20260101-120000-000000.bak").write_bytes(b"\x82\xa0\x82\xa2")
    (bucket / "20260101-120000-000000.json").write_text(
        json.dumps({"source": str(resolved), "saved_at": "2026-01-01T12:00:00.000000"}),
        encoding="utf-8",
    )

    entries = backups.list_backups(source)

    assert [entry.data_path.name for entry in entries] == ["20260101-120000-000000.bak"]
    assert entries[0].size == 4


# --- /proc/mounts の種別（カーネルが決めた名前） -----------------------
#
# ここに無い種別は「ローカルディスク」扱いになり、**定期的な見回りから
# 外れる**＝サーバー側で書き換えられても気づけない（2026-10-12 に実機で
# 報告された不具合そのもの）。既存のテストは ``cifs`` と ``nfs4`` しか
# 通していなかったので、残りの 9 個は一覧から落ちても誰も気づけなかった。

#: :data:`file_watch.NETWORK_FS_TYPES` に必ず入っていてほしい種別。
#: **一覧を縮めたときに気づけるよう、ここには写しを直接書いてある。**
EXPECTED_NETWORK_FS_TYPES = frozenset(
    {
        "cifs",
        "smbfs",
        "smb3",
        "nfs",
        "nfs4",
        "afs",
        "9p",
        "afpfs",
        "ceph",
        "glusterfs",
        "davfs",
    }
)


def test_network_fs_types_has_not_shrunk() -> None:
    """種別の一覧が縮んでいないこと（増やすのは構わない）。

    **迷ったら足す側に倒す**のがこのモジュールの方針なので、増えた分は
    赤くしない。減った分だけを見る。
    """
    missing = EXPECTED_NETWORK_FS_TYPES - file_watch.NETWORK_FS_TYPES
    assert not missing, f"ネットワーク種別が一覧から落ちている: {sorted(missing)}"


@pytest.mark.parametrize("fstype", sorted(EXPECTED_NETWORK_FS_TYPES))
def test_mount_of_each_network_fs_type_is_treated_as_network(
    tmp_path: Path, fstype: str
) -> None:
    """``/proc/mounts`` の 1 行から、本物の経路で「ネットワーク上」と判定されること。"""
    mounts = tmp_path / "mounts"
    mounts.write_text(
        f"/dev/sda1 / ext4 rw 0 0\n//nas/share /mnt/nas {fstype} rw 0 0\n",
        encoding="utf-8",
    )

    table = dict(file_watch.read_mount_table(mounts))

    assert table["/mnt/nas"] is True, f"{fstype} がローカル扱い（見回りから外れる）"
    assert table["/"] is False


def test_local_disk_types_are_not_treated_as_network(tmp_path: Path) -> None:
    """裏返し：ローカルのディスクを「ネットワーク上」に倒していないこと。

    こちらが壊れると、全部のタブを 45 秒ごとに ``stat`` し続ける
    （実害は小さいが、一覧に何でも足してよいわけではないことの歯止め）。
    """
    mounts = tmp_path / "mounts"
    mounts.write_text(
        "/dev/sda1 / ext4 rw 0 0\n"
        "/dev/sdb1 /data btrfs rw 0 0\n"
        "/dev/sdc1 /win ntfs3 rw 0 0\n"
        "/dev/sdd1 /ntfs fuseblk rw 0 0\n",
        encoding="utf-8",
    )

    table = dict(file_watch.read_mount_table(mounts))

    assert set(table.values()) == {False}


# --- デスクトップ環境との取り決め -------------------------------------


def test_desktop_file_name_matches_the_packaged_desktop_entry() -> None:
    """``setDesktopFileName`` と ``.desktop`` 側の名前が揃っていること。

    ``app.py`` は ``"tuxpad"`` を名乗り、``packaging/tuxpad.desktop.in`` は
    ``StartupWMClass=tuxpad`` と書き、``install.sh`` は
    ``tuxpad.desktop`` という名前で設置する。この 3 つが揃っていないと、
    ウィンドウマネージャがアプリとエントリを結び付けられず、
    **タスクバーのアイコンが既定のものに戻る／別枠に分かれる**。
    片方だけ直したときに気づけるよう、ここで突き合わせる
    （``test_app_startup.py`` は ``app.py`` 側だけを見ている）。
    """
    desktop_in = (REPO_ROOT / "packaging" / "tuxpad.desktop.in").read_text(encoding="utf-8")
    match = re.search(r"^StartupWMClass=(.+)$", desktop_in, re.MULTILINE)
    assert match, "packaging/tuxpad.desktop.in に StartupWMClass が無い"

    source = (REPO_ROOT / "src" / "editor_app" / "app.py").read_text(encoding="utf-8")
    called = re.search(r'setDesktopFileName\("([^"]+)"\)', source)
    assert called, "app.py に setDesktopFileName の呼び出しが無い"

    assert called.group(1) == match.group(1) == "tuxpad"
    assert (REPO_ROOT / "packaging" / "tuxpad.desktop.in").name.startswith(
        f"{match.group(1)}."
    ), ".desktop の設置名（install.sh の DESKTOP_PATH）と揃っていること"
