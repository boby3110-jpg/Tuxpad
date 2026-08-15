"""起動処理（``app.create_app`` / ``app.main``）のテスト。

``main()`` は「アプリを起動したときに何が・どの順で起きるか」を決めている
**唯一の場所**なのに、これまでテストが 1 行も通っていなかった
（``coverage`` で ``app.py`` が 69% と最下位だった）。ここが壊れると

- ファイルマネージャーから渡したファイルが開かない（実際に起きた不具合。
  ``app.py`` の ``main()`` のコメント参照）
- 2 回目の起動が転送で済まずウィンドウを増やしてしまう
- 最初のウィンドウだけ前回のテーマが当たらずに出る

といった、**起動した瞬間に目に見える壊れ方**をする。にもかかわらず
既存のテストは ``_resolve_file_arguments`` や ``open_paths`` を個別に
呼ぶだけで、**それらを結線している ``main()`` 自身**は素通りだった。

ヘッドレスで ``main()`` を呼ぶために、``run_main`` フィクスチャで
次の 3 つだけを差し替える（残りは本物を動かす）。

* ``QApplication.exec()`` … これがイベントループに入ってしまうと戻らない
* ``try_forward_to_running_instance()`` … 「他のインスタンスが起動中か」を
  テストから決めるため（本物は実際にソケットへ繋ぎに行く）
* ``check_for_updates_on_startup()`` … 本物は ``git fetch`` を別スレッドで
  始めてしまう
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from editor_app import APP_NAME, __version__
from editor_app import app as app_module
from editor_app.ipc import InstanceServer
from editor_app.main_window import MainWindow


class _MainRun:
    """``run_main`` が返す記録役。"""

    def __init__(self) -> None:
        self.forwarded: list[list[str]] | None = None
        self.exec_called = 0
        self.update_checks = 0
        self.exit_code: int | None = None
        #: apply_theme / migrate_legacy_settings が呼ばれた順と、そのときの
        #: ウィンドウ数（「ウィンドウを作る前か後か」を見るため）。
        self.events: list[tuple[str, int]] = []

    @property
    def window(self) -> MainWindow:
        windows = MainWindow.open_windows()
        assert len(windows) == 1, f"ウィンドウが 1 つではない: {len(windows)}"
        return windows[0]


@pytest.fixture
def run_main(monkeypatch, qtbot):
    """``main()`` をヘッドレスで実行できる形にして呼ぶフィクスチャ。

    ``forwarded=True`` を渡すと「他のインスタンスが起動中だった」ことにできる。
    """
    record = _MainRun()

    def _forward(paths: list[str]) -> bool:
        record.forwarded = list(paths)
        return _forward.result

    _forward.result = False

    def _exec(self) -> int:
        record.exec_called += 1
        return 0

    def _check_updates(self) -> None:
        record.update_checks += 1

    def _apply_theme(app, theme):  # 記録だけして本物は呼ばない
        record.events.append(("apply_theme", len(MainWindow.open_windows())))

    def _migrate() -> None:
        record.events.append(("migrate", len(MainWindow.open_windows())))

    monkeypatch.setattr(app_module, "try_forward_to_running_instance", _forward)
    monkeypatch.setattr(app_module, "apply_theme", _apply_theme)
    monkeypatch.setattr(app_module, "migrate_legacy_settings", _migrate)
    monkeypatch.setattr(QApplication, "exec", _exec)
    monkeypatch.setattr(MainWindow, "check_for_updates_on_startup", _check_updates)

    def factory(argv: list[str], *, forwarded: bool = False) -> _MainRun:
        _forward.result = forwarded
        record.exit_code = app_module.main(argv)
        return record

    yield factory

    # main() が作った InstanceServer は QApplication に親付けされるため、
    # テストが終わってもソケットを listen したまま残る。セッション中ずっと
    # 溜まらないよう明示的に閉じておく（サーバー名はテストごとに一意なので
    # 他のテストと衝突はしないが、file descriptor は消費する）。
    for server in QApplication.instance().findChildren(InstanceServer):
        server.close()
        server.setParent(None)
        server.deleteLater()


# ----------------------------------------------------------------------
# create_app
# ----------------------------------------------------------------------
@pytest.fixture
def restore_app_metadata(qtbot):
    """``create_app`` が QApplication へ書き込む属性を、テスト後に戻す。

    （``qtbot`` は QApplication が存在することを保証するために取っている。）
    """
    app = QApplication.instance()
    before = (
        app.applicationName(),
        app.applicationVersion(),
        app.organizationName(),
        app.desktopFileName(),
    )
    yield app
    app.setApplicationName(before[0])
    app.setApplicationVersion(before[1])
    app.setOrganizationName(before[2])
    app.setDesktopFileName(before[3])


def test_create_app_reuses_the_existing_application(restore_app_metadata) -> None:
    """QApplication は 1 プロセスに 1 つしか作れない（2 つ目は即クラッシュ）。"""
    assert app_module.create_app([]) is restore_app_metadata


def test_create_app_sets_application_metadata(restore_app_metadata) -> None:
    app = app_module.create_app([])

    assert app.applicationName() == APP_NAME
    assert app.applicationVersion() == __version__
    # 組織名は QSettings() の保存先（~/.config/Tuxpad/Tuxpad.conf）を決める。
    assert app.organizationName() == APP_NAME
    # .desktop エントリとの対応付け（StartupWMClass=tuxpad と合わせてある）。
    assert app.desktopFileName() == "tuxpad"


def test_create_app_sets_a_window_icon(restore_app_metadata) -> None:
    app = app_module.create_app([])

    assert not app.windowIcon().isNull()


# ----------------------------------------------------------------------
# main(): 既に起動中のインスタンスがあるとき（転送して自分は終わる）
# ----------------------------------------------------------------------
def test_main_forwards_paths_and_exits_without_opening_a_window(
    run_main, tmp_path: Path
) -> None:
    # 実在するファイルを渡しておく（早期 return が壊れて自分でも開こうと
    # したときに、「ファイルが無い」のエラーダイアログではなく、下の
    # 「ウィンドウが増えていない」で落ちるようにするため）。
    sample = tmp_path / "a.txt"
    sample.write_text("内容", encoding="utf-8")

    record = run_main(["tuxpad", str(sample)], forwarded=True)

    assert record.exit_code == 0
    assert record.forwarded == [str(sample)]
    # ここでウィンドウを作ってしまうと、シングルインスタンス化の意味が無い。
    assert MainWindow.open_windows() == []
    assert record.exec_called == 0


def test_main_forwards_local_paths_not_raw_file_uris(
    run_main, tmp_path: Path
) -> None:
    """Dolphin は ``file://...`` の形で渡してくる（``.desktop`` の ``%U``）。

    転送する前に実パスへ直しておかないと、受け取った側が
    ``file:///home/...`` という名前のファイルを探して失敗する。
    """
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    for sample in (first, second):
        sample.write_text("内容", encoding="utf-8")

    record = run_main(
        ["tuxpad", first.as_uri(), str(second)], forwarded=True
    )

    assert record.forwarded == [str(first), str(second)]


def test_main_does_not_touch_settings_when_forwarding(run_main) -> None:
    """2 つ目のプロセスは、起動中のインスタンスの設定を触らずに消えること。"""
    record = run_main(["tuxpad"], forwarded=True)

    assert record.events == []
    assert record.update_checks == 0


# ----------------------------------------------------------------------
# main(): 自分がプライマリインスタンスのとき
# ----------------------------------------------------------------------
def test_main_opens_files_given_on_the_command_line(run_main, tmp_path: Path) -> None:
    """実機で起きた不具合の再発防止。

    ``.desktop`` の ``Exec=... %U`` で渡した引数を ``main()`` が捨てていて、
    「Tuxpad で開く」が常に空の無題タブになっていた。
    """
    sample = tmp_path / "sample.txt"
    sample.write_text("あいうえお\n", encoding="utf-8")

    record = run_main(["tuxpad", str(sample)])

    editor = record.window.current_editor()
    assert editor.path == sample.resolve()
    assert editor.toPlainText() == "あいうえお\n"


def test_main_opens_file_uri_arguments(run_main, tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")

    record = run_main(["tuxpad", sample.resolve().as_uri()])

    assert record.window.current_editor().path == sample.resolve()


def test_main_does_not_treat_the_program_name_as_a_file(run_main) -> None:
    """``argv[0]`` は実行ファイル名なので、開く対象に混ぜてはいけない。"""
    record = run_main(["/usr/local/bin/tuxpad"])

    editor = record.window.current_editor()
    assert editor.path is None  # 無題タブのまま
    assert record.window.tabs.count() == 1


def test_main_without_arguments_opens_one_empty_window(run_main) -> None:
    record = run_main(["tuxpad"])

    assert record.window.tabs.count() == 1
    assert record.window.current_editor().path is None
    assert record.window.isVisible()


def test_main_applies_the_theme_before_creating_the_first_window(run_main) -> None:
    """テーマは最初のウィンドウを作る**前**に当てること。

    順番が逆になると、起動直後の一瞬だけ前のテーマで描かれてしまう。
    """
    record = run_main(["tuxpad"])

    assert ("apply_theme", 0) in record.events


def test_main_migrates_legacy_settings_before_reading_them(run_main) -> None:
    """旧名称 (Tabula) の設定の引き継ぎは、設定を読むより先に 1 回だけ。"""
    record = run_main(["tuxpad"])

    names = [name for name, _ in record.events]
    assert names == ["migrate", "apply_theme"]


def test_main_returns_the_application_exit_code(run_main, monkeypatch) -> None:
    monkeypatch.setattr(QApplication, "exec", lambda self: 3)

    record = run_main(["tuxpad"])

    assert record.exit_code == 3


def test_main_enters_the_event_loop_once(run_main) -> None:
    record = run_main(["tuxpad"])

    assert record.exec_called == 1


def test_main_defers_the_startup_update_check(run_main, qtbot) -> None:
    """更新確認で起動を待たせないこと（イベントループに乗ってから走る）。"""
    record = run_main(["tuxpad"])

    # main() を抜けた時点ではまだ走っていない。
    assert record.update_checks == 0

    qtbot.wait(50)  # QTimer.singleShot(0, ...) が配送される

    assert record.update_checks == 1


def test_main_opens_paths_forwarded_by_another_process(
    run_main, qtbot, tmp_path: Path
) -> None:
    """起動中のインスタンス側の受け口（InstanceServer → _handle_forwarded_paths）
    が結線されていること。ここが外れると、2 つ目の起動が黙って消える。
    """
    sample = tmp_path / "forwarded.txt"
    sample.write_text("転送された内容", encoding="utf-8")

    record = run_main(["tuxpad"])
    servers = QApplication.instance().findChildren(InstanceServer)
    assert servers, "InstanceServer が作られていない"

    servers[-1].paths_received.emit([str(sample)])

    assert record.window.current_editor().path == sample.resolve()


def test_main_falls_back_to_sys_argv(run_main, monkeypatch, tmp_path: Path) -> None:
    """引数無しで呼ばれたら ``sys.argv`` を使う（``src/main.py`` の呼び方）。"""
    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")
    monkeypatch.setattr(app_module.sys, "argv", ["tuxpad", str(sample)])

    record = run_main(None)

    assert record.window.current_editor().path == sample.resolve()


# ----------------------------------------------------------------------
# _handle_forwarded_paths の端の場合
# ----------------------------------------------------------------------
def test_forwarded_paths_are_ignored_when_no_window_is_open(tmp_path: Path) -> None:
    """ウィンドウが 1 つも無いとき（終了処理の途中など）に落ちないこと。"""
    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")
    assert MainWindow.open_windows() == []

    app_module._handle_forwarded_paths([str(sample)])

    assert MainWindow.open_windows() == []


def test_forwarded_path_opens_even_if_it_cannot_be_resolved(
    window: MainWindow, monkeypatch, tmp_path: Path
) -> None:
    """パスの正規化 (``Path.resolve``) が OS に拒まれても、開く方は続けること。

    ネットワーク越し (NAS / rclone) のパスでは ``resolve()`` が失敗しうる。
    ここで例外が抜けると、転送されたファイルが開かないだけでなく、
    受け取った側のシグナル処理ごと落ちる。
    """

    class _UnresolvablePath:
        """``resolve()`` だけが失敗する、パスのふりをするオブジェクト。"""

        def __init__(self, raw: str) -> None:
            self._raw = str(raw)

        def resolve(self, strict: bool = False) -> Path:
            raise OSError("resolve に失敗")

        def __fspath__(self) -> str:
            return self._raw

        def __str__(self) -> str:
            return self._raw

    monkeypatch.setattr(app_module, "Path", _UnresolvablePath)

    sample = tmp_path / "sample.txt"
    sample.write_text("内容", encoding="utf-8")

    app_module._handle_forwarded_paths([str(sample)])

    assert window.current_editor().path == sample.resolve()
