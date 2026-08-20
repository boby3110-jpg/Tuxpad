"""上書きされる前のファイルを、生のバイト列のまま控えておく（引き継ぎ ⑦）.

**最後の安全網**。文字コードの判定が外れたまま編集・保存してしまうと、
元の内容はディスクから消えて戻せなくなる。開いた時点の印（拮抗の通知）と
保存直前の警告（「文字化けしているかもしれません」）はどちらも**目安**で、
取りこぼしがある（後者は化けた本文の 65% しか拾わない）。ここは
**判定の当たり外れに一切依存しない**——怪しいかどうかを問わず、
上書きする前のバイト列をそのまま複製しておくだけ。

**このモジュールの要は「テキストとして読み書きし直さないこと」**。
デコードして書き戻すと、バックアップ自体が文字コード誤判定の被害を受ける
（元のバイト列に戻せなくなり、控えた意味が無くなる）。読み書きは
:func:`shutil.copy2` と ``write_bytes`` だけで行い、
:mod:`editor_app.fileio` の変換は一切通さない。

Qt には依存しない（判定と同じく、ディスクの話は GUI から切り離しておく）。
GUI 側の入り口は :mod:`editor_app.file_commands` の
``_backup_before_first_write`` と ``restore_from_backup``。

保存先の作り
------------

::

    ~/.cache/tuxpad/backups/
      <元のパスのハッシュ>/
        20260817-181559-123456.bak     ← 生のバイト列（複製そのもの）
        20260817-181559-123456.json    ← 索引（元の絶対パス・日時）

**索引を「1 つの台帳ファイル」にしなかった理由**: ウィンドウは複数
起動でき（別プロセスのこともある）、台帳が 1 つだと同時に書いたときに
壊れる。控えの隣に小さな JSON を置く形なら、書き込みはその 1 件で閉じる。
JSON が壊れていたり消えていたりする控えは、一覧から黙って外れるだけで
他の控えには波及しない。

ディレクトリを元のパスのハッシュで分けているのは、「このファイルの控え」を
引くのに全体を走査しなくて済むようにするため。**ハッシュだけを信用せず、
JSON に入っている元の絶対パスで必ず突き合わせる**（万一ぶつかっても
別のファイルの控えを見せない）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from .fileio import JAPANESE_LEGACY_ENCODINGS

#: 控えを何日で捨てるか。**将来 UI から変えられるようにしたいという要望が
#: あるので、変えるのはこの定数 1 か所で済むようにしてある**（今回は設定 UI
#: までは作らない）。
RETENTION_DAYS = 30

#: 控え全体で使ってよい容量の上限。超えたら古いものから捨てる。
#: 日本語レガシー文字コードのファイルに絞っている（:func:`should_back_up`）
#: ので、実際にはまず届かない想定。
MAX_TOTAL_BYTES = 200 * 1024 * 1024

#: 生のバイト列の複製と、その索引に付ける拡張子。
BACKUP_SUFFIX = ".bak"
INDEX_SUFFIX = ".json"

#: 索引 (JSON) のキー。
INDEX_SOURCE_KEY = "source"
INDEX_SAVED_AT_KEY = "saved_at"

#: 控えのファイル名に使う日時の書き方（そのまま並べ替えれば時刻順になる）。
_STAMP_FORMAT = "%Y%m%d-%H%M%S-%f"

#: 同じマイクロ秒に 2 回控えたときに、名前をずらす回数の上限。
#: ここまで来ることは無いが、無限ループにはしない。
_MAX_NAME_ATTEMPTS = 1000


class BackupEntry(NamedTuple):
    """控え 1 件分。:func:`list_backups` が新しい順に返す。"""

    #: 生のバイト列を複製したファイル
    data_path: Path
    #: 控えを取ったときの、元のファイルの絶対パス
    source_path: Path
    #: 控えを取った日時
    saved_at: datetime
    #: 複製の大きさ（バイト）
    size: int


def cache_root() -> Path:
    """このアプリのキャッシュ置き場（``~/.cache/tuxpad``）。

    ``XDG_CACHE_HOME`` が設定されていればそちらに従う（freedesktop の
    決まり。テストからもここを差し替えて、本人の実際のキャッシュを
    汚さないようにしている）。
    """
    base = os.environ.get("XDG_CACHE_HOME") or ""
    root = Path(base) if base else Path.home() / ".cache"
    return root / "tuxpad"


def backup_root() -> Path:
    """控えの置き場（``~/.cache/tuxpad/backups``）。"""
    return cache_root() / "backups"


def should_back_up(encoding: str) -> bool:
    """その文字コードのファイルを控える対象とするか。

    日本語レガシー文字コード（:data:`~editor_app.fileio.JAPANESE_LEGACY_ENCODINGS`）
    だけに絞る。文字コードの誤判定でファイルが壊れるのは、**どちらでも
    読めてしまうバイト列がある**この 3 つに限られるため。UTF-8 系は
    「読めたなら、ほぼ確実にそれ」なので対象外にしてある
    （全ファイルを対象にするかどうかは利用者の判断待ち）。
    """
    return encoding in JAPANESE_LEGACY_ENCODINGS


def _bucket(path: Path) -> Path:
    """そのファイルの控えを入れるディレクトリ。"""
    digest = hashlib.sha1(str(path).encode("utf-8", "surrogateescape")).hexdigest()
    return backup_root() / digest[:16]


def backup_file(path: Path | str, *, now: datetime | None = None) -> Path | None:
    """``path`` の今のバイト列を、そのまま複製して控える。

    複製したファイルのパスを返す。**元のファイルがまだ無ければ何もせず
    None**（無題タブの初回保存など。失うものが無い）。

    **デコード・エンコードは一切しない。** :func:`shutil.copy2` で
    バイト列と更新日時をそのまま写す。ここでテキストとして読み書きすると、
    バックアップ自体が誤判定の被害を受けて元に戻せなくなる。

    「初めての保存のときだけ控える」という絞り込みは**呼び出し側の仕事**に
    してある（:mod:`editor_app.file_commands`）。復元（7-B）は「今の内容を
    もう一度控えてから上書きする」ので、無条件に控えられる必要がある。

    失敗（キャッシュ置き場が書けない等）は握り潰さずそのまま投げる。
    保存の経路では**呼び出し側が捕まえて保存自体は続ける**（控えが取れない
    ことを理由に保存できなくなる方が損失が大きい）が、復元の経路では
    中止する（取り消せない上書きになってしまうため）。
    """
    source = Path(path).resolve()
    if not source.is_file():
        return None

    stamp = (now or datetime.now()).strftime(_STAMP_FORMAT)
    bucket = _bucket(source)
    bucket.mkdir(parents=True, exist_ok=True)

    data_path = _unique_data_path(bucket, stamp)
    shutil.copy2(source, data_path)
    data_path.with_suffix(INDEX_SUFFIX).write_text(
        json.dumps(
            {
                INDEX_SOURCE_KEY: str(source),
                INDEX_SAVED_AT_KEY: datetime.now().isoformat(timespec="microseconds"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    prune(protected=data_path)
    return data_path


def _unique_data_path(bucket: Path, stamp: str) -> Path:
    """まだ使われていない控えのファイル名を作る。

    同じマイクロ秒に 2 回控えることは実際には起きないが、起きたときに
    **前の控えを黙って踏み潰す**のはこの機能の趣旨に反するので、名前を
    ずらして両方残す。
    """
    for attempt in range(_MAX_NAME_ATTEMPTS):
        suffix = "" if attempt == 0 else f"-{attempt}"
        candidate = bucket / f"{stamp}{suffix}{BACKUP_SUFFIX}"
        if not candidate.exists():
            return candidate
    raise OSError(f"バックアップの名前を決められませんでした: {bucket}")


def list_backups(path: Path | str) -> list[BackupEntry]:
    """``path`` に対応する控えを**新しい順**に返す。無ければ空。

    索引 (JSON) の元パスで突き合わせるので、ハッシュがぶつかっても
    別のファイルの控えは混ざらない。読めない索引はその 1 件を飛ばすだけ
    （控えの一覧が丸ごと出なくなる方が困る）。
    """
    source = Path(path).resolve()
    entries = [
        entry for entry in _entries_in(_bucket(source)) if entry.source_path == source
    ]
    entries.sort(key=lambda entry: entry.saved_at, reverse=True)
    return entries


def all_backups() -> list[BackupEntry]:
    """全ての控えを**古い順**に返す（掃除に使う）。"""
    root = backup_root()
    if not root.is_dir():
        return []
    entries: list[BackupEntry] = []
    for bucket in sorted(root.iterdir()):
        if bucket.is_dir():
            entries.extend(_entries_in(bucket))
    entries.sort(key=lambda entry: entry.saved_at)
    return entries


def _entries_in(bucket: Path) -> list[BackupEntry]:
    """1 つのディレクトリの中の控えを読み取る（順序は問わない）。"""
    if not bucket.is_dir():
        return []
    entries: list[BackupEntry] = []
    for data_path in bucket.glob(f"*{BACKUP_SUFFIX}"):
        entry = _read_entry(data_path)
        if entry is not None:
            entries.append(entry)
    return entries


def _read_entry(data_path: Path) -> BackupEntry | None:
    """控え 1 件を読み取る。索引が無い・壊れている・複製が消えたなら None。"""
    try:
        index = json.loads(
            data_path.with_suffix(INDEX_SUFFIX).read_text(encoding="utf-8")
        )
        source = Path(index[INDEX_SOURCE_KEY])
        saved_at = datetime.fromisoformat(index[INDEX_SAVED_AT_KEY])
        size = data_path.stat().st_size
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return BackupEntry(data_path, source, saved_at, size)


def prune(
    *,
    protected: Path | None = None,
    now: datetime | None = None,
    retention_days: int | None = None,
    max_total_bytes: int | None = None,
) -> list[Path]:
    """期限切れ・容量超過の控えを、**古いものから**捨てる。

    捨てたファイル（``*.bak``）の一覧を返す。専用のバックグラウンド処理は
    持たず、**次に控えを取るとき**に一緒に行う（:func:`backup_file`）。

    上限を省略したときは :data:`RETENTION_DAYS` / :data:`MAX_TOTAL_BYTES` を
    **呼ばれた時点で**見る（既定値として書いてしまうと定義時に焼き付き、
    「定数 1 か所を直せば変えられる」形にならない）。

    ``protected`` に渡した控えは、上限を超えていても捨てない。**今まさに
    控えたばかりのもの**を掃除が持っていくと、安全網を張った瞬間に外す
    ことになるため（上限より 1 つのファイルの方が大きい場合に起きる）。
    """
    moment = now or datetime.now()
    days = RETENTION_DAYS if retention_days is None else retention_days
    limit = MAX_TOTAL_BYTES if max_total_bytes is None else max_total_bytes
    deadline = moment - timedelta(days=days)
    kept: list[BackupEntry] = []
    removed: list[Path] = []

    for entry in all_backups():  # 古い順
        if entry.saved_at < deadline and entry.data_path != protected:
            _remove_entry(entry)
            removed.append(entry.data_path)
        else:
            kept.append(entry)

    total = sum(entry.size for entry in kept)
    for entry in kept:  # 古い順のまま、収まるまで捨てる
        if total <= limit:
            break
        if entry.data_path == protected:
            continue
        _remove_entry(entry)
        removed.append(entry.data_path)
        total -= entry.size

    _remove_empty_buckets()
    return removed


def _remove_entry(entry: BackupEntry) -> None:
    """控え 1 件（複製と索引）を消す。既に無ければ何もしない。"""
    entry.data_path.unlink(missing_ok=True)
    entry.data_path.with_suffix(INDEX_SUFFIX).unlink(missing_ok=True)


def _remove_empty_buckets() -> None:
    """中身が無くなったディレクトリを片付ける（控えの置き場が際限なく
    増え続けないように）。消せなければそのままにする。"""
    root = backup_root()
    if not root.is_dir():
        return
    for bucket in root.iterdir():
        if bucket.is_dir() and not any(bucket.iterdir()):
            try:
                bucket.rmdir()
            except OSError:
                pass


def describe_size(size: int) -> str:
    """控えの大きさを、一覧に並べられる短い表記にする。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def describe_entry(entry: BackupEntry) -> str:
    """控え 1 件を、選ばせる一覧に出す 1 行にする。"""
    return (
        f"{entry.saved_at.strftime('%Y-%m-%d %H:%M:%S')}"
        f"（{describe_size(entry.size)}）"
    )
