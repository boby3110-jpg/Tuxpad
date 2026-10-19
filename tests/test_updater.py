"""git 操作そのもの (editor_app/updater.py) のテスト.

``updater.py`` は Qt に依存しないので、**本物の git リポジトリを一時
ディレクトリに 2 つ作って**（origin 役と clone 役）、実際に fetch・
merge させて確かめる。モックで固めるより、git の振る舞い（未コミットの
変更があるとどうなる、早送りできないとどうなる）ごと固定できる。

ここで押さえたいのは、**利用者のファイルを壊しかねない経路**:

- 未コミットの変更があるときに更新しない（開発PCでの誤爆防止）
- ``--ff-only`` が通らないときに force しない
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from editor_app import updater
from editor_app.updater import (
    GitResult,
    STATE_DIRTY,
    STATE_FAILED,
    STATE_UNSUPPORTED,
    STATE_UP_TO_DATE,
    STATE_UPDATE_AVAILABLE,
    apply_update,
    check_for_updates,
    find_repository_root,
    run_git,
)


def git(repo: Path, *args: str) -> None:
    """テストの準備用に git を動かす（失敗したらその場で分かるようにする）。"""
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )


def commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", f"{name} を更新")


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """「開発PC が push する先」に見立てた、コミットが 1 つあるリポジトリ。"""
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.com")
    commit(repo, "README.md", "最初\n")
    return repo


@pytest.fixture
def clone(tmp_path: Path, upstream: Path) -> Path:
    """「利用PC に git clone した Tuxpad」に見立てたリポジトリ。"""
    repo = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(upstream), str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.com")
    return repo


# ----------------------------------------------------------------------
# リポジトリの見つけ方
# ----------------------------------------------------------------------


def test_find_repository_root_walks_up_to_dot_git(clone: Path) -> None:
    """``.git`` のある親までさかのぼって見つけること（導入先パスに依存しない）。"""
    nested = clone / "src" / "editor_app"
    nested.mkdir(parents=True)
    assert find_repository_root(nested / "updater.py") == clone


def test_find_repository_root_accepts_the_repository_itself(clone: Path) -> None:
    """リポジトリ直下そのものを渡されたら、それを返すこと（親から探し始めない）。"""
    assert find_repository_root(clone) == clone


def test_find_repository_root_returns_none_outside_git(tmp_path: Path) -> None:
    """git 管理外（tarball 等）では None を返し、更新機能を無効にできること。"""
    plain = tmp_path / "not-a-repo" / "editor_app"
    plain.mkdir(parents=True)
    assert find_repository_root(plain / "updater.py") is None


def test_check_for_updates_on_non_repository_directory_fails(tmp_path: Path) -> None:
    """git 管理外のディレクトリを明示的に渡された場合は、失敗として返すこと。

    実運用では :func:`find_repository_root` が先に None を返すので
    ここには来ないが、渡されても例外で落ちないことを固定しておく。
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    assert check_for_updates(plain).state == STATE_FAILED


def test_check_for_updates_without_repository_root_is_unsupported(monkeypatch) -> None:
    """``.git`` が見つからなければ、静かに「使えない」を返すこと。"""
    monkeypatch.setattr("editor_app.updater.find_repository_root", lambda: None)
    assert check_for_updates().state == STATE_UNSUPPORTED


# ----------------------------------------------------------------------
# 更新あり / なし
# ----------------------------------------------------------------------


def test_up_to_date_when_upstream_unchanged(clone: Path) -> None:
    status = check_for_updates(clone)
    assert status.state == STATE_UP_TO_DATE
    assert status.count == 0
    assert status.branch == "main"


def test_update_available_counts_new_commits(clone: Path, upstream: Path) -> None:
    commit(upstream, "a.txt", "1\n")
    commit(upstream, "b.txt", "2\n")

    status = check_for_updates(clone)
    assert status.state == STATE_UPDATE_AVAILABLE
    assert status.count == 2
    assert status.has_update is True
    assert status.target  # 取り込む先の SHA が入っていること


def test_update_is_looked_for_on_the_branch_we_are_on(clone: Path, upstream: Path) -> None:
    """今いるブランチの更新だけを見ること（別のブランチの先端を掴まない）。

    ``git fetch origin`` をブランチ名なしで呼ぶと、``FETCH_HEAD`` は
    **origin の既定ブランチ**を指す（追跡設定の無いブランチにいる場合）。
    その先端を取り込み先にしてしまうと、利用者のブランチに**別のブランチの
    内容**を早送りしようとすることになる。ブランチ名を必ず添えること。
    """
    # upstream に main とは別のブランチ work を作り、両方に別々のコミットを積む。
    git(upstream, "checkout", "-b", "work")
    commit(upstream, "work.txt", "作業\n")
    git(upstream, "checkout", "main")
    commit(upstream, "main1.txt", "1\n")
    commit(upstream, "main2.txt", "2\n")

    # 利用側は work にいる（追跡設定は付けない＝ブランチ名なしの fetch と差が出る形）。
    git(clone, "fetch", "origin")
    git(clone, "checkout", "--no-track", "-b", "work", "origin/work")
    git(upstream, "checkout", "work")
    commit(upstream, "work2.txt", "作業 2\n")

    status = check_for_updates(clone)
    assert status.branch == "work"
    assert status.state == STATE_UPDATE_AVAILABLE
    assert status.count == 1  # work に積んだ 1 件だけ（main の 2 件ではない）
    assert status.target == run_git(upstream, "rev-parse", "work").text


def test_dirty_worktree_skips_check(clone: Path, upstream: Path) -> None:
    """未コミットの変更があるときは、更新ありでも取り込みに進まないこと。"""
    commit(upstream, "a.txt", "1\n")
    (clone / "README.md").write_text("編集中\n", encoding="utf-8")

    status = check_for_updates(clone)
    assert status.state == STATE_DIRTY
    assert "未コミット" in status.detail


def test_untracked_file_counts_as_dirty(clone: Path) -> None:
    """追跡していない新しいファイルがあるだけでも見送ること（誤爆防止側に倒す）。"""
    (clone / "作業メモ.txt").write_text("メモ\n", encoding="utf-8")
    assert check_for_updates(clone).state == STATE_DIRTY


def test_detached_head_fails_with_reason(clone: Path, upstream: Path) -> None:
    """ブランチにいないときは、理由を添えて失敗すること（勝手に動かさない）。"""
    head = run_git(clone, "rev-parse", "HEAD").text
    git(clone, "checkout", "--detach", head)

    status = check_for_updates(clone)
    assert status.state == STATE_FAILED
    assert "detached HEAD" in status.detail


def test_unreachable_origin_fails_with_reason(clone: Path, upstream: Path, tmp_path: Path) -> None:
    """origin に届かないとき（通信不通・認証失敗に相当）は失敗を返すこと。"""
    git(clone, "remote", "set-url", "origin", str(tmp_path / "存在しない"))
    status = check_for_updates(clone)
    assert status.state == STATE_FAILED
    assert status.detail


def test_broken_repository_is_failure_not_up_to_date(clone: Path, upstream: Path) -> None:
    """リポジトリ自体が壊れているときに、例外も「最新です」も返さないこと。

    ``.git/index`` を壊すと ``status`` も ``fetch`` も落ちる（電源断や
    NAS 越しの書き込みが途中で切れると実際に起こる）。**どの段で捕まえたかは
    問わない**通し確認で、個々の分岐を縛るものではない（``status`` の
    分岐そのものは
    :func:`test_unreadable_worktree_state_is_not_treated_as_clean` で縛る）。
    """
    commit(upstream, "a.txt", "1\n")
    (clone / ".git" / "index").write_bytes("これは index ではない".encode("utf-8"))

    status = check_for_updates(clone)

    assert status.state == STATE_FAILED
    assert status.branch == "main"  # 分かっている分は返すこと
    assert status.detail


def break_git(monkeypatch, *match: str, stdout: str | None = None) -> None:
    """``match`` で始まる git コマンドだけを差し替える（他は本物のまま動かす）。

    fetch までは成功して**その先だけが失敗する**状況は、本物の git では
    作れない（fetch が成功していれば ``FETCH_HEAD`` は書かれている）ので、
    ここだけは差し替えで確かめる。``stdout`` を渡すと「成功したが出力が
    おかしい」を、渡さないと「失敗した」を再現する。
    """
    real_run_git = updater.run_git

    def fake_run_git(repo_root: Path, *args: str, **kwargs) -> GitResult:
        if args[: len(match)] == match:
            if stdout is not None:
                return GitResult(True, stdout=stdout)
            return GitResult(False, stderr="git がここで失敗した")
        return real_run_git(repo_root, *args, **kwargs)

    monkeypatch.setattr("editor_app.updater.run_git", fake_run_git)


def test_unreadable_worktree_state_is_not_treated_as_clean(
    clone: Path, upstream: Path, monkeypatch
) -> None:
    """``status`` が失敗したときに、きれいな作業ツリー扱いで先へ進まないこと。

    ``status`` が落ちると標準出力は空になるので、失敗を無視すると
    **「未コミットの変更なし」と区別が付かない**まま更新に進んでしまい、
    利用者の書きかけを巻き込みかねない（未コミットなら見送るのが本来の動き）。
    ここだけを失敗させるために差し替えている——本物の壊れた ``.git`` では
    ``fetch`` も一緒に落ちるので、その先の受け皿に助けられて素通りする。
    """
    commit(upstream, "a.txt", "1\n")
    break_git(monkeypatch, "status")

    status = check_for_updates(clone)

    assert status.state == STATE_FAILED
    assert status.has_update is False
    assert "git がここで失敗した" in status.detail  # 理由をそのまま見せること


def test_unreadable_fetch_head_fails(clone: Path, upstream: Path, monkeypatch) -> None:
    """取ってきた内容の位置が読めないときは、失敗として返すこと。"""
    commit(upstream, "a.txt", "1\n")
    break_git(monkeypatch, "rev-parse", "FETCH_HEAD")

    status = check_for_updates(clone)

    assert status.state == STATE_FAILED
    assert status.branch == "main"
    assert status.detail


def test_failure_to_count_commits_is_not_treated_as_up_to_date(
    clone: Path, upstream: Path, monkeypatch
) -> None:
    """件数を数えられなかったときに「最新です」と言わないこと。

    数えられない＝更新があるかどうか分からない、なので失敗を返す。
    ここで 0 件扱いに倒すと、**更新があるのに永久に気づけない**。
    あわせて、**git が言っている理由をそのまま見せる**ことも縛る。
    この検査を外しても、その先の「数字に見えない」の受け皿が拾って
    失敗にはなるが、利用者に出る文言が「更新の件数を数えられませんでした」
    だけになり、**本当の理由（容量不足・オブジェクト破損など）が消える**。
    """
    commit(upstream, "a.txt", "1\n")
    break_git(monkeypatch, "rev-list")

    status = check_for_updates(clone)

    assert status.state == STATE_FAILED
    assert status.has_update is False
    assert "git がここで失敗した" in status.detail


def test_non_numeric_commit_count_fails_instead_of_raising(
    clone: Path, upstream: Path, monkeypatch
) -> None:
    """件数として数字以外が返ってきても、例外にせず失敗に均すこと。

    ここは別スレッド（`update_check.py`）から呼ばれるので、例外が漏れると
    **確認が終わったことすら GUI 側へ返らない**（黙って何も起きない）。
    """
    commit(upstream, "a.txt", "1\n")
    break_git(monkeypatch, "rev-list", stdout="たくさん\n")

    status = check_for_updates(clone)

    assert status.state == STATE_FAILED
    assert status.detail


# ----------------------------------------------------------------------
# 取り込み
# ----------------------------------------------------------------------


def test_apply_update_fast_forwards(clone: Path, upstream: Path) -> None:
    commit(upstream, "a.txt", "新しい内容\n")
    status = check_for_updates(clone)

    result = apply_update(clone, status.target)

    assert result.ok is True
    assert (clone / "a.txt").read_text(encoding="utf-8") == "新しい内容\n"
    assert run_git(clone, "rev-parse", "HEAD").text == status.target


def test_apply_update_reports_dependency_changes(clone: Path, upstream: Path) -> None:
    """requirements.txt / install.sh が変わったら、入れ直しを勧められること。"""
    commit(upstream, "requirements.txt", "PySide6>=6.9\n")
    status = check_for_updates(clone)

    result = apply_update(clone, status.target)

    assert result.ok is True
    assert result.dependencies_changed is True
    assert "requirements.txt" in result.changed_files


def test_apply_update_ignores_ordinary_changes_for_dependency_hint(
    clone: Path, upstream: Path
) -> None:
    commit(upstream, "notes.txt", "ただの変更\n")
    status = check_for_updates(clone)

    result = apply_update(clone, status.target)

    assert result.ok is True
    assert result.dependencies_changed is False


def test_apply_update_refuses_when_not_fast_forwardable(clone: Path, upstream: Path) -> None:
    """ローカルに独自のコミットがあるときは force せず、理由を返すこと。

    開発PC で誤って「更新する」を押しても、手元のコミットが消えないこと。
    """
    commit(upstream, "a.txt", "向こうの変更\n")
    status = check_for_updates(clone)
    commit(clone, "b.txt", "手元の変更\n")  # 分岐させる
    local_head = run_git(clone, "rev-parse", "HEAD").text

    result = apply_update(clone, status.target)

    assert result.ok is False
    assert result.detail
    # 手元のコミットが残っていること（force されていない）。
    assert run_git(clone, "rev-parse", "HEAD").text == local_head
    assert (clone / "b.txt").exists()


def test_apply_update_refuses_when_repository_is_gone(tmp_path: Path) -> None:
    """取り込む直前に導入先が git リポジトリでなくなっていたら、何もせず失敗すること。

    「更新しますか」を出してから利用者が「はい」を押すまでの間に、外付け
    ドライブが外れる・フォルダを移動する、といったことは起こりうる。
    """
    plain = tmp_path / "もう git ではない"
    plain.mkdir()

    result = apply_update(plain, "0" * 40)

    assert result.ok is False
    assert result.detail
    assert result.changed_files == ()


def test_apply_update_refuses_when_current_commit_is_unknown(
    tmp_path: Path, upstream: Path
) -> None:
    """今どのコミットにいるか分からないまま ``merge`` へ進まないこと。

    コミットが 1 つも無いリポジトリ（``git init`` 直後）では
    ``rev-parse HEAD`` が失敗する一方、**``merge --ff-only`` は成功して
    ファイルを展開してしまう**。ここで降りずに進むと、更新自体は通るのに
    「取り込む前の位置」が分からないため差分が取れず、**変更ファイルが空**に
    なる＝``requirements.txt`` や ``install.sh`` が変わっていても
    「``./install.sh`` の再実行を」の案内が出ない（依存が足りないまま
    次回起動して落ちる）。
    """
    fresh = tmp_path / "コミットのない clone"
    fresh.mkdir()
    git(fresh, "init", "-b", "main")
    git(fresh, "config", "user.name", "test")
    git(fresh, "config", "user.email", "test@example.com")
    git(fresh, "remote", "add", "origin", str(upstream))
    git(fresh, "fetch", "origin", "main")
    target = run_git(fresh, "rev-parse", "FETCH_HEAD").text

    result = apply_update(fresh, target)

    assert result.ok is False
    assert result.detail
    # 何も展開されていないこと（merge へ進んでいない）。
    assert not (fresh / "README.md").exists()


# ----------------------------------------------------------------------
# git 呼び出しのラッパー
# ----------------------------------------------------------------------


def test_run_git_returns_failure_instead_of_raising(clone: Path) -> None:
    """失敗しても例外にせず、``ok=False`` に均すこと。"""
    result = run_git(clone, "そんなサブコマンドは無い")
    assert result.ok is False
    assert result.stderr


def test_run_git_disables_interactive_credential_prompt(clone: Path) -> None:
    """資格情報を対話で聞かれない設定になっていること（GUI の裏で固まらせない）。

    ``git var`` 相当の代わりに、環境変数がそのまま子プロセスへ渡ることを、
    git 自身に表示させて確かめる。
    """
    result = run_git(clone, "config", "--get", "user.name")
    assert result.ok is True  # ラッパーが普通に動くこと
    # 実際の環境変数の設定は run_git の中なので、間接的な確認として
    # 到達不能な https リモートに対して即座に失敗することを見る。
    run_git(clone, "remote", "set-url", "origin", "https://127.0.0.1:1/repo.git")
    failed = run_git(clone, "fetch", "origin", "main", timeout=20)
    assert failed.ok is False


def test_run_git_hardens_the_child_environment(clone: Path) -> None:
    """git に渡す環境変数そのものを確かめる（間接的な確認では緩んでも気づけない）。

    上の「到達不能なリモートで失敗する」だけだと、``GIT_TERMINAL_PROMPT`` が
    ``1`` に緩んでも通ってしまう（接続自体が拒否されるため）。git のエイリアス
    （``!`` で始まるとシェルで動く）に環境変数を表示させて、直接確かめる。
    """
    result = run_git(
        clone,
        "-c",
        'alias.showenv=!echo "prompt=$GIT_TERMINAL_PROMPT ssh=$GIT_SSH_COMMAND locale=$LC_ALL"',
        "showenv",
    )
    assert result.ok is True
    assert "prompt=0" in result.text          # 資格情報を対話で聞かせない
    assert "BatchMode=yes" in result.text     # ssh のパスフレーズ等も聞かせない
    assert "locale=C" in result.text          # 出力を解析するのでロケール固定


def test_run_git_gives_up_when_git_never_returns(clone: Path) -> None:
    """git が帰ってこないときは、待ち続けずに打ち切ること。

    ここが効かないと、**更新確認のスレッドが起動のたびに 1 本ずつ残る**
    （通信が沈黙したまま切れない回線・応答しない NAS 越しの origin など）。
    本物の「帰ってこない git」を作るために、git のエイリアスで ``sleep`` を
    動かす。標準出力を ``/dev/null`` へ逃がしているのは、打ち切った後も
    孫プロセスがパイプを握っていると、そこで待たされてしまうため。
    """
    git(clone, "config", "alias.sleeper", "!exec sleep 30 >/dev/null 2>&1")

    started = time.monotonic()
    result = run_git(clone, "sleeper", timeout=1)
    elapsed = time.monotonic() - started

    assert result.ok is False
    assert "時間内" in result.stderr
    # 打ち切ったのに sleep の終わり (30 秒) まで待っていない＝本当に諦めている。
    assert elapsed < 15


def test_run_git_reports_missing_git_command(clone: Path, monkeypatch) -> None:
    """git が入っていない環境でも、例外ではなく理由付きの失敗を返すこと。

    tarball ではなく git clone で入れた利用者でも、後から git を消すことは
    ありうる。ここで例外が漏れると、**更新確認しただけでアプリが落ちる**。
    """
    monkeypatch.setenv("PATH", "")

    result = run_git(clone, "status", "--porcelain")

    assert result.ok is False
    assert "git を実行できませんでした" in result.stderr


def test_run_git_reports_vanished_working_directory(clone: Path) -> None:
    """導入先のフォルダごと消えていても、例外ではなく失敗を返すこと。

    外付けドライブや NAS 上へ導入していて、アプリの起動中に外れた場合に
    実際に起こる（cwd が無いので git を起動する前に OSError になる）。
    """
    shutil.rmtree(clone)

    result = run_git(clone, "status", "--porcelain")

    assert result.ok is False
    assert "git を実行できませんでした" in result.stderr


# ----------------------------------------------------------------------
# 71 回目（仕上げの見直し）で足したぶん。
#
# `tools/mutation_check.py` の要領で `updater.py` をわざと壊してみたところ、
# 下の 7 通りは**どれもテストが赤くならなかった**（＝何も縛っていなかった）。
# いずれも「更新したのに案内が出ない」「見当違いの導入先を更新しようとする」
# といった、実機でしか気づけない壊れ方につながる。
# ----------------------------------------------------------------------


def test_find_repository_root_accepts_dot_git_as_a_file(tmp_path: Path) -> None:
    """``.git`` が**ファイル**の導入（worktree / submodule）も git 管理下とみなすこと。

    ``git worktree add`` や submodule で置いた作業ツリーでは、``.git`` は
    ディレクトリではなく ``gitdir: ...`` と書かれた**ファイル**になる。
    ここを ``is_dir()`` で見てしまうと、そうした導入が丸ごと「git 管理外」
    扱いになり、**更新機能が黙って無効になる**（利用者からは「更新を確認
    しても何も起きない」としか見えない）。
    """
    worktree = tmp_path / "worktree"
    (worktree / "src" / "editor_app").mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /どこか/.git/worktrees/tuxpad\n", encoding="utf-8")

    assert find_repository_root(worktree / "src" / "editor_app" / "updater.py") == worktree


def test_run_git_overrides_a_locale_already_in_the_environment(clone: Path, monkeypatch) -> None:
    """利用者の環境に ``LC_ALL`` があっても、git には ``C`` を渡すこと。

    出力（``status --porcelain`` の有無、``rev-list --count`` の数字）を
    解析しているので、日本語ロケールのまま git を動かすと**訳された文言**が
    返りうる。環境に無いときだけ ``C`` を入れる書き方（``setdefault``）だと、
    日本語環境の実機でだけ静かに壊れる。
    """
    monkeypatch.setenv("LC_ALL", "ja_JP.UTF-8")

    result = run_git(
        clone, "-c", 'alias.showenv=!echo "locale=$LC_ALL"', "showenv"
    )

    assert result.ok is True
    assert "locale=C" in result.text


def test_run_git_overrides_a_credential_prompt_already_in_the_environment(
    clone: Path, monkeypatch
) -> None:
    """環境に ``GIT_TERMINAL_PROMPT=1`` があっても、必ず ``0`` に上書きすること。

    ここが環境任せだと、**答える人のいない GUI の裏で git が入力を待ち続ける**
    （利用者からは「更新の確認が終わらない」に見える）。
    """
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")

    result = run_git(
        clone, "-c", 'alias.showenv=!echo "prompt=$GIT_TERMINAL_PROMPT"', "showenv"
    )

    assert result.ok is True
    assert "prompt=0" in result.text


def test_run_git_keeps_a_custom_ssh_command(clone: Path, monkeypatch) -> None:
    """``GIT_SSH_COMMAND`` だけは利用者の指定を尊重すること（``setdefault``）。

    鍵の置き場所などを自分で指定している利用者の設定を奪うと、
    **更新のためだけに fetch が通らなくなる**。``BatchMode`` を足すのは
    「何も指定が無いとき」の既定にとどめる、という使い分けをここで固定する。
    """
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /どこか/id_ed25519")

    result = run_git(
        clone, "-c", 'alias.showenv=!echo "ssh=$GIT_SSH_COMMAND"', "showenv"
    )

    assert result.ok is True
    assert "ssh=ssh -i /どこか/id_ed25519" in result.text


def test_run_git_trims_the_complaint_from_git(clone: Path) -> None:
    """``stderr`` の前後の空白を落としてから返すこと。

    この文字列はそのままダイアログの本文に差し込まれる。落とさないと
    **末尾に空行の入った間の抜けた知らせ**になる。
    """
    result = run_git(clone, "そんなサブコマンドは無い")

    assert result.ok is False
    assert result.stderr
    assert result.stderr == result.stderr.strip()


def test_apply_update_looks_at_the_whole_update_for_dependency_changes(
    clone: Path, upstream: Path
) -> None:
    """依存の変更は、**取り込んだ全コミット**にまたがって見ること。

    しばらく更新していない利用PC では、一度に何コミットも取り込む。
    ``requirements.txt`` が変わったのが**最後のコミットとは限らない**ので、
    直前の 1 コミットだけを見ていると「``./install.sh`` の再実行を」の案内が
    出ない（依存が足りないまま次回起動して落ちる）。
    """
    commit(upstream, "requirements.txt", "PySide6>=6.9\n")
    commit(upstream, "notes.txt", "そのあとの、ただの変更\n")
    status = check_for_updates(clone)
    assert status.count == 2

    result = apply_update(clone, status.target)

    assert result.ok is True
    assert result.dependencies_changed is True
    assert "requirements.txt" in result.changed_files


def test_apply_update_notices_dependency_changes_mixed_with_ordinary_ones(
    clone: Path, upstream: Path
) -> None:
    """依存ファイル**以外**も一緒に変わっているのが普通の更新であること。

    「変わったファイルが全部依存ファイルのときだけ案内する」では、
    ほとんどの更新で案内が出ない。
    """
    (upstream / "requirements.txt").write_text("PySide6>=6.9\n", encoding="utf-8")
    (upstream / "notes.txt").write_text("本文の変更\n", encoding="utf-8")
    git(upstream, "add", "requirements.txt", "notes.txt")
    git(upstream, "commit", "-m", "依存と本文をまとめて更新")
    status = check_for_updates(clone)

    result = apply_update(clone, status.target)

    assert result.ok is True
    assert result.dependencies_changed is True


@pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt", "install.sh"])
def test_apply_update_notices_each_dependency_file(
    clone: Path, upstream: Path, name: str
) -> None:
    """案内の対象にしているファイルを 1 つずつ確かめる。

    一覧 (:data:`updater.DEPENDENCY_FILES`) から 1 行こぼれても、
    代表 1 件だけを見るテストでは気づけない。
    """
    commit(upstream, name, "変わった\n")
    status = check_for_updates(clone)

    result = apply_update(clone, status.target)

    assert result.ok is True
    assert result.dependencies_changed is True
    assert name in result.changed_files


def test_git_timeout_is_short_enough_to_answer_a_person_who_pressed_the_button() -> None:
    """``GIT_TIMEOUT_SECONDS`` が「待てる長さ」に収まっていること.

    **この定数は、黙った相手に対する唯一の歯止めである。** GUI 側
    (`update_check.py`) は ``check_for_updates(repo_root)`` と呼ぶだけで
    ``timeout`` を一度も渡していないので、実際に効くのは常にこの既定値。

    長くし過ぎると、「設定 → 更新 → 更新を確認」を押した人が**答えを
    もらえなくなる**。確認中は ``_update_check_running`` が立っていて、
    もう一度押しても「更新を確認しています。」と返すだけなので、
    **そのセッションの間ずっとメニューが死ぬ**（確認スレッドは daemon
    なのでアプリは終われるが、終わるまで結果は出ない）。
    短くし過ぎると、遅い回線や NAS 越しの ``git fetch`` が正常なのに
    毎回「確認できませんでした」になる。

    そこで**値そのものを固定せず**（調整の余地は残す）、
    「人が待てる範囲か」だけを縛る。2026-10-18（77 回目）に変異
    ``up-timeout-a-day``（30 秒 → 86400 秒）が全 1981 件すべて緑のまま
    通り抜けたため追加した。
    """
    assert 5 <= updater.GIT_TIMEOUT_SECONDS <= 120


def test_the_gui_relies_on_the_default_git_timeout() -> None:
    """更新確認の入口が ``timeout`` を自分で渡していないこと.

    上のテストが守っているのは**既定値**なので、もし GUI 側が独自の
    ``timeout`` を渡すようになったら、既定値をいくら縛っても意味が無くなる
    （上のテストは緑のまま、実際の歯止めだけが外れる）。
    その取り違えに気づけるよう、既定値に任せていること自体を押さえておく。
    """
    import ast
    import inspect
    import textwrap

    from editor_app import update_check

    tree = ast.parse(textwrap.dedent(inspect.getsource(update_check.UpdateCheckMixin)))
    called: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in ("check_for_updates", "apply_update"):
            continue
        called.append(node.func.id)
        passed = {keyword.arg for keyword in node.keywords}
        assert "timeout" not in passed, (
            f"{node.func.id} に timeout を渡すようになったら、"
            "GIT_TIMEOUT_SECONDS を縛っても歯止めにならない"
        )

    # 呼び出しそのものが消えて（名前が変わって）いたら、上の for 文は
    # 1 度も回らずに緑になる。それを「確かめた」と取り違えないための念押し。
    assert sorted(called) == ["apply_update", "check_for_updates"]
