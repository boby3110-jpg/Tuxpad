"""シングルインスタンス化のための IPC.

Dolphin 等のファイルマネージャーで複数のファイルを別々に「Tuxpad で
開く」すると、そのたびに新しいプロセス（＝新しいウィンドウ）が起動して
しまっていた。``QLocalServer``/``QLocalSocket`` を使い、既に起動中の
インスタンスがあればそちらへファイルパスを転送して自分自身は何もせず
終了し、起動中のインスタンス側は受け取ったパスを既存のウィンドウで開く
（既に開いているファイルなら、そのタブ・ウィンドウを前面に出すだけに
する）。

``SERVER_NAME`` はモジュール属性として参照する（関数内で直接名前を
束縛せず、呼び出し時にモジュールから読む）ことで、テストが
``monkeypatch`` で差し替えられるようにしてある。テストで本番と同じ
名前を使うと、このマシンで実際に起動中の Tuxpad に誤ってテスト用の
一時ファイルパスを転送してしまう恐れがあるため、テストでは必ず
テスト専用の一意な名前に差し替えること（``tests/conftest.py`` の
``_isolated_ipc_server_name`` 参照）。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

#: ローカルソケットのサーバー名。テストでは必ず一意な名前に差し替えること。
SERVER_NAME = "tuxpad-ipc"

_CONNECT_TIMEOUT_MS = 500
_ENCODING = "utf-8"


def try_forward_to_running_instance(paths: list[str]) -> bool:
    """既に起動中のインスタンスがあれば、そちらへ paths を転送する。

    転送できた（＝他のインスタンスが起動中だった）場合は True を返す。
    その場合、呼び出し側は自分では何もせず終了してよい。
    """
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(_CONNECT_TIMEOUT_MS):
        socket.abort()
        return False

    payload = "\n".join(paths).encode(_ENCODING)
    socket.write(payload)
    socket.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
    socket.disconnectFromServer()
    socket.waitForDisconnected(_CONNECT_TIMEOUT_MS)
    return True


class InstanceServer(QObject):
    """他プロセスから転送されたファイルパスを受け取るサーバー."""

    paths_received = Signal(list)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # 前回異常終了時のソケットの残骸があれば掃除してから listen する。
        QLocalServer.removeServer(SERVER_NAME)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._server.listen(SERVER_NAME)
        # 受け入れた接続ソケットを明示的に保持しておく（PySide6 では、
        # クロージャ経由の参照だけに頼ると GC・シグナル配送のタイミング
        # 次第でオブジェクトの寿命管理が不安定になることがあるため）。
        self._pending_sockets: list[QLocalSocket] = []

    def close(self) -> None:
        self._server.close()

    def _on_new_connection(self) -> None:
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        self._pending_sockets.append(socket)

        buffer = bytearray()

        def on_ready_read() -> None:
            buffer.extend(bytes(socket.readAll().data()))

        def on_disconnected() -> None:
            text = bytes(buffer).decode(_ENCODING)
            paths = [line for line in text.split("\n") if line]
            if socket in self._pending_sockets:
                self._pending_sockets.remove(socket)
            self.paths_received.emit(paths)
            socket.deleteLater()

        socket.readyRead.connect(on_ready_read)
        socket.disconnected.connect(on_disconnected)
