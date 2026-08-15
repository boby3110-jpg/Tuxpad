"""アプリケーションの起動処理."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .fileio import resolve_path
from .ipc import InstanceServer, try_forward_to_running_instance
from .main_window import MainWindow, raise_window
from .settings import (
    IPC_TARGET_ACTIVE,
    IPC_TARGET_LAST,
    load_ipc_target_mode,
    load_theme,
    migrate_legacy_settings,
)
from .theme import apply_theme

# 日本語入力 (fcitx5) を含む IME 連携には、pip 版ではなく OS のパッケージ
# マネージャで入れたシステム版 PySide6（openSUSE では `python313-pyside6`）
# が必要。pip 版は自前の Qt 一式を同梱しており、システムの fcitx5 用 Qt6
# プラグインとプライベート ABI が一致せず読み込めない
# （README・PROGRESS.md 参照）。

#: アイコン画像（複数サイズ）の置き場所。
_RESOURCES_DIR = Path(__file__).resolve().parent / "resources"

#: ウィンドウアイコンに使うサイズ。小さいサイズも用意しておくと、
#: タスクバー・タイトルバー等それぞれで縮小されず綺麗に表示される。
_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def _load_app_icon() -> QIcon:
    icon = QIcon()
    for size in _ICON_SIZES:
        path = _RESOURCES_DIR / f"icon-{size}.png"
        if path.exists():
            icon.addFile(str(path))
    return icon


def create_app(argv: list[str] | None = None) -> QApplication:
    """QApplication を生成する (既に存在すればそれを返す)。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(_load_app_icon())
    # QSettings() を引数無しで使う（設定の永続化、実機フィードバックにより
    # 追加）ために、組織名も設定しておく。保存先は Linux では
    # ~/.config/Tuxpad/Tuxpad.conf になる。
    app.setOrganizationName(APP_NAME)
    # デスクトップへの登録（tuxpad.desktop）と対応付ける。ウィンドウ
    # マネージャがこのアプリをどの .desktop エントリのものかを認識でき、
    # タスクバー等のアイコン・グループ化の一貫性が上がる。
    # （StartupWMClass=tuxpad と合わせてある）
    app.setDesktopFileName("tuxpad")
    return app


def _resolve_file_arguments(args: list[str]) -> list[str]:
    """コマンドライン引数をファイルパスの一覧に変換する。

    Dolphin 等のファイルマネージャーで「Tuxpad で開く」を実行すると、
    ``.desktop`` の ``Exec=... %U`` により、選ばれたファイルが引数として
    渡ってくる（``file://...`` の URI 形式のこともある）。
    """
    paths = []
    for arg in args:
        if arg.startswith("file://"):
            local_path = QUrl(arg).toLocalFile()
            if local_path:
                paths.append(local_path)
        else:
            paths.append(arg)
    return paths


def _handle_forwarded_paths(paths: list[str]) -> None:
    """他プロセスから転送されたファイルパスを、起動中のウィンドウで処理する。

    Dolphin 等で別のファイルをダブルクリックしたときに新しいウィンドウが
    増えていくのではなく、起動中のウィンドウで開く（実機フィードバックに
    より追加）。既に同じファイルを開いているタブがあれば、重複して開かず
    そのタブ・ウィンドウを前面に出すだけにする。

    読み込み先のウィンドウは「最初のウィンドウ」（既定）/「最後の
    ウィンドウ」/「最後にアクティブにしたウィンドウ」から選べる
    （``設定 → 外部から開くファイルの読み込み先``、実機フィードバックに
    より追加）。

    ファイル指定なし（``paths`` が空）で再度起動されたときは、既存
    ウィンドウを前面に出すのではなく、**新しい空のウィンドウを開く**
    （利用者の要望、2026-08-09）。アイコンの再クリックでも新しい
    ウィンドウが増えることになるが、それで良いとの判断。
    """
    if not paths:
        MainWindow.open_new_empty_window(near=MainWindow.most_recently_active_window())
        return

    windows = MainWindow.open_windows()
    if not windows:
        return

    mode = load_ipc_target_mode()
    if mode == IPC_TARGET_LAST:
        target_window = windows[-1]
    elif mode == IPC_TARGET_ACTIVE:
        target_window = MainWindow.most_recently_active_window() or windows[0]
    else:
        target_window = windows[0]

    for raw_path in paths:
        # タブが持つパスと同じ正規化を通す（``fileio.resolve_path``）。
        # 以前はここに同じ処理が書き写してあり、シンボリックリンクの輪で
        # ``Path.resolve()`` が投げる ``RuntimeError`` を拾えていなかった。
        # ここは起動中のプロセスが**受け取ったパスを順に開く**ループなので、
        # 1 つで例外が漏れると残りのファイルも開かれずに終わる。
        resolved = resolve_path(raw_path)

        owner = None
        editor = None
        for win in windows:
            found = win.find_editor_by_path(resolved)
            if found is not None:
                owner, editor = win, found
                break

        if owner is not None:
            owner.tabs.setCurrentWidget(editor)
            editor.setFocus()
            target_window = owner
        else:
            target_window.open_path(raw_path)

    raise_window(target_window)


def main(argv: list[str] | None = None) -> int:
    resolved_argv = list(argv) if argv is not None else sys.argv
    app = create_app(resolved_argv)

    # コマンドラインでファイルが指定されていれば開く（Dolphin 等で
    # 「Tuxpad で開く」を実行したときは、これが無いと常に無題タブで
    # 開いてしまい、指定したファイルを開けなかった。実機フィードバックで
    # 発覚したバグへの対応）。
    file_paths = _resolve_file_arguments(resolved_argv[1:])

    # 既に起動中のインスタンスがあれば、そちらへ転送して自分は何もせず
    # 終了する（シングルインスタンス化。実機フィードバックにより追加）。
    if try_forward_to_running_instance(file_paths):
        return 0

    # 旧名称(Tabula)時代の設定があれば、新名称(Tuxpad)へ引き継ぐ（改名対応、
    # 2026-08-10）。設定を読み込む前に 1 回だけ、プライマリインスタンスで行う。
    migrate_legacy_settings()

    # 前回終了時点のテーマ（ライト/ダーク）を、最初のウィンドウを作る前に
    # 適用しておく（実機フィードバックにより追加）。
    apply_theme(app, load_theme())

    window = MainWindow()
    # app に親付けしておくことで、app の生存期間だけソケットを listen し
    # 続ける（明示的な後始末は不要）。
    server = InstanceServer(app)
    server.paths_received.connect(_handle_forwarded_paths)

    if file_paths:
        window.open_paths(file_paths)

    window.show()

    # 起動時の更新確認（git clone 導入時のみ。設定で OFF にできる）。
    # ウィンドウを出したあとにイベントループへ乗せてから走らせる
    # （確認そのものは別スレッドなので、起動が待たされることはない）。
    QTimer.singleShot(0, window.check_for_updates_on_startup)

    return app.exec()
