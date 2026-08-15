"""シングルインスタンス化 (IPC) のテスト。

``_isolated_ipc_server_name`` (conftest.py, autouse) により、ここで
使われるサーバー名は毎回一意なテスト専用の名前になる。本番の
``tuxpad-ipc`` には一切触れない。

``QLocalSocket.waitForConnected()`` 等の同期待ち関数は、内部でネストした
イベントループを回す。``qtbot.waitSignal()`` （それ自体もイベントループを
使う）と組み合わせて同じ呼び出しの中でネストさせると、PySide6 のオブジェクト
寿命管理が不安定になり segfault することを確認したため、ここでは使わない。
``try_forward_to_running_instance()`` の呼び出しが返ってきた時点で、その
内部の同期待ちによりサーバー側の処理もすでに完了しているはずなので、
呼び出し後に直接 (必要なら ``qtbot.wait()`` で少し待ってから) 結果を
確認する形にしてある。
"""

from __future__ import annotations

from editor_app import ipc


def test_forward_returns_false_when_no_instance_running() -> None:
    assert ipc.try_forward_to_running_instance(["/tmp/a.txt"]) is False


def test_forward_delivers_paths_to_running_server(qtbot) -> None:
    server = ipc.InstanceServer()
    received: list[list[str]] = []
    server.paths_received.connect(received.append)

    try:
        delivered = ipc.try_forward_to_running_instance(["/tmp/a.txt", "/tmp/b.txt"])
        assert delivered is True

        qtbot.waitUntil(lambda: len(received) == 1, timeout=2000)
        assert received == [["/tmp/a.txt", "/tmp/b.txt"]]
    finally:
        server.close()


def test_forward_with_empty_paths_still_signals_running_instance(qtbot) -> None:
    server = ipc.InstanceServer()
    received: list[list[str]] = []
    server.paths_received.connect(received.append)

    try:
        delivered = ipc.try_forward_to_running_instance([])
        assert delivered is True

        qtbot.waitUntil(lambda: len(received) == 1, timeout=2000)
        assert received == [[]]
    finally:
        server.close()


def test_new_connection_without_a_pending_socket_is_ignored() -> None:
    """待っている接続が無いのに newConnection を受けても、静かに見送る。

    ``QLocalServer.nextPendingConnection()`` は、**接続してきた側が受理
    される前に切れていた**場合などに ``None`` を返す（起動直後の
    Tuxpad へ「開く」を転送しようとして、こちらが受理する前に相手が
    諦めた、といった場合に起こる）。ここで受け止め損ねると
    ``socket.readyRead`` が ``AttributeError`` になり、**newConnection の
    スロットで例外が飛ぶ**＝以後この経路が信用できなくなる
    （2 つ目以降の起動からファイルを渡せない）。
    """
    server = ipc.InstanceServer()
    try:
        server._on_new_connection()  # 例外を出さないこと
        assert server._pending_sockets == []
    finally:
        server.close()


def test_two_servers_with_different_names_do_not_collide(monkeypatch) -> None:
    server_a = ipc.InstanceServer()
    try:
        monkeypatch.setattr(ipc, "SERVER_NAME", ipc.SERVER_NAME + "-other")
        # サーバー名を変えた後は、別のサーバーが無い限り転送できないはず。
        assert ipc.try_forward_to_running_instance(["/tmp/x.txt"]) is False
    finally:
        server_a.close()
