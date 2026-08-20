"""IPC（シングルインスタンス化）の「届き方」を縛るテスト。

`test_ipc.py` は「転送できたか」「受け取れたか」までを見ているが、
2026-08-19（39 回目）の変異検査で、その手前にある 3 つの約束が
**壊しても全部緑のまま**だと分かったので、ここで別に縛る。

1. **送りっぱなしにしない**（`disconnectFromServer()`）…
   受け側は「切断されたら 1 件ぶん受け取り終わり」と決めているので、
   送った側が切らないと届かない。`test_ipc.py` は
   ``qtbot.waitUntil()`` で待つ形なので、**関数の中で切らなくても
   後始末（GC）で切れたぶんが間に合ってしまい**、この抜けを見逃す。
2. **パスは UTF-8 で読み直す**… 日本語のファイル名を扱う道具なので、
   ASCII として読むと**開こうとした瞬間に別のファイル名になる**。
3. **前回異常終了の残骸を掃除してから待ち受ける**
   （`QLocalServer.removeServer()`）… 掃除しないと 2 回目以降の起動が
   待ち受けに失敗し、転送は死んだ側へ吸い込まれる。

サーバー名は `conftest.py` の ``_isolated_ipc_server_name`` により
テストごとに一意な名前になっている（本番の ``tuxpad-ipc`` には触れない）。
"""

from __future__ import annotations

import pytest
from PySide6.QtNetwork import QLocalSocket

from editor_app import ipc


@pytest.fixture
def keep_sockets_alive(monkeypatch):
    """転送に使った ``QLocalSocket`` を、関数が返った後も生かしておく。

    ``try_forward_to_running_instance()`` の中で作られるソケットは
    ローカル変数なので、**関数を抜けた時点で参照が消えて後始末される**。
    その後始末そのものが切断を起こすため、関数の中で
    ``disconnectFromServer()`` を呼んでいなくても、少し待てば結果的に
    届いてしまう。**「関数が返った時点でもう届いている」という約束**を
    確かめるには、後始末が起きないようにする必要がある。
    """
    created: list[QLocalSocket] = []

    class RecordingSocket(QLocalSocket):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(ipc, "QLocalSocket", RecordingSocket)
    yield created
    for socket in created:
        socket.abort()
    created.clear()


def test_forward_closes_the_connection_itself(qtbot, keep_sockets_alive) -> None:
    """転送の関数が、自分で接続を切ってから返ること。

    受け側は「切断されたら 1 件ぶん受け取り終わり」と決めている。切るのを
    プロセスの終了まかせにすると、**送った気になったまま何も開かれない**
    経路が残る（終了の仕方によっては切断が届かない）。

    ここでは ``keep_sockets_alive`` でソケットを生かしたまま少し待つ。
    関数の中で切っていれば、この待ちのあいだに受け側の処理まで終わる。
    切っていなければ、いくら待っても届かない。
    """
    server = ipc.InstanceServer()
    received: list[list[str]] = []
    server.paths_received.connect(received.append)

    try:
        assert ipc.try_forward_to_running_instance(["/tmp/a.txt"]) is True
        assert keep_sockets_alive  # 差し替えたソケットが実際に使われた
        # 後始末（GC）による切断は起きない状態にしてあるので、ここで届くのは
        # 関数自身が切ったからに他ならない。
        qtbot.waitUntil(lambda: received == [["/tmp/a.txt"]], timeout=2000)
    finally:
        server.close()


def test_forward_delivers_japanese_paths(qtbot) -> None:
    """日本語のファイル名がそのまま届く（UTF-8 で読み直している）。"""
    server = ipc.InstanceServer()
    received: list[list[str]] = []
    server.paths_received.connect(received.append)

    paths = ["/tmp/日本語のファイル名.txt", "/tmp/請求書（控）.txt"]
    try:
        assert ipc.try_forward_to_running_instance(paths) is True
        qtbot.waitUntil(lambda: len(received) == 1, timeout=2000)
        assert received == [paths]
    finally:
        server.close()


def test_server_takes_over_a_leftover_socket(qtbot) -> None:
    """前回の残骸が残っていても、新しい側が待ち受けを引き取る。

    異常終了（電源断・強制終了）で ``close()`` を通らなかった場合、同じ
    名前のソケットが残る。掃除せずに ``listen()`` すると「使用中」で
    失敗し、**以後この Tuxpad には何も転送されてこない**
    （＝ファイルマネージャーからの「開く」がそのたびに別ウィンドウを
    作る、という元々の不具合に戻る）。
    """
    leftover = ipc.InstanceServer()  # close() を呼ばない＝異常終了の再現
    to_leftover: list[list[str]] = []
    leftover.paths_received.connect(to_leftover.append)

    fresh = ipc.InstanceServer()
    to_fresh: list[list[str]] = []
    fresh.paths_received.connect(to_fresh.append)

    try:
        assert fresh._server.isListening() is True
        assert ipc.try_forward_to_running_instance(["/tmp/a.txt"]) is True
        qtbot.waitUntil(lambda: len(to_fresh) == 1, timeout=2000)
        assert to_fresh == [["/tmp/a.txt"]]
        assert to_leftover == []  # 死んだ側へ吸い込まれていない
    finally:
        fresh.close()
        leftover.close()
