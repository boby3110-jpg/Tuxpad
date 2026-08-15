"""install.sh（ディストロ自動判別インストーラ）のテスト。

実際のインストールはクラウドでは回せないので、
  * `--print-plan` で「どのディストロと判別し、どのパッケージを選ぶか」
  * `--desktop-only` で「テンプレートがどう展開されるか」
の 2 つを固定する。どちらもシステムを変更しない経路だけを叩いている。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"

# 実在するディストロの /etc/os-release の要点を写したもの
OS_RELEASES = {
    "ubuntu": 'ID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\n',
    "debian": 'ID=debian\nVERSION_ID="12"\n',
    "linuxmint": 'ID=linuxmint\nID_LIKE="ubuntu debian"\n',
    "fedora": 'ID=fedora\nVERSION_ID="41"\n',
    "rocky": 'ID="rocky"\nID_LIKE="rhel centos fedora"\n',
    "arch": "ID=arch\n",
    "manjaro": 'ID=manjaro\nID_LIKE=arch\n',
    "opensuse": 'ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n',
    "sles": 'ID="sles"\nID_LIKE="suse"\n',
    "weird": 'ID=plan9\nID_LIKE=inferno\n',
    "empty": "\n",
}


@pytest.fixture
def os_release(tmp_path):
    """名前を渡すと os-release ファイルを作ってパスを返す。"""

    def make(name: str) -> str:
        path = tmp_path / f"os-release-{name}"
        path.write_text(OS_RELEASES[name], encoding="utf-8")
        return str(path)

    return make


def run_install(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALL_SH), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def plan_for(os_release_path: str, *extra: str) -> dict[str, str]:
    proc = run_install("--print-plan", "--os-release", os_release_path, *extra)
    assert proc.returncode == 0, proc.stderr
    plan = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        plan[key] = value
    return plan


class TestDistroDetection:
    @pytest.mark.parametrize(
        "name, family, pkg_manager",
        [
            ("ubuntu", "debian", "apt"),
            ("debian", "debian", "apt"),
            ("linuxmint", "debian", "apt"),  # ID_LIKE 経由
            ("fedora", "fedora", "dnf"),
            ("rocky", "fedora", "dnf"),  # ID_LIKE 経由
            ("arch", "arch", "pacman"),
            ("manjaro", "arch", "pacman"),  # ID_LIKE 経由
            ("opensuse", "suse", "zypper"),
            ("sles", "suse", "zypper"),  # ID_LIKE 経由
        ],
    )
    def test_family_and_package_manager(self, os_release, name, family, pkg_manager):
        plan = plan_for(os_release(name))
        assert plan["family"] == family
        assert plan["pkg_manager"] == pkg_manager

    def test_unknown_distro_is_unknown(self, os_release):
        plan = plan_for(os_release("weird"))
        assert plan["family"] == "unknown"
        assert plan["pkg_manager"] == ""

    def test_missing_os_release_is_unknown(self, tmp_path):
        plan = plan_for(str(tmp_path / "does-not-exist"))
        assert plan["family"] == "unknown"

    def test_unknown_distro_aborts_instead_of_pip_installing(self, os_release):
        """判別できないときは pip で無理に入れず中断する（IME が死ぬため）。"""
        proc = run_install("--os-release", os_release("weird"), "--dry-run")
        assert proc.returncode != 0
        assert "自動判別できませんでした" in proc.stderr


class TestPackagePlan:
    def test_debian_has_split_pyside6_modules_including_qtnetwork(self, os_release):
        plan = plan_for(os_release("ubuntu"))
        pyside = plan["pyside_packages"].split()
        # Debian/Ubuntu の PySide6 はモジュール別。IPC で QtNetwork を使う
        assert "python3-pyside6.qtwidgets" in pyside
        assert "python3-pyside6.qtgui" in pyside
        assert "python3-pyside6.qtcore" in pyside
        assert "python3-pyside6.qtnetwork" in pyside
        # QtTest はテスト専用なので base ではなく dev_packages 側（下の専用テスト参照）
        assert "python3-pyside6.qttest" not in pyside

    def test_debian_dev_packages_hold_qttest(self, os_release):
        """QtTest はテスト専用。base ではなく dev_packages に入る（Debian のみ）。"""
        plan = plan_for(os_release("ubuntu"))
        assert plan["dev_packages"].split() == ["python3-pyside6.qttest"]

    def test_nondebian_has_no_dev_packages(self, os_release):
        """全部入りパッケージのディストロは dev 専用 OS パッケージが要らない。"""
        for name in ("fedora", "arch", "opensuse"):
            plan = plan_for(os_release(name))
            assert plan["dev_packages"] == "", name

    def test_debian_runtime_libraries(self, os_release):
        plan = plan_for(os_release("debian"))
        runtime = plan["runtime_packages"].split()
        for lib in ("libegl1", "libgl1", "libxkbcommon0", "libxcb-cursor0"):
            assert lib in runtime

    def test_charset_normalizer_in_every_supported_family(self, os_release):
        for name in ("ubuntu", "fedora", "arch", "opensuse"):
            plan = plan_for(os_release(name))
            packages = plan["extra_packages"]
            assert "charset-normalizer" in packages, name

    def test_opensuse_package_names_carry_python_version(self, os_release):
        plan = plan_for(os_release("opensuse"), "--python", sys.executable)
        tag = "python%d%d" % sys.version_info[:2]
        assert plan["pyside_packages"] == f"{tag}-pyside6"
        assert plan["extra_packages"] == f"{tag}-charset-normalizer"

    def test_arch_uses_single_pyside6_package(self, os_release):
        plan = plan_for(os_release("arch"))
        assert plan["pyside_packages"] == "pyside6"
        assert plan["extra_packages"] == "python-charset-normalizer"

    def test_fedora_uses_single_pyside6_package(self, os_release):
        plan = plan_for(os_release("fedora"))
        assert plan["pyside_packages"] == "python3-pyside6"

    def test_verified_names_status_is_honest(self, os_release):
        """実機で裏取りできた族だけ verified=yes。未確認は正直に no を出す。"""
        # debian(apt CI)・fedora(F44実機)・arch(EndeavourOS実機) は確認済み
        for name in ("ubuntu", "fedora", "arch"):
            assert plan_for(os_release(name))["pkg_names_verified"] == "yes", name
        # opensuse は命名規則ベース（実機の verified フラグは no）
        assert plan_for(os_release("opensuse"))["pkg_names_verified"] == "no"


class TestPrintPlanIsSideEffectFree:
    def test_does_not_create_anything(self, os_release, tmp_path):
        bin_dir = tmp_path / "bin"
        desktop_dir = tmp_path / "applications"
        proc = run_install(
            "--print-plan",
            "--os-release", os_release("ubuntu"),
            "--bin-dir", str(bin_dir),
            "--desktop-dir", str(desktop_dir),
        )
        assert proc.returncode == 0
        assert not bin_dir.exists()
        assert not desktop_dir.exists()

    def test_paths_follow_the_options(self, os_release, tmp_path):
        plan = plan_for(
            os_release("ubuntu"),
            "--bin-dir", str(tmp_path / "bin"),
            "--desktop-dir", str(tmp_path / "apps"),
        )
        assert plan["launcher"] == str(tmp_path / "bin" / "tuxpad")
        assert plan["desktop"] == str(tmp_path / "apps" / "tuxpad.desktop")
        assert plan["install_dir"] == str(REPO_ROOT)


class TestRelativeDirsAreMadeAbsolute:
    """`--bin-dir` / `--desktop-dir` に相対パスが来ても絶対パスに直す。

    相対のまま .desktop に書くと `Exec=bin/tuxpad` になり、デスクトップ環境は
    別の作業ディレクトリから起動するので**メニューには出るのに開けない**。
    desktop-file-validate も素通りする（`Exec=` には PATH から引くコマンド名も
    書けるので、相対パスと見分けが付かない）ため、ここで縛る。
    """

    @staticmethod
    def _run_from(cwd: Path, *args: str) -> subprocess.CompletedProcess:
        """install.sh を「別の作業ディレクトリから」実行する。

        既定の run_install はリポジトリ直下で走るので、相対パスを渡すと
        リポジトリの中に書き込んでしまう。ここだけ cwd を差し替える。
        """
        return subprocess.run(
            ["bash", str(INSTALL_SH), *args],
            capture_output=True,
            text=True,
            cwd=str(cwd),
        )

    @pytest.fixture
    def workdir(self, tmp_path) -> Path:
        # bash は起動時に getcwd() から PWD を作り直す＝シンボリックリンクが
        # 解決されるので、比較する側も実体パスに揃えておく。
        return Path(os.path.realpath(tmp_path))

    def test_plan_shows_absolute_paths(self, workdir):
        proc = self._run_from(
            workdir, "--print-plan", "--bin-dir", "bin", "--desktop-dir", "./apps/"
        )
        assert proc.returncode == 0, proc.stderr
        plan = dict(
            line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line
        )
        assert plan["launcher"] == str(workdir / "bin" / "tuxpad")
        # 先頭の "./" と末尾の "/" は落ちる（// を作らない）
        assert plan["desktop"] == str(workdir / "apps" / "tuxpad.desktop")

    def test_desktop_entry_exec_is_absolute(self, workdir):
        proc = self._run_from(
            workdir, "--desktop-only", "--bin-dir", "bin", "--desktop-dir", "apps"
        )
        assert proc.returncode == 0, proc.stderr
        body = (workdir / "apps" / "tuxpad.desktop").read_text(encoding="utf-8")
        exec_line = next(x for x in body.splitlines() if x.startswith("Exec="))
        command = exec_line[len("Exec=") :].removesuffix(" %U")
        assert command.startswith("/"), exec_line
        assert command == str(workdir / "bin" / "tuxpad")
        # 絶対パスに直したランチャーが、実際にそこに作られていること
        assert (workdir / "bin" / "tuxpad").exists()

    def test_absolute_paths_are_left_alone(self, os_release):
        plan = plan_for(
            os_release("ubuntu"),
            "--bin-dir", "/opt/tuxpad/bin",
            "--desktop-dir", "/opt/tuxpad/apps/",
        )
        assert plan["launcher"] == "/opt/tuxpad/bin/tuxpad"
        assert plan["desktop"] == "/opt/tuxpad/apps/tuxpad.desktop"


class TestTemplateRendering:
    @pytest.fixture
    def rendered(self, tmp_path):
        bin_dir = tmp_path / "bin"
        desktop_dir = tmp_path / "applications"
        proc = run_install(
            "--desktop-only",
            "--bin-dir", str(bin_dir),
            "--desktop-dir", str(desktop_dir),
        )
        assert proc.returncode == 0, proc.stderr
        return bin_dir / "tuxpad", desktop_dir / "tuxpad.desktop"

    def test_launcher_is_executable_and_points_at_this_clone(self, rendered):
        launcher, _ = rendered
        assert launcher.exists()
        assert os.access(launcher, os.X_OK)
        body = launcher.read_text(encoding="utf-8")
        assert str(REPO_ROOT) in body
        assert f'{REPO_ROOT}/.venv/bin/python' in body
        assert f'{REPO_ROOT}/src/main.py' in body

    def test_desktop_entry_points_at_launcher_and_icon(self, rendered):
        launcher, desktop = rendered
        body = desktop.read_text(encoding="utf-8")
        assert f"Exec={launcher} %U" in body
        assert f"Icon={REPO_ROOT}/src/editor_app/resources/icon-256.png" in body
        # デスクトップ側の同定に使っているので落とさない
        assert "StartupWMClass=tuxpad" in body

    def test_no_placeholder_survives(self, rendered):
        for path in rendered:
            assert "__" not in path.read_text(encoding="utf-8")

    @pytest.mark.skipif(
        shutil.which("desktop-file-validate") is None,
        reason="desktop-file-validate が無い",
    )
    def test_desktop_entry_passes_freedesktop_validation(self, rendered):
        _, desktop = rendered
        proc = subprocess.run(
            ["desktop-file-validate", str(desktop)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_referenced_icon_actually_exists(self):
        assert (REPO_ROOT / "src/editor_app/resources/icon-256.png").exists()

    def test_rerunning_is_idempotent(self, tmp_path):
        """冪等: 2 回流しても同じ結果になる。"""
        args = [
            "--desktop-only",
            "--bin-dir", str(tmp_path / "bin"),
            "--desktop-dir", str(tmp_path / "apps"),
        ]
        assert run_install(*args).returncode == 0
        first = (tmp_path / "apps" / "tuxpad.desktop").read_text(encoding="utf-8")
        assert run_install(*args).returncode == 0
        assert (tmp_path / "apps" / "tuxpad.desktop").read_text(encoding="utf-8") == first


def _desktop_unescape(value: str) -> str:
    """.desktop の値（string 型）を元の文字列へ戻す。

    freedesktop の仕様で使えるのは `\\s` `\\n` `\\t` `\\r` `\\\\` の 5 つだけ。
    それ以外の `\\x` は未定義なので、そのまま（`\\` も残す）とする。
    """
    mapping = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    out: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value) and value[i + 1] in mapping:
            out.append(mapping[value[i + 1]])
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def _split_exec(value: str) -> list[str]:
    """`Exec=` のコマンド行を argv へ分解する（freedesktop の規則）。

    `"..."` で囲まれた中では `\\` が次の 1 文字をそのまま出す。囲みの外の
    空白が語の区切り。デスクトップ環境（GLib の g_shell_parse_argv）が
    やっているのと同じことを、依存を増やさずに写したもの。
    Python の shlex は `"..."` の中の `\\` を仕様どおりに扱わないので使えない。
    """
    argv: list[str] = []
    cur: list[str] = []
    started = in_quotes = False
    i = 0
    while i < len(value):
        char = value[i]
        if in_quotes:
            if char == "\\" and i + 1 < len(value):
                cur.append(value[i + 1])
                i += 2
                continue
            if char == '"':
                in_quotes = False
            else:
                cur.append(char)
        elif char == '"':
            in_quotes = started = True
        elif char in " \t":
            if started:
                argv.append("".join(cur))
                cur, started = [], False
        else:
            cur.append(char)
            started = True
        i += 1
    if started:
        argv.append("".join(cur))
    return argv


def exec_argv(desktop_file: Path) -> list[str]:
    body = desktop_file.read_text(encoding="utf-8")
    line = next(x for x in body.splitlines() if x.startswith("Exec="))
    return _split_exec(_desktop_unescape(line[len("Exec=") :]))


class TestPathsWithSpacesAndSymbols:
    """clone 先や --bin-dir にスペース・記号が入っても壊れないこと。

    埋め込む値は 3 か所あり、必要な引用がそれぞれ違う。どれも**黙って**
    壊れるのが厄介だった:

      * ランチャー(.sh) の `cd "..."` … パスに `$b` があると展開されて消え、
        別の場所へ cd してしまう
      * .desktop の `Exec=` … スペースがあると 2 語に割れ、**メニューには
        出るのに押しても開かない**（`desktop-file-validate` は素通りする）
      * .desktop の値全般 … `\\` は `\\\\` と書く決まり

    以前は sed で置換していたため、パスに `&` があると置換後に
    `__INSTALL_DIR__` が復活して「テンプレートに未置換のプレースホルダが
    残っている」と、テンプレートのせいであるかのように止まっていた。
    """

    NASTY_NAMES = [
        "my apps",       # スペース（いちばんありがち）
        "R&D",           # sed の置換文字列で「一致した文字列」に化ける
        "a$b",           # シェルの "..." の中で変数展開されてしまう
        "back`tick`",    # 同上（コマンド置換）
        "semi;colon",    # Exec の予約文字
    ]

    @staticmethod
    def _make_fake_clone(root: Path) -> Path:
        """--desktop-only に必要な最小限だけ持つ、偽の clone を作る。

        本物をコピーすると重いので、install.sh とテンプレート、それに
        「起動できたか」を確かめるための偽 python だけを置く。
        """
        clone = root / "clone"
        (clone / "packaging").mkdir(parents=True)
        (clone / ".venv" / "bin").mkdir(parents=True)
        (clone / "src" / "editor_app" / "resources").mkdir(parents=True)
        shutil.copy(INSTALL_SH, clone / "install.sh")
        for name in ("tuxpad-launcher.sh.in", "tuxpad.desktop.in"):
            shutil.copy(REPO_ROOT / "packaging" / name, clone / "packaging" / name)
        (clone / "src" / "main.py").touch()
        (clone / "src" / "editor_app" / "resources" / "icon-256.png").touch()
        fake_python = clone / ".venv" / "bin" / "python"
        # 起動されたら「どこで・何を渡されたか」を出すだけの偽 python
        fake_python.write_text(
            '#!/bin/sh\necho "cwd=$(pwd)"\necho "argv1=$1"\n', encoding="utf-8"
        )
        fake_python.chmod(0o755)
        return clone

    @pytest.mark.parametrize("name", NASTY_NAMES)
    def test_launcher_starts_the_app_from_the_right_place(self, tmp_path, name):
        base = Path(os.path.realpath(tmp_path)) / name
        clone = self._make_fake_clone(base)
        proc = subprocess.run(
            [
                "bash", str(clone / "install.sh"), "--desktop-only",
                "--bin-dir", str(base / "bin"),
                "--desktop-dir", str(base / "apps"),
            ],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

        launched = subprocess.run(
            [str(base / "bin" / "tuxpad")], capture_output=True, text=True
        )
        assert launched.returncode == 0, launched.stderr
        # clone の中へ cd し、その clone の main.py を起動していること
        assert f"cwd={clone}" in launched.stdout, launched.stdout
        assert f"argv1={clone / 'src' / 'main.py'}" in launched.stdout, launched.stdout

    @pytest.mark.parametrize("name", NASTY_NAMES)
    def test_desktop_exec_stays_one_word(self, tmp_path, name):
        base = Path(os.path.realpath(tmp_path)) / name
        clone = self._make_fake_clone(base)
        proc = subprocess.run(
            [
                "bash", str(clone / "install.sh"), "--desktop-only",
                "--bin-dir", str(base / "bin"),
                "--desktop-dir", str(base / "apps"),
            ],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

        argv = exec_argv(base / "apps" / "tuxpad.desktop")
        # ここが割れていると「メニューに出るのに押しても開かない」
        assert argv == [str(base / "bin" / "tuxpad"), "%U"], argv

    @pytest.mark.skipif(
        shutil.which("desktop-file-validate") is None,
        reason="desktop-file-validate が無い",
    )
    @pytest.mark.parametrize("name", NASTY_NAMES)
    def test_still_passes_freedesktop_validation(self, tmp_path, name):
        base = Path(os.path.realpath(tmp_path)) / name
        clone = self._make_fake_clone(base)
        subprocess.run(
            [
                "bash", str(clone / "install.sh"), "--desktop-only",
                "--bin-dir", str(base / "bin"),
                "--desktop-dir", str(base / "apps"),
            ],
            capture_output=True, text=True, cwd=str(tmp_path), check=True,
        )
        proc = subprocess.run(
            ["desktop-file-validate", str(base / "apps" / "tuxpad.desktop")],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_plain_paths_are_not_quoted(self, tmp_path):
        """記号が無ければ引用しない（実機で確認済みの見た目を変えないため）。"""
        proc = run_install(
            "--desktop-only",
            "--bin-dir", str(tmp_path / "bin"),
            "--desktop-dir", str(tmp_path / "apps"),
        )
        assert proc.returncode == 0, proc.stderr
        body = (tmp_path / "apps" / "tuxpad.desktop").read_text(encoding="utf-8")
        assert f"Exec={tmp_path / 'bin' / 'tuxpad'} %U" in body

    def test_missing_placeholder_value_is_reported(self, tmp_path):
        """テンプレートに知らないプレースホルダが増えたら止まること。"""
        clone = self._make_fake_clone(tmp_path)
        template = clone / "packaging" / "tuxpad.desktop.in"
        template.write_text(
            template.read_text(encoding="utf-8") + "X=__NEW_THING__\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [
                "bash", str(clone / "install.sh"), "--desktop-only",
                "--bin-dir", str(tmp_path / "bin"),
                "--desktop-dir", str(tmp_path / "apps"),
            ],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        assert proc.returncode != 0
        assert "__NEW_THING__" in proc.stderr

    def test_placeholder_like_path_is_not_mistaken_for_one(self, tmp_path):
        """パス自体に `__NAME__` が入っていても誤検知しないこと。

        検査するのは展開**前**のテンプレートなので、値の中身は関係ない。
        """
        base = Path(os.path.realpath(tmp_path)) / "__WORK_DIR__"
        clone = self._make_fake_clone(base)
        proc = subprocess.run(
            [
                "bash", str(clone / "install.sh"), "--desktop-only",
                "--bin-dir", str(base / "bin"),
                "--desktop-dir", str(base / "apps"),
            ],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        argv = exec_argv(base / "apps" / "tuxpad.desktop")
        assert argv == [str(base / "bin" / "tuxpad"), "%U"], argv


class TestTemplatesInRepo:
    def test_templates_exist(self):
        assert (REPO_ROOT / "packaging/tuxpad-launcher.sh.in").exists()
        assert (REPO_ROOT / "packaging/tuxpad.desktop.in").exists()

    def test_templates_have_no_absolute_home_paths(self):
        """他人の環境のパスをベタ書きしない（テンプレート化した理由）。"""
        for name in ("tuxpad-launcher.sh.in", "tuxpad.desktop.in"):
            body = (REPO_ROOT / "packaging" / name).read_text(encoding="utf-8")
            assert "/home/" not in body, name


class TestDryRun:
    def test_dry_run_changes_nothing(self, os_release, tmp_path):
        venv_before = (REPO_ROOT / ".venv").exists()
        proc = run_install(
            "--dry-run",
            "--os-release", os_release("ubuntu"),
            "--bin-dir", str(tmp_path / "bin"),
            "--desktop-dir", str(tmp_path / "apps"),
        )
        assert proc.returncode == 0, proc.stderr
        assert "[dry-run]" in proc.stdout
        assert not (tmp_path / "bin").exists()
        assert not (tmp_path / "apps").exists()
        assert (REPO_ROOT / ".venv").exists() == venv_before

    def test_no_desktop_skips_launcher(self, os_release, tmp_path):
        proc = run_install(
            "--dry-run",
            "--no-desktop",
            "--os-release", os_release("ubuntu"),
            "--bin-dir", str(tmp_path / "bin"),
        )
        assert proc.returncode == 0, proc.stderr
        assert "--no-desktop" in proc.stdout
        assert "tuxpad.desktop" not in proc.stdout


class TestDevFlag:
    """既定はアプリ実行分のみ、テスト系(pytest 等・QtTest)は --dev のときだけ。"""

    def test_default_install_omits_dev_deps(self, os_release, tmp_path):
        proc = run_install(
            "--dry-run",
            "--os-release", os_release("ubuntu"),
            "--bin-dir", str(tmp_path / "bin"),
            "--desktop-dir", str(tmp_path / "apps"),
        )
        assert proc.returncode == 0, proc.stderr
        assert "requirements-dev.txt" not in proc.stdout
        assert "python3-pyside6.qttest" not in proc.stdout
        assert "テスト:" not in proc.stdout

    def test_dev_flag_adds_dev_deps(self, os_release, tmp_path):
        proc = run_install(
            "--dry-run",
            "--dev",
            "--os-release", os_release("ubuntu"),
            "--bin-dir", str(tmp_path / "bin"),
            "--desktop-dir", str(tmp_path / "apps"),
        )
        assert proc.returncode == 0, proc.stderr
        assert "requirements-dev.txt" in proc.stdout
        assert "python3-pyside6.qttest" in proc.stdout
        assert "テスト:" in proc.stdout


class TestUsage:
    def test_help_exits_zero(self):
        proc = run_install("--help")
        assert proc.returncode == 0
        assert "--print-plan" in proc.stdout
        assert "--allow-pip-pyside6" in proc.stdout
        assert "--dev" in proc.stdout

    def test_help_shows_only_the_header_block(self):
        """--help に実装コメントが混ざらないこと。

        ファイル全体から `#` の行を拾う作りだったので、下のほうの実装
        コメントまで出て 78 行になっていた（読めない）。
        """
        proc = run_install("--help")
        assert proc.returncode == 0
        # 説明ブロックの最後の行までで終わっていること
        assert proc.stdout.rstrip().endswith("-h, --help            このヘルプ")
        assert len(proc.stdout.splitlines()) < 40, proc.stdout

    def test_unknown_option_is_rejected(self):
        proc = run_install("--no-such-option")
        assert proc.returncode == 2
        assert "不明な引数" in proc.stderr

    @pytest.mark.skipif(shutil.which("bash") is None, reason="bash が無い")
    def test_script_is_syntactically_valid(self):
        proc = subprocess.run(
            ["bash", "-n", str(INSTALL_SH)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr

    def test_script_is_executable(self):
        assert os.access(INSTALL_SH, os.X_OK)
