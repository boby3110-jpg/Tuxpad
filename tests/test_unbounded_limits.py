"""「値そのものが誰にも縛られていない」上限・待ち時間の見張り（78 回目）。

2026-10-18（77 回目）に :data:`~editor_app.updater.GIT_TIMEOUT_SECONDS` が、
coverage 100%・テスト 39 件の下で**30 秒を 86400 秒に書き換えても全件緑**の
まま通り抜けた。**その行が「通る」ことと、その値が「縛られている」ことは
別である。** 77 回目の申し送りが「時間・件数・長さの上限を決めている定数は、
同じ目で一度見直す価値がある」と残したので、78 回目は ``src/`` の
**モジュール直下の数値定数 37 件を全部洗い出し、1 件ずつ実測で切り分けた**
（やり方と結果の全件は PROGRESS.md の 78 回目の節）。

**テストが定数を「使う」ことは、定数を「縛る」ことではない。** 生き残った
ものの多くは、テスト側が ``REPLACE_PROGRESS_MIN_MATCHES - 1`` のように
**定数から期待値を導いている**ため、値を動かすと期待値も一緒に動いてしまう
（``monkeypatch.setattr`` で差し替えているものも同じで、本番の値は誰も
見ていない）。ここに集めたのは、生き残ったもののうち**値を取り違えると
利用者に実害が出る**ものだけである。

**値そのものは固定しない**（実機で重ければ調整する余地を残す）。
「人が待てる範囲か」「歯止めとして働く範囲か」だけを縛る。
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from editor_app import dialogs, file_commands, file_watch, ipc, search_replace
from editor_app.main_window import MainWindow

# ----------------------------------------------------------------------
# 外部変更に気づいてから「再読み込みしますか？」を出すまでの待ち時間
# ----------------------------------------------------------------------


def test_the_reload_debounce_stays_near_the_one_or_two_seconds_agreed() -> None:
    """``RELOAD_CHECK_DELAY_MS`` が、利用者と決めた「1〜2 秒ほど」の見当に収まること。

    **この待ち時間を伸ばすと、外部変更の通知そのものが黙って死ぬ。**
    しかも死ぬのは inotify の経路だけではない。引き継ぎ ⑭ で足した
    ネットワーク上のファイルの見回り
    (:meth:`~editor_app.file_watch.FileWatchMixin._poll_network_files`) も、
    **新しい確認ダイアログは持たず ``_pending_reload_paths`` と
    ``_reload_check_timer`` へ合流する**作りなので、ここが長いと
    **見回りで気づいた変更も同じだけ待たされる**。

    :data:`~editor_app.file_watch.NETWORK_POLL_INTERVAL_MS` の側には
    「30〜60 秒の範囲か」を見るテストが既にあるため（75 回目）、
    **見回りは縛られているのに合流先は縛られていない**という片側だけの
    状態になっていた。あちらが緑であることが、こちらの安心にはならない。

    短くし過ぎる側にも実害がある。``temp`` へ書いてから ``rename`` する
    保存方式や、何回かに分けて書き込む同期ソフトの途中を捕まえてしまい、
    **書き終わっていないファイルについて訊く**（または同じ保存で何度も
    訊く）。この待ち時間はそれを 1 回にまとめるためのものである。

    2026-10-18（78 回目）に 1200ms → 86400000ms（1 日）の書き換えが
    全 1989 件すべて緑のまま通り抜けたため追加した。
    """
    assert 500 <= file_watch.RELOAD_CHECK_DELAY_MS <= 3_000


def test_the_reload_debounce_timer_uses_the_constant_and_fires_once(
    window: MainWindow,
) -> None:
    """デバウンス用のタイマーが、その定数を実際に使っていること（単発で）。

    上のテストが守っているのは**定数の値**だけなので、タイマーが別の値を
    使うようになったら、定数をいくら縛っても歯止めにならない（上のテストは
    緑のまま、実際の待ち時間だけが外れる）。

    ``setSingleShot(True)`` も一緒に押さえる。繰り返し発火に変わると、
    溜まっていないパスについて :meth:`_process_pending_reloads` が
    1.2 秒ごとに回り続ける（何も溜まっていなければ無害に見えるが、
    「断られた」状態の照合まで回り続ける）。
    """
    assert window._reload_check_timer.interval() == file_watch.RELOAD_CHECK_DELAY_MS
    assert window._reload_check_timer.isSingleShot() is True


def test_each_watch_timer_takes_its_interval_from_its_own_constant() -> None:
    """2 つのタイマーが、それぞれの定数を**名前で**受け取っていること。

    **このテストが別に要る理由は、上のテストを実際に素通りさせてみて
    分かった。** 上のテストは「タイマーの間隔が定数と等しいか」しか見ない
    ので、``setInterval(RELOAD_CHECK_DELAY_MS)`` を
    ``setInterval(1200)``（今と同じ数値の直書き）に替えると**7 件すべて
    緑のまま通る**。それでは「定数を縛れば待ち時間が決まる」という
    つながりが切れていて、以後は定数をいくら直しても実機の動きが変わらない
    （78 回目に実測で確認）。

    見張りの向きが逆にならないよう（``_reload_check_timer`` に
    :data:`NETWORK_POLL_INTERVAL_MS` を渡す、など）、
    **どちらのタイマーにどちらの定数を渡しているか**の対応で突き合わせる。
    対応表そのものを比べるので、呼び出しが消えた「空振りで緑」も防げる。
    """
    tree = ast.parse(
        textwrap.dedent(inspect.getsource(file_watch.FileWatchMixin._setup_file_watching))
    )
    intervals: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "setInterval":
            continue
        intervals[ast.unparse(node.func.value)] = ", ".join(
            ast.unparse(arg) for arg in node.args
        )

    assert intervals == {
        "self._reload_check_timer": "RELOAD_CHECK_DELAY_MS",
        "self._network_poll_timer": "NETWORK_POLL_INTERVAL_MS",
    }


# ----------------------------------------------------------------------
# 2 つ目の起動を 1 つ目へ転送するときの待ち時間（シングルインスタンス化）
# ----------------------------------------------------------------------


def test_the_ipc_connect_timeout_is_long_enough_to_find_a_running_instance() -> None:
    """``_CONNECT_TIMEOUT_MS`` が「取り逃がさず、起動を止めない」範囲にあること。

    **この定数は、転送の 3 つの同期待ち（接続・書き込み・切断）すべての
    唯一の歯止めである。** :func:`~editor_app.ipc.try_forward_to_running_instance`
    は ``timeout`` を引数に取らないので、実際に効くのは常にこの値。

    短くし過ぎると、**起動中のインスタンスを取り逃がす**。``waitForConnected``
    が間に合わなければ関数は ``False`` を返し、呼び出し側 (``app.py``) は
    「誰も起動していない」と判断して**2 つ目のプロセスを立てる**。
    「同じファイルを 2 つのタブで開かせない」見張り（69 回目）は
    **1 つのプロセスの中しか見ない**ので、別プロセスなら同じファイルを
    2 枚開けてしまい、**後から保存した側が相手の編集を上書きする**。
    さらに ``waitForBytesWritten`` が間に合わなかった場合はもっと悪く、
    **関数は ``True`` を返すのにパスは届かない**（Dolphin の「Tuxpad で
    開く」が、エラーも出さずに何も起こらない）。

    長くし過ぎる側は、**窓が 1 枚も出ていない間に効く**のが問題である。
    この転送は ``app.py`` で ``MainWindow()`` を作る**前**に走るので、
    受理はするが読まない相手に当たったとき、**画面に何も出ないまま**
    その時間だけ起動が止まる。

    そこで値そのものは固定せず、範囲だけを縛る。2026-10-18（78 回目）に
    500ms → 1ms と 500ms → 600000ms の**どちらの書き換えも**
    全 1989 件すべて緑のまま通り抜けたため追加した（IPC のテスト 28 件も、
    1ms のまま全部通る——同じマシンの中では 1ms でも間に合ってしまうので、
    実測では捕まらない種類の穴である）。
    """
    assert 100 <= ipc._CONNECT_TIMEOUT_MS <= 5_000


def test_every_synchronous_wait_in_the_forward_uses_that_one_timeout() -> None:
    """転送の 3 つの同期待ちが、どれもその定数を使っていること。

    上のテストは**定数の値**しか見ないので、3 つのうち 1 つでも直接
    数値を書くようになったら、そこだけ歯止めが外れる（上のテストは緑のまま）。

    呼び出しの名前が変わって ``for`` 文が 1 度も回らない「空振りで緑」も
    防ぐため、**見つけた待ち関数の一覧そのもの**を突き合わせる。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(ipc.try_forward_to_running_instance)))
    waited: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not node.func.attr.startswith("waitFor"):
            continue
        waited.append(node.func.attr)
        passed = [ast.unparse(arg) for arg in node.args]
        assert passed == ["_CONNECT_TIMEOUT_MS"], (
            f"{node.func.attr} が {passed} で待っている。"
            "_CONNECT_TIMEOUT_MS を縛っても歯止めにならない"
        )

    assert sorted(waited) == [
        "waitForBytesWritten",
        "waitForConnected",
        "waitForDisconnected",
    ]


# ----------------------------------------------------------------------
# 「重い検索」とみなす境目
# ----------------------------------------------------------------------


def test_the_slow_search_threshold_is_not_tripped_by_every_search() -> None:
    """``SLOW_SEARCH_REFRESH_MS`` が「たまにしか当たらない」長さであること。

    これは「1 文字打つたびに固まる」のを防ぐための安全弁で、検索し直すのに
    これ以上かかったら**本文の編集への追随をやめる**
    (``_auto_refresh_on_edit = False``)。

    短くし過ぎると、**ふつうの検索が毎回「重い」と判定される**。
    数ミリ秒で終わる検索でも降りてしまうので、安全弁ではなく
    **「検索結果が本文の編集に二度と追随しない」という既定の動き**に
    なる。利用者から見れば、本文を直したのに結果一覧が古いまま
    （しかも降りたことは画面のどこにも出ない）。

    長くし過ぎると安全弁そのものが働かず、重い検索で 1 文字ごとに固まる。

    2026-10-18（78 回目）に 300ms → 1ms の書き換えが全 1989 件すべて
    緑のまま通り抜けたため追加した。
    """
    assert 100 <= search_replace.SLOW_SEARCH_REFRESH_MS <= 2_000


# ----------------------------------------------------------------------
# 全置換が「応答なし」に見えないための 2 つの定数（引き継ぎ ⑨）
# ----------------------------------------------------------------------


def test_the_progress_window_still_appears_before_a_replace_gets_slow() -> None:
    """``REPLACE_PROGRESS_MIN_MATCHES`` が「遅くなる前に窓が出る」件数であること。

    **この 2 つの定数は、実機で報告された不具合（引き継ぎ ⑨）への対策
    そのものである。** 2 万件の置換の間ウィンドウが再描画されず「応答なし」に
    見え、やめる手立ても無かった、という報告を受けて入れたもの。

    大きくし過ぎると :meth:`_make_replace_progress` が常に ``None`` を返し、
    **進み具合の窓も「中止」ボタンも二度と出ない**。つまり**対策が入る前の
    状態に黙って戻る**（テストは緑のまま、報告された不具合だけが戻ってくる）。

    小さくし過ぎると、一瞬で終わる数件の置換でも窓がちらつく
    （「窓が一瞬ちらつく方が鬱陶しい」ので境目を設けた、という元の判断を
    裏返してしまう）。

    2026-10-18（78 回目）に 1000 件 → 10000000 件の書き換えが全 1989 件
    すべて緑のまま通り抜けたため追加した。既存の
    `tests/test_replace_all_progress.py` は期待値を
    ``REPLACE_PROGRESS_MIN_MATCHES - 1`` のように**定数から導いている**ので、
    値を動かすと期待値も一緒に動いてしまい、本番の値は誰も見ていなかった。
    """
    assert 50 <= search_replace.REPLACE_PROGRESS_MIN_MATCHES <= 5_000


def test_the_replace_loop_hands_control_back_often_enough_to_repaint() -> None:
    """``REPLACE_BATCH_SIZE`` が「描き直せる間隔」で Qt へ戻る件数であること。

    大きくし過ぎると、進み具合の窓を**出しはするのに一度も描き直さない**
    （「中止」も受け付けない）。窓があるだけに、上の定数を壊したときより
    かえって始末が悪い。小さくし過ぎると、1 件ごとにイベントループへ
    戻るので置換そのものが極端に遅くなる（2 万件で 2 万往復）。

    元の見当は「2 万件で 100 回ほど戻る」。2026-10-18（78 回目）に
    200 件 → 1 件と 200 件 → 1000000 件の**どちらの書き換えも**全 1989 件
    すべて緑のまま通り抜けたため追加した（既存のテストは
    ``monkeypatch.setattr(search_replace, "REPLACE_BATCH_SIZE", 2)`` で
    差し替えてから仕組みを見ているので、本番の値は縛っていない）。
    """
    assert 20 <= search_replace.REPLACE_BATCH_SIZE <= 2_000


def test_a_replace_that_shows_the_progress_window_also_yields_at_least_once() -> None:
    """窓を出す件数が、1 回も Qt へ戻らずに終わり得ないこと。

    片方ずつ範囲に収まっていても、**2 つの大小が逆転すると噛み合わない**。
    ``REPLACE_BATCH_SIZE >= REPLACE_PROGRESS_MIN_MATCHES`` だと、窓を出すと
    決めた件数が**まるごと 1 バッチに収まる**ので、窓は出るのに
    **一度も描き直されないまま**置換が終わる（「中止」を押す隙も無い）。
    どちらの定数も単独では「ありそうな値」のままこの状態を作れるので、
    値の範囲とは別に**関係**を押さえておく。
    """
    assert search_replace.REPLACE_BATCH_SIZE < search_replace.REPLACE_PROGRESS_MIN_MATCHES


# ----------------------------------------------------------------------
# ダイアログ・ステータスバーの「出し過ぎ」を抑える上限
# ----------------------------------------------------------------------


def test_the_diff_line_cap_actually_caps_something_a_person_can_read() -> None:
    """``MAX_DIFF_LINES`` が「画面に収まる量」で止まっていること。

    この定数は**それ自体が歯止めとしてだけ存在する**（保存直前の外部変更を
    見せる差分ダイアログが、画面からはみ出さないようにするためのもの）。
    大きくすると歯止めは黙って無くなり、**別マシンが全面的に書き換えた
    ファイルを保存しようとした瞬間**に、何万行もの差分を組み立てて
    ダイアログに流し込もうとする。**止めたいのがまさにその場面**である
    （保存の確認が返ってこないと、利用者はアプリを強制終了しかねない＝
    未保存の本文が消える）。

    小さくし過ぎる側は既にテストがある
    (`tests/test_dialogs.py::test_build_unified_diff_marks_each_side` が
    2 行では落ちる)。ここで足すのは**上限の側**である。

    2026-10-18（78 回目）に 200 行 → 100000 行の書き換えが全 1989 件
    すべて緑のまま通り抜けたため追加した。
    """
    assert 20 <= dialogs.MAX_DIFF_LINES <= 2_000


def test_the_save_all_status_message_disappears_on_its_own() -> None:
    """``SAVE_ALL_STATUS_TIMEOUT_MS`` が「読めて、やがて消える」長さであること。

    **``0`` は Qt では「消さない」という意味である**
    （``QStatusBar.showMessage(text, 0)`` は ``clearMessage()`` か次の
    ``showMessage()`` まで残る）。つまりこの定数を 0 にすると、
    「3 件のタブを保存しました」が**出たまま居座り**、以降の案内
    （行番号や文字コードの表示）を覆い隠す。**0 は小さい値ではなく、
    意味が反転する値**なので、下限を置いて跨げないようにしておく。

    短すぎる側（1ms など）は、押した直後に目を落としても読めない＝
    「すべて保存」が何件保存したのか分からない。長すぎる側は、編集に
    戻ってからも案内が残り続ける。

    2026-10-18（78 回目）に 5000ms → 1ms の書き換えが全 1989 件すべて
    緑のまま通り抜けたため追加した。
    """
    assert 1_000 <= file_commands.SAVE_ALL_STATUS_TIMEOUT_MS <= 30_000
