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
from PySide6.QtWidgets import QApplication

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


# ----------------------------------------------------------------------
# 一度に大量のパスを転送したとき（84 回目に足した 3 件）
#
# `ipc.py` は 1 行あたりの変異が薄い 3 モジュールの 1 つだった
# （77 回目の申し送り）。受け取り側の「溜め込み」と「後始末」は、
# 壊しても全件緑のまま通り抜けていた。
# ----------------------------------------------------------------------
def test_paths_split_across_two_reads_arrive_whole(qtbot) -> None:
    """何回かに分かれて届いても、**繋ぎ合わせてから**読むこと。

    送られた中身が 1 回の ``readyRead`` で全部読めるとは限らない。別の
    プロセスが書いている間に受け側のイベントループが回るので、緩衝の
    大きさを超えると**途中まで読んだ時点で 1 回**呼ばれる。受け側が
    溜め込み (``extend``) ではなく上書きになっていると、**最後の塊だけ**が
    残る——ファイルマネージャーで大量のファイルを選んで「Tuxpad で開く」
    したときに、**後ろの何個かしか開かない**という形で出る。

    既存のテストはどれも 1〜2 個しか送っておらず、**1 プロセスの中では
    250KiB を一度に書いても 1 回で読み切れてしまう**（84 回目に実測）
    ため、この違いが出なかった。そこで分かれて届く形をここで作る。
    区切りは**マルチバイト文字の途中**（``あ`` の 3 バイトの間）に置く。
    こうしておくと、塊ごとに文字コードを解く作りに変えたときも
    ここで気づける（84 回目に実測。変異 ``ipc-server-overwrites-buffer``）。
    """
    server = ipc.InstanceServer()
    received: list[list[str]] = []
    server.paths_received.connect(received.append)

    payload = "/tmp/前半.txt\n/tmp/後半.txt\n".encode("utf-8")
    split = payload.index("後".encode("utf-8")) + 1  # 「後」の 1 バイト目まで

    socket = QLocalSocket()
    try:
        socket.connectToServer(ipc.SERVER_NAME)
        assert socket.waitForConnected(2000) is True
        socket.write(payload[:split])
        socket.waitForBytesWritten(2000)
        # 受け側へ 1 回目の readyRead を配る（別プロセスならここが
        # 「相手のイベントループが回った」場面に当たる）。
        qtbot.waitUntil(lambda: server._pending_sockets != [], timeout=2000)
        QApplication.processEvents()

        socket.write(payload[split:])
        socket.waitForBytesWritten(2000)
        QApplication.processEvents()

        socket.disconnectFromServer()
        qtbot.waitUntil(lambda: len(received) == 1, timeout=2000)
        assert received == [["/tmp/前半.txt", "/tmp/後半.txt"]]
    finally:
        socket.abort()
        server.close()


def test_delivered_sockets_are_not_kept_forever(qtbot) -> None:
    """受け取り終わったソケットを、控えの一覧に残したままにしないこと。

    ``_pending_sockets`` は「配送の途中だけ」ソケットを握るための控えで、
    配送し終えたら外す。外し忘れても**配送そのものは今までどおり動く**
    ので、全件緑のまま通り抜ける（84 回目に実測。変異
    ``ipc-server-keeps-every-socket``）。

    実害は、**起動したままのプロセスに転送が届くたびに増え続けること**。
    ファイルマネージャーからの「Tuxpad で開く」は 1 回 1 接続なので、
    一日中開いたままの使い方（この道具の普通の使い方）では確実に溜まる。
    """
    server = ipc.InstanceServer()
    received: list[list[str]] = []
    server.paths_received.connect(received.append)

    try:
        for index in range(3):
            assert ipc.try_forward_to_running_instance([f"/tmp/{index}.txt"]) is True
            qtbot.waitUntil(lambda: len(received) == index + 1, timeout=2000)
        assert server._pending_sockets == []
    finally:
        server.close()


def test_delivered_sockets_are_actually_disposed(qtbot) -> None:
    """受け取り終わったソケットを、Qt 側でも捨てること（``deleteLater``）。

    ``nextPendingConnection()`` が返すソケットは**サーバーの子**として
    作られるため、Python 側の控えを外しただけでは消えない。捨て忘れても
    配送は今までどおり動くので、ここも全件緑のまま通り抜けていた
    （84 回目に実測。変異 ``ipc-server-never-disposes-sockets``）。
    上の控えの一覧と**別に**見る必要があるのは、溜まる場所が 2 つある
    ということに他ならない。
    """
    server = ipc.InstanceServer()
    received: list[list[str]] = []
    server.paths_received.connect(received.append)

    def accepted_sockets() -> list[QLocalSocket]:
        return [child for child in server._server.children() if isinstance(child, QLocalSocket)]

    try:
        assert ipc.try_forward_to_running_instance(["/tmp/a.txt"]) is True
        qtbot.waitUntil(lambda: len(received) == 1, timeout=2000)
        # deleteLater() はイベントループに戻ってから効くので、消えるまで待つ。
        qtbot.waitUntil(lambda: accepted_sockets() == [], timeout=2000)
    finally:
        server.close()
