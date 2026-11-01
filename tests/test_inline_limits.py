"""メソッドの中に直書きされた数値が、誰にも縛られていない穴（79 回目）。

78 回目は ``ast`` で**モジュール直下の代入**だけを数え上げたので、
``digits = field[index + 1 : index + 4]`` や ``window.move(near.x() + 40, ...)``
のように**関数の中に直書きされた数値**は 1 件も見ていなかった。
ここはその続きで、同じ目で 56 件を数え上げ、1 件ずつ壊して
「テストが赤くなるか」を実測した結果から、**実害が出るものだけ**を縛る。

見栄えの余白や色の濃さは**意図的に縛っていない**（78 回目の申し送りどおり。
落ちたときに「どこが悪いのか」を説明できないテストは、正しい変更の
邪魔にしかならない）。詳しくは PROGRESS.md の「79 回目」の節。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtWidgets import QApplication

from editor_app import backups, file_watch
from editor_app.main_window import MainWindow


# ----------------------------------------------------------------------
# /proc/mounts の読み取り（引き継ぎ ⑭ の土台）
#
# ここが黙って壊れると、NAS のファイルが「見回り」の対象から外れ、
# 別のマシンの書き換えに気づけなくなる（2026-10-19 に実機で起きた形。
# あのときは autofs の行を先に採っていたのが原因だった）。
# ----------------------------------------------------------------------
def test_mount_line_with_only_two_columns_does_not_break_the_whole_table(
    tmp_path: Path,
) -> None:
    """列が 2 つしか無い行があっても、表を読み切れること。

    列数の下限（``len(fields) < 3``）は「1 行壊れていても判定全体を
    失わない」ための歯止めだが、**既存のテストが使っていた壊れた行は
    1 列**（``"壊れた行"``）だったため、**下限を 2 に書き換えても
    全件緑のまま通った**（79 回目に実測）。下限が 2 になると、2 列の行で
    ``fields[2]`` が ``IndexError`` になり、``read_mount_table`` は
    ``OSError`` しか捕まえていないので**例外がそのまま外へ出る**。
    そうなると見回りそのものが毎回失敗し、**NAS の外部変更に永久に
    気づけなくなる**。

    ⑭ の教訓どおり「``/proc/mounts`` のような OS が与える表は、
    作り物では形が違うことがある」ので、**列が足りない形は実際に
    流して確かめておく**。
    """
    path = tmp_path / "mounts"
    path.write_text(
        "/dev/sdb1 /mnt/中途半端\n//nas/share /mnt/nas cifs rw 0 0\n",
        encoding="utf-8",
    )

    assert file_watch.read_mount_table(path) == (("/mnt/nas", True),)


def test_incomplete_octal_escape_is_left_as_written(tmp_path: Path) -> None:
    """3 桁に足りない ``\\`` は、エスケープとして解釈しないこと。

    カーネルが書くのは ``\\040`` のように**必ず 3 桁**で、``\\04`` のような
    形は無い。桁数の条件（``len(digits) == 3``）を ``>= 2`` に緩めても
    **全件緑のまま通った**（79 回目に実測）ので、ここを縛る。

    緩めると、末尾が ``\\04`` で終わるマウント点から**実在しない制御文字**
    （``chr(4)``）を作ってしまう。マウント点の文字列が実機と 1 文字でも
    違えば ``path_is_on_network_mount`` の前方一致が外れ、**NAS の
    ファイルがローカル扱いになって見回りから静かに落ちる**
    ——10/19 に実機で起きたのと同じ壊れ方になる。
    """
    assert file_watch._unescape_mount_field("/mnt/share\\04") == "/mnt/share\\04"
    # 正しい 3 桁は、今までどおり元に戻す（上の条件だけを狭く見る）。
    assert file_watch._unescape_mount_field("/mnt/my\\040disk") == "/mnt/my disk"


# ----------------------------------------------------------------------
# ファイル指定なしで 2 つ目を起動したときの、新しいウィンドウの位置
# ----------------------------------------------------------------------
def test_open_new_empty_window_puts_it_below_right_of_the_window_it_is_near(
    window: MainWindow,
) -> None:
    """新しい空のウィンドウは、元のウィンドウの**右下**に出ること。

    73 回目に、タブを切り離した窓の向き（``_on_tab_double_clicked`` /
    ``_tear_off_via_menu``）は縛った（``test_multi_window.py``）。
    ところが**同じ計算をしている 3 か所目**——アイコンの再クリックや
    素の ``tuxpad`` で 2 つ目を起動したときに開く空のウィンドウ
    （``open_new_empty_window``）——は、**向きを逆にしても全件緑**だった
    （79 回目に実測）。

    ``__init__`` は前回終了時の位置を復元するので、ずらさないと既存の
    ウィンドウにぴったり重なり、利用者からは**「アイコンを押しても
    何も起きない」**ようにしか見えない（これを避けるために ``near`` が
    ある）。左上へずらす向きに変わると、元のウィンドウが画面の左上際に
    あるときに**画面の外**へ出る。

    固定するのは**ずらす向きだけ**で、何 px かは縛らない
    （73 回目の 2 件と同じ方針）。
    """
    window.move(200, 150)

    new_window = MainWindow.open_new_empty_window(near=window)

    assert new_window is not window
    assert new_window.pos().x() > window.pos().x()
    assert new_window.pos().y() > window.pos().y()


# ----------------------------------------------------------------------
# 上書き前の控えの置き場（引き継ぎ ⑤⑦ の安全網）
# ----------------------------------------------------------------------
def test_backup_bucket_keeps_sixteen_hex_digits_of_the_path_digest() -> None:
    """控えの置き場の名前は、パスの SHA-1 の**先頭 16 桁**であること。

    これは**保存形式**なので、桁数を変えると**既に取ってある控えが
    別のディレクトリを指すことになり、「復元」の一覧から静かに消える**
    （``list_backups`` は ``_bucket()`` が返す 1 つのディレクトリしか
    見ない）。文字コードの誤判定からファイルを取り戻すための最後の
    安全網（引き継ぎ ⑤⑦）が、**利用者には何の知らせも無く無くなる**。

    79 回目に ``digest[:16]`` を ``digest[:4]`` に書き換えて実測したところ、
    **全件緑のまま通った**。既存の変異 ``bk-bucket-shared-for-all`` は
    「置き場で分けること」自体は縛っているが、**桁数は見ていない**。

    桁数を変えるときは、このテストを消すのではなく
    **古い置き場からの引っ越しを一緒に書くこと**。
    """
    path = Path("/home/someone/仕事/メモ.txt")
    expected = hashlib.sha1(str(path).encode("utf-8", "surrogateescape")).hexdigest()[:16]

    bucket = backups._bucket(path)

    assert bucket.name == expected
    assert len(bucket.name) == 16
    assert bucket.parent == backups.backup_root()


# ----------------------------------------------------------------------
# 検索欄・置換欄の高さ（実機フィードバックで「狭くて打ちにくい」と
# 言われて足した設定。表示行数は利用者が 1〜30 から選ぶ）
# ----------------------------------------------------------------------
def _usable_text_height(box) -> float:
    """その欄で本文を描ける高さ（枠と内部の余白を除いたぶん）。"""
    return box.viewport().height() - 2 * box.document().documentMargin()


def test_search_and_replace_boxes_can_show_every_line_the_user_asked_for(
    window: MainWindow,
) -> None:
    """指定した表示行数が、**実際に全部見える**高さになっていること。

    ``_set_input_height`` は「行の高さ × 行数」に ``QPlainTextEdit`` が
    内部で取る余白（``documentMargin`` の上下 4px ＝ 8px）を足している。
    この ``+ 8`` を落としても**全件緑のまま通った**（79 回目に実測）。

    落ちると実測で次のようになる（4 行指定・``lineSpacing`` 15px のとき）。
    本文を描ける高さが **60px → 52px** になり、**4 行目が入らない**ので
    欄が勝手にスクロールする（打っている行を見せるために 1 行目が上へ
    隠れる）。利用者が「6 行で」と指定しても見えるのは 5 行弱で、
    **「検索欄が狭くて打ちにくい」という元の実機フィードバックへ
    そのまま戻る**。

    縛るのは「指定した行数が入るか」だけで、px は縛らない（余白の実際の
    値はテーマやフォントで変わってよい数字）。
    """
    panel = window.search_panel
    lines = 4
    # 隠れているうちは Qt が中身の配置をしないので、実際に開いた状態で測る
    # （利用者が Ctrl+F で開いたときと同じ状態）。
    panel.show()
    QApplication.processEvents()

    panel.set_input_visible_lines(lines)
    QApplication.processEvents()

    for box, name in ((panel._input, "検索欄"), (panel._replace_input, "置換欄")):
        needed = box.fontMetrics().lineSpacing() * lines
        assert _usable_text_height(box) >= needed, (
            f"{name}に {lines} 行が入らない"
            f"（描ける高さ {_usable_text_height(box)}px < 必要な {needed}px）"
        )
