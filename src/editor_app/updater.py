"""git clone で導入した Tuxpad を、自分自身で更新するための git 操作.

**このモジュールは Qt に一切依存しない**（``subprocess`` で git を呼ぶだけ）。
ダイアログの出し方・起動時に確認するかどうかといった GUI 側の都合は
`update_check.py` が受け持つ。こうしておくと「更新あり / 最新 / 作業ツリーが
汚い / ff-only 失敗 / git 管理外」の判定を、本物の git リポジトリを一時
ディレクトリに作るだけでヘッドレスにテストできる。

**想定している運用**: 利用PCへ git clone で導入し、開発PC (別マシン) から
push された更新を取り込む。逆に開発PC側でこの機能が誤爆すると、書きかけの
変更を巻き込みかねないので、**未コミットの変更があるときは何もしない**・
**``--ff-only`` が通らなければ中断する**（force しない）ようにしてある。

git の呼び出しは :func:`run_git` の 1 か所に閉じてある。ここで

- ``GIT_TERMINAL_PROMPT=0`` … 資格情報を対話で聞かれても即座に失敗させる
  （Private リポジトリを資格情報なしで fetch したときに、応答する人が
  いない GUI アプリの裏で永久に待つのを防ぐ）
- ``BatchMode=yes`` … ssh でも同じ理由でパスフレーズ等を聞かせない
- ``timeout`` … 通信が沈黙したまま帰ってこないケースを必ず打ち切る

をまとめて面倒を見ている。**git を直接呼ぶコードをここ以外に足さないこと。**
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: git の 1 コマンドに許す時間 (秒)。fetch はネットワーク越しなので、
#: 少し余裕を見つつ「固まったまま」にはならない長さにする。
GIT_TIMEOUT_SECONDS = 30

#: 更新確認の結果。
#: git 管理外の導入（tarball 等）で、更新機能そのものが使えない。
STATE_UNSUPPORTED = "unsupported"
#: 既に最新（取り込むコミットが無い）。
STATE_UP_TO_DATE = "up_to_date"
#: 新しいコミットがある。
STATE_UPDATE_AVAILABLE = "update_available"
#: 未コミットの変更があるので、更新は見送る（主に開発PC）。
STATE_DIRTY = "dirty"
#: 確認そのものに失敗した（通信不通・認証失敗・detached HEAD 等）。
STATE_FAILED = "failed"

#: 更新後に「システム側の入れ直しが要るかもしれません」と添えるべきファイル。
#: 依存パッケージやインストール手順が変わったときだけ知らせたいので、
#: 本文 (`src/`) の変更では出さない。
DEPENDENCY_FILES = ("requirements.txt", "requirements-dev.txt", "install.sh")


@dataclass(frozen=True)
class GitResult:
    """:func:`run_git` の戻り値（``subprocess`` の細かい差を吸収する）。"""

    ok: bool
    stdout: str = ""
    stderr: str = ""

    @property
    def text(self) -> str:
        """前後の空白を落とした標準出力。"""
        return self.stdout.strip()


@dataclass(frozen=True)
class UpdateStatus:
    """更新確認の結果。

    ``state`` は ``STATE_*`` のいずれか。``count`` は取り込めるコミット数
    （``STATE_UPDATE_AVAILABLE`` のときだけ意味がある）。``target`` は
    取り込む先のコミット SHA で、:func:`apply_update` にそのまま渡す
    （確認と適用の間に ``FETCH_HEAD`` が動いても、確認したものを確実に
    取り込むため）。``detail`` は失敗理由など、利用者に見せる補足。
    """

    state: str
    count: int = 0
    target: str = ""
    branch: str = ""
    detail: str = ""

    @property
    def has_update(self) -> bool:
        return self.state == STATE_UPDATE_AVAILABLE


@dataclass(frozen=True)
class UpdateResult:
    """:func:`apply_update` の結果。"""

    ok: bool
    detail: str = ""
    #: 依存・インストール手順に関わるファイルが変わったか
    #: （``./install.sh`` の再実行を勧めるかどうかの判断に使う）。
    dependencies_changed: bool = False
    changed_files: tuple[str, ...] = field(default_factory=tuple)


def run_git(repo_root: Path, *args: str, timeout: int = GIT_TIMEOUT_SECONDS) -> GitResult:
    """``repo_root`` で git を 1 回動かす（このモジュール唯一の git 呼び出し）。

    例外は投げず、失敗はすべて ``GitResult(ok=False, stderr=...)`` に均す
    （呼び出し側が毎回 try 文を書かなくて済むように）。
    """
    env = dict(os.environ)
    # 対話で資格情報を聞かれても待たずに失敗させる（説明はモジュール冒頭）。
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    # 出力を解析するので、利用者のロケールに引きずられないようにする。
    env["LC_ALL"] = "C"

    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GitResult(False, stderr=f"git {args[0] if args else ''} が時間内に終わりませんでした")
    except OSError as exc:  # git が入っていない等
        return GitResult(False, stderr=f"git を実行できませんでした: {exc}")

    return GitResult(
        completed.returncode == 0,
        stdout=completed.stdout or "",
        stderr=(completed.stderr or "").strip(),
    )


def find_repository_root(start: Path | None = None) -> Path | None:
    """自分の居場所から上へ辿って、``.git`` のあるディレクトリを探す。

    インストール先の絶対パスに依存しないよう、``__file__`` を起点にする
    （``install.sh`` は clone 先を選べるため、決め打ちはできない）。
    ``.git`` が見つからなければ ``None``（tarball 等の git 管理外の導入）。
    その場合、更新機能は静かに無効にすること。
    """
    here = (start or Path(__file__)).resolve()
    for directory in [here, *here.parents]:
        if (directory / ".git").exists():
            return directory
    return None


def _current_branch(repo_root: Path) -> tuple[str | None, str]:
    """いるブランチ名を返す。detached HEAD なら ``(None, 理由)``。"""
    result = run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if not result.ok:
        return None, result.stderr or "現在のブランチを取得できませんでした"
    branch = result.text
    if not branch or branch == "HEAD":
        return None, (
            "ブランチではなく特定のコミットを指しています (detached HEAD)。"
            "更新を取り込むには、あらかじめブランチに切り替えてください。"
        )
    return branch, ""


def check_for_updates(
    repo_root: Path | None = None, *, timeout: int = GIT_TIMEOUT_SECONDS
) -> UpdateStatus:
    """``origin`` を見て、取り込める更新があるかどうかを調べる。

    ネットワークを使う（``git fetch``）ので、GUI スレッドから直接呼ばないこと
    （`update_check.py` が別スレッドで呼ぶ）。
    """
    if repo_root is None:
        repo_root = find_repository_root()
    if repo_root is None:
        return UpdateStatus(STATE_UNSUPPORTED, detail="git で導入されていません")

    branch, reason = _current_branch(repo_root)
    if branch is None:
        return UpdateStatus(STATE_FAILED, detail=reason)

    # 未コミットの変更があるなら、そもそも更新しない（開発PCでの誤爆防止）。
    # fetch より先に調べる＝通信する前に降りられる。
    status = run_git(repo_root, "status", "--porcelain", timeout=timeout)
    if not status.ok:
        return UpdateStatus(
            STATE_FAILED, branch=branch, detail=status.stderr or "作業ツリーの状態を確認できませんでした"
        )
    if status.text:
        return UpdateStatus(
            STATE_DIRTY,
            branch=branch,
            detail="保存していない変更（未コミットの変更）があるため、更新を見送りました。",
        )

    fetched = run_git(repo_root, "fetch", "origin", branch, timeout=timeout)
    if not fetched.ok:
        return UpdateStatus(
            STATE_FAILED, branch=branch, detail=fetched.stderr or "origin から取得できませんでした"
        )

    remote = run_git(repo_root, "rev-parse", "FETCH_HEAD", timeout=timeout)
    if not remote.ok:
        return UpdateStatus(
            STATE_FAILED, branch=branch, detail=remote.stderr or "取得した内容を確認できませんでした"
        )
    target = remote.text

    counted = run_git(repo_root, "rev-list", "--count", f"HEAD..{target}", timeout=timeout)
    if not counted.ok:
        return UpdateStatus(
            STATE_FAILED, branch=branch, detail=counted.stderr or "更新の件数を数えられませんでした"
        )
    try:
        count = int(counted.text)
    except ValueError:
        return UpdateStatus(STATE_FAILED, branch=branch, detail="更新の件数を数えられませんでした")

    if count <= 0:
        return UpdateStatus(STATE_UP_TO_DATE, branch=branch, target=target)
    return UpdateStatus(STATE_UPDATE_AVAILABLE, count=count, target=target, branch=branch)


def apply_update(
    repo_root: Path, target: str, *, timeout: int = GIT_TIMEOUT_SECONDS
) -> UpdateResult:
    """``target`` まで ``--ff-only`` で早送りする。

    **force は絶対にしない**。ローカルに push していないコミットがある等で
    早送りできない場合は、理由を返して何もせずに終わる（主に開発PC。
    利用者の手元の変更を消してしまうより、更新しない方がよい）。
    """
    before = run_git(repo_root, "rev-parse", "HEAD", timeout=timeout)
    if not before.ok:
        return UpdateResult(False, detail=before.stderr or "現在のコミットを確認できませんでした")

    merged = run_git(repo_root, "merge", "--ff-only", target, timeout=timeout)
    if not merged.ok:
        return UpdateResult(
            False,
            detail=(
                merged.stderr
                or "早送りで取り込めませんでした（このリポジトリに独自の変更があるようです）"
            ),
        )

    changed = run_git(repo_root, "diff", "--name-only", before.text, "HEAD", timeout=timeout)
    files = tuple(line for line in changed.text.splitlines() if line) if changed.ok else ()
    return UpdateResult(
        True,
        dependencies_changed=any(name in DEPENDENCY_FILES for name in files),
        changed_files=files,
    )
