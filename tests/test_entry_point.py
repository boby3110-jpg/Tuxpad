"""起動そのもの（``python3 src/main.py``）のテスト。

``src/main.py`` は**利用者が実際に叩く入り口**で、``install.sh`` が作る
ランチャも ``.venv/bin/python <クローン先>/src/main.py`` を呼ぶ。ところが
テストの側は ``pytest.ini`` の ``pythonpath = src`` によって ``editor_app``
を最初から import できてしまうため、**この 7 行が壊れていても全テストが
緑のまま通る**（``coverage`` でも ``src/main.py`` だけがずっと 0% だった）。
壊れ方は「アプリが起動しない」で、利用者がいちばん最初に踏む所なので、
ここだけは**本物の別プロセス**で確かめる。

``src/main.py`` の働きは 3 つあり、それぞれ別に縛ってある。

1. ``__main__`` として実行されたら :func:`editor_app.app.main` を呼び、
   **その戻り値をそのまま終了コードにする**（``raise SystemExit(main())``）。
   ここは Qt を一切読み込まずに確かめたいので、偽の ``editor_app.app`` を
   ``sys.modules`` へ先に置いてから実行する。
2. ``src`` を ``sys.path`` に足すので、**どの作業ディレクトリから実行しても**
   ``editor_app`` を import できる（``PYTHONPATH`` を外した別プロセスで
   確かめる）。
3. その足し方は ``Path(__file__).resolve().parent`` なので、**シンボリック
   リンク越しに実行しても**リンク先の ``src`` を指す（``~/bin`` などから
   リンクを張る使い方で効く）。

``runpy.run_path`` を使い、``run_name`` を変えて「``__main__`` として実行
された場合」と「そうでない場合」を撃ち分けている。本物の
``python3 src/main.py`` をそのまま起動すると ``QApplication.exec()`` で
イベントループに入ってしまい、ヘッドレスでは戻ってこないため。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINT = REPO_ROOT / "src" / "main.py"

#: 別プロセスで ``src/main.py`` を実行する小さな道具。
#:
#: 引数は (実行するファイル, ``run_name``, ``fake`` か ``real``)。``fake``
#: のときは、偽の ``editor_app.app`` を ``sys.modules`` へ先に置いて
#: **本物の import を起こさせない**（Qt を読まずに、呼ばれ方だけを見る）。
#:
#: ``real`` のときは本物を import するので、``QApplication.exec()`` を
#: 先に潰しておく。**``if __name__`` の番人が壊れた状態で走らせても、
#: イベントループに入って固まらないようにするため**（固まると
#: ``pytest.ini`` の ``faulthandler_exit_on_timeout`` がテスト全体を
#: 道連れに落とす）。番人そのものは
#: :func:`test_entry_point_does_not_run_main_when_imported` が別に見ている。
_RUNNER = """
import runpy, sys, types

target, run_name, mode = sys.argv[1], sys.argv[2], sys.argv[3]

if mode == "fake":
    def _main():
        print("MAIN_CALLED")
        return 7

    app = types.ModuleType("editor_app.app")
    app.main = _main
    package = types.ModuleType("editor_app")
    package.app = app
    sys.modules["editor_app"] = package
    sys.modules["editor_app.app"] = app
else:
    from PySide6.QtWidgets import QApplication

    QApplication.exec = lambda self: 0

namespace = runpy.run_path(target, run_name=run_name)
print("MAIN_IS_CALLABLE", callable(namespace.get("main")))
"""


def _run(entry_point: Path, run_name: str, mode: str, cwd: Path) -> subprocess.CompletedProcess:
    """``entry_point`` を、``editor_app`` が見えない環境の別プロセスで実行する。

    ``PYTHONPATH`` を外すのがこのテストの要。残したままだと ``src`` が
    最初から ``sys.path`` に入っていて、**``src/main.py`` が何もしなくても
    import が通ってしまう**（＝何も確かめていないことになる）。
    """
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run(
        [sys.executable, "-c", _RUNNER, str(entry_point), run_name, mode],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=120,
    )


def test_entry_point_returns_main_exit_code() -> None:
    """``__main__`` として実行すると ``main()`` を呼び、戻り値で終了する。

    終了コードを取り違えると、``install.sh`` のランチャや
    ``systemd``・シェルの ``&&`` から見て「失敗したのに成功」に見える。
    """
    proc = _run(ENTRY_POINT, "__main__", "fake", REPO_ROOT)

    assert "MAIN_CALLED" in proc.stdout, proc.stderr
    assert proc.returncode == 7, f"終了コードが main() の戻り値でない: {proc.returncode}"


def test_entry_point_does_not_run_main_when_imported() -> None:
    """``__main__`` でなければ ``main()`` は呼ばれない（``if`` の番人）。

    番人が外れると、``src/main.py`` を import しただけでウィンドウが開く。
    """
    proc = _run(ENTRY_POINT, "not-main", "fake", REPO_ROOT)

    assert proc.returncode == 0, proc.stderr
    assert "MAIN_CALLED" not in proc.stdout
    assert "MAIN_IS_CALLABLE True" in proc.stdout


def test_entry_point_imports_editor_app_from_another_directory(tmp_path: Path) -> None:
    """``PYTHONPATH`` 無し・別の作業ディレクトリからでも import が通る。

    ``sys.path`` に足すパスを ``__file__`` からではなく相対で作ってしまうと、
    リポジトリ直下から起動したときだけ動いて、ランチャ（作業ディレクトリは
    利用者のホーム等）からは起動しない、という形で壊れる。
    """
    proc = _run(ENTRY_POINT, "not-main", "real", tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "MAIN_IS_CALLABLE True" in proc.stdout


def test_entry_point_works_through_a_symlink(tmp_path: Path) -> None:
    """シンボリックリンク越しに実行しても、リンク先の ``src`` を見つける。

    ``Path(__file__).resolve()`` の ``resolve()`` がこれを支えている
    （``~/bin/tuxpad`` のようなリンクを張る使い方で効く）。
    """
    link = tmp_path / "tuxpad-link.py"
    link.symlink_to(ENTRY_POINT)

    proc = _run(link, "not-main", "real", tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "MAIN_IS_CALLABLE True" in proc.stdout
