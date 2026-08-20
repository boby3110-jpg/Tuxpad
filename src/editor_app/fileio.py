"""ファイルの読み書き (文字コード・改行コードの扱い).

このモジュールだけがバイト列と文字列の変換を担当する。呼び出し側
(MainWindow) はここの戻り値だけを見ればよいようにしてある。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unicodedata
from pathlib import Path
from typing import NamedTuple

from charset_normalizer import from_bytes

#: BOM とそれに対応する Python のコーデック名。長い BOM から順に判定する。
_BOM_TABLE: tuple[tuple[bytes, str], ...] = (
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xfe\xff", "utf-16-be"),
    (b"\xff\xfe", "utf-16-le"),
)

#: 「BOM 付きだが、コーデック自身は BOM を処理してくれない」文字コード。
#: utf-8-sig と違い utf-16-le などは decode しても BOM が U+FEFF として本文に残り、
#: encode しても BOM が付かない。読み込み時はここで剥がし、
#: 保存時は同じ表を使って BOM を書き戻す (:func:`encode_text`)。
BOM_BY_ENCODING: dict[str, bytes] = {
    "utf-32-be": b"\x00\x00\xfe\xff",
    "utf-32-le": b"\xff\xfe\x00\x00",
    "utf-16-be": b"\xfe\xff",
    "utf-16-le": b"\xff\xfe",
}

#: BOM が無く UTF-8 でも読めない場合に、この順で「厳密デコードが通るか」を
#: 試す (charset-normalizer より先に試す)。日本語の実際のテキストファイルで
#: 実際に出会うのはほぼ Shift-JIS (cp932) で、稀に EUC-JP もありうる。
#: これらの日本語レガシー文字コードは互いに無効なバイト列を厳密に弾いてくれる
#: ため、charset-normalizer の統計的な推定より確実に判定できる
#: （実測で、charset-normalizer 単体だと漢字の多い日本語文を中国語系の
#: 文字コード (gb18030 など) に誤判定することがあったため、この方式にした）。
#:
#: **ISO-2022-JP はここに置けない**（置いても絶対に届かない）。7bit の
#: エスケープ列なので「UTF-8 として読めてしまう」＝ 2 段目で決着してしまい、
#: 逆に UTF-8 として読めないバイト列 (0x80 以上を含む) は ISO-2022-JP としても
#: 読めないため。UTF-8 より**先**に :data:`_ISO2022_JP_ESCAPE` で見分ける。
#:
#: **並び順は「引き分けたときにどちらを採るか」だけの意味**しか持たない。
#: cp932 と euc_jp は**どちらでも読めてしまうバイト列がある**ので、先に試した
#: 方で決めると誤読する（:func:`_pick_japanese_legacy_detail` を参照）。ここで
#: cp932 を先にしてあるのは、引き分けたときは Shift-JIS の方がはるかに
#: 多いから。
_STRICT_JAPANESE_ENCODINGS: tuple[str, ...] = ("cp932", "euc_jp")

#: ISO-2022-JP が「ここから 2 バイト文字」に切り替えるエスケープ列の先頭 2 バイト
#: (``ESC $ @`` = JIS C 6226-1978 / ``ESC $ B`` = JIS X 0208-1983)。
#:
#: ISO-2022-JP のファイルは全てのバイトが 0x80 未満なので、**UTF-8 としても
#: 必ず「読めて」しまう**。素直に「UTF-8 で読めたら UTF-8」と判定すると、
#: JIS のファイルを開いた利用者には ``\x1b$BF|K\8l`` がそのまま並んで見える
#: （エラーにはならないので、化けていることにしか気づけない）。しかも
#: このアプリ自身が「文字コードを指定して保存」で JIS を選ばせるため、
#: **自分で保存したファイルを自分で開けない**ことになる。
#: 逆に、この 2 バイトが 1 つも無い ISO-2022-JP のファイルは中身が ASCII だけ
#: なので、UTF-8 として読んでも結果は 1 文字も変わらない (見分ける必要が無い)。
_ISO2022_JP_ESCAPE = b"\x1b$"

#: 日本語のレガシー文字コード。**判定を間違えるとファイルが壊れうるのは
#: この 3 つだけ**——互いに「どちらでも読めてしまう」バイト列を持ち、
#: 化けたまま保存すると元に戻せない。UTF-8 系は「読めたならほぼ確実に
#: それ」なので、この危険は事実上無い。
#:
#: 「文字化けしたまま保存していないか」の警告
#: (:func:`find_suspicious_text`) と、上書き前の控え
#: (:mod:`editor_app.backups`) は、どちらもこの範囲だけを対象にしている。
#: **絞り込みの範囲を変えるときは、この 1 か所で変えること**（片方だけ
#: 広げると「警告は出るのに控えは無い」ような食い違いが生まれる）。
JAPANESE_LEGACY_ENCODINGS: tuple[str, ...] = (*_STRICT_JAPANESE_ENCODINGS, "iso2022_jp")

DEFAULT_ENCODING = "utf-8"
DEFAULT_NEWLINE = "\n"

#: 利用者に見せるときの文字コードの呼び名。コーデック名 (``cp932``) だけだと
#: 「Shift-JIS のことだ」と分からないため、エラーメッセージではこちらを使う。
ENCODING_DISPLAY_NAMES: dict[str, str] = {
    "utf-8": "UTF-8",
    "utf-8-sig": "UTF-8 (BOM 付き)",
    "cp932": "Shift-JIS (cp932)",
    "euc_jp": "EUC-JP",
    "iso2022_jp": "JIS (ISO-2022-JP)",
    "utf-16-le": "UTF-16 (LE)",
    "utf-16-be": "UTF-16 (BE)",
    "utf-32-le": "UTF-32 (LE)",
    "utf-32-be": "UTF-32 (BE)",
}

#: 「文字コードを指定して保存」で利用者に選ばせる文字コード (この順に並べる)。
#: 読み込み時に判定できるものと同じ顔ぶれにしてある
#: (:data:`ENCODING_DISPLAY_NAMES` の並び順そのまま = UTF-8 系 → 日本語の
#: レガシー → UTF-16/32)。判定は charset-normalizer 経由でこれ以外の
#: コーデック名になることもあるので、**今のタブの文字コードがこの一覧に
#: 無い場合もある**。呼び出し側 (:func:`encoding_choices`) がそれを足す。
SELECTABLE_ENCODINGS: tuple[str, ...] = tuple(ENCODING_DISPLAY_NAMES)

#: :func:`find_unencodable` が一度に探す該当箇所の上限。全部数えようとすると
#: 「文字コードが根本的に合っていない」場合に何万件も走査することになるため、
#: 先頭から数件見つけた時点で切り上げる (利用者が直すのに必要なのは
#: 「どこから直せばよいか」であって、正確な総数ではない)。
MAX_UNENCODABLE_SPOTS = 20

#: :func:`find_suspicious_text` が一度に挙げる該当箇所の上限。
#: :data:`MAX_UNENCODABLE_SPOTS` と同じ考え方（利用者に要るのは
#: 「どこを見ればよいか」であって正確な総数ではない）。
MAX_SUSPICIOUS_SPOTS = 20

#: 半角カナと半角の句読点の範囲 (U+FF61 ｡ 〜 U+FF9F ﾟ)。
_HALFWIDTH_BLOCK = range(0xFF61, 0xFFA0)

#: そのうち句読点・かぎ括弧・中点 (``｡｢｣､･``)。EUC-JP のひらがな・カタカナの
#: 1 バイト目 (0xA4 / 0xA5) が cp932 ではここに落ちるため、**誤読の跡として
#: 一番よく出る**文字群（:func:`_japanese_plausibility` の「誤読の跡」を参照）。
_HALFWIDTH_PUNCTUATION = range(0xFF61, 0xFF66)

#: 全角の日本語とみなす範囲。:func:`_japanese_plausibility` と
#: :func:`find_suspicious_text` が**同じ物差しで数えるように**共有する。
_FULLWIDTH_JAPANESE_RANGES: tuple[tuple[int, int], ...] = (
    (0x3000, 0x30FF),   # 全角記号・ひらがな・カタカナ
    (0x3400, 0x4DBF),   # 漢字（拡張 A）
    (0x4E00, 0x9FFF),   # 漢字
    (0xFF01, 0xFF60),   # 全角英数字・記号
)

#: :func:`find_suspicious_text` が返す「怪しい」理由。
SUSPICIOUS_CONTROL = "control"
SUSPICIOUS_MIXED_HALFWIDTH = "mixed_halfwidth"
SUSPICIOUS_HALFWIDTH_PUNCTUATION = "halfwidth_punctuation"

#: 本文にあってよい制御文字。改ページ (``\f``) を通すのは、
#: **区切りとして実際に使われている**ため（Emacs 系のファイル等）。
#: 誤読の跡として出るのは ESC であって改ページではないので、除いても
#: 既知の不具合の取りこぼしにはならない。
_ALLOWED_CONTROL_CHARACTERS = frozenset("\t\n\r\f")

#: 制御文字をダイアログで目に見える名前にするための表
#: (:func:`describe_text_spot`)。ここに無いものは ``<0x01>`` の形で出す。
_CONTROL_CHARACTER_NAMES: dict[int, str] = {
    0x00: "NUL", 0x07: "BEL", 0x08: "BS", 0x0B: "VT",
    0x0E: "SO", 0x0F: "SI", 0x1A: "SUB", 0x1B: "ESC",
}

#: 制御文字の該当箇所に、後ろ何文字までを一緒に見せるか。``<ESC>$B`` の形で
#: 見えると、JIS のファイルを UTF-8 と誤判定した跡だと一目で分かる。
_CONTROL_SPOT_CONTEXT = 5

#: 「誤読した日本語」の判定を行う文字コード。既知の実バグ
#: （EUC-JP のファイルを cp932 として読んでしまう）に基づく 2 つだけに絞る。
_MOJIBAKE_PRONE_ENCODINGS: tuple[str, ...] = _STRICT_JAPANESE_ENCODINGS

#: 「全角の日本語と半角カナが無秩序に混ざっている」と見なす、細かい交互の回数。
#: 半角カナの塊どうしが**全角 1 文字を挟んだだけで隣り合っている**箇所を数え、
#: これ以上あれば誤読とみなす（:func:`find_suspicious_text` の測定結果を参照）。
_MIN_TIGHT_ALTERNATIONS = 2

#: 「半角の句読点が規則的に並ぶ」と見なす個数と割合（半角カナ・半角句読点の
#: 並び全体に占める句読点の割合）。
_MIN_HALFWIDTH_PUNCTUATION = 3
_MIN_HALFWIDTH_PUNCTUATION_RATIO = 0.3


def resolve_path(path: Path | str) -> Path:
    """パスを絶対パスに正規化する (できなければ渡されたまま返す)。

    タブが持つ ``EditorWidget.path`` は必ずこれを通した形にしておくこと。
    「同じファイルを開いているタブを探す」(``MainWindow.find_editor_by_path``)
    は単純なパスの比較なので、片方だけ相対パスやシンボリックリンクのままだと
    同じファイルを別物と判定してしまい、同じファイルのタブが 2 つできる。

    ``RuntimeError`` も握るのは、**シンボリックリンクが輪になっている**とき
    ``Path.resolve()`` が Python 3.11 / 3.12 では ``OSError`` ではなく
    ``RuntimeError("Symlink loop from ...")`` を投げるため
    （3.13 以降は投げずに諦めた結果を返す）。ここで漏らすと、輪になった
    リンクを開こうとしただけで ``open_path`` の**外**まで例外が飛ぶ
    （読み込みの失敗と違い「開けません」のダイアログにもならない）。
    解決できなかったときは渡されたパスをそのまま返し、開けるかどうかの
    判断は実際に読みに行く側へ任せる。
    """
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError):
        return Path(path)


def file_signature(path: Path | str | None) -> tuple[int, int] | None:
    """ファイルの「見分け札」= ``(更新日時 ns, サイズ)`` を返す。無ければ None。

    「読み込んだ後に、外部で書き換わっていないか」を判定するためのもの
    (:meth:`~editor_app.file_commands.FileCommandsMixin._check_external_change`)。
    ファイルが無い・``stat`` できない場合は None を返し、呼び出し側は
    「判断できない」として扱う。

    更新日時に ``st_mtime`` (float 秒) ではなく ``st_mtime_ns`` (整数) を
    使うのは、float だと丸めのせいで「同じはずの値が一致しない」ことが
    起こりうるため。サイズも併せて見るのは、**ネットワーク越し
    (NAS/rclone) のファイルシステムでは更新日時の分解能が 1 秒しか
    無いことがある**ため（1 秒以内に書き換わると更新日時だけでは
    気づけないが、大きさが変わっていれば気づける）。それでも
    「1 秒以内に、同じ大きさのまま書き換わった」場合はすり抜ける
    ——この方式の限界であり、監視 (:mod:`editor_app.file_watch`) の方が
    届く環境ではそちらが拾う。
    """
    if path is None:
        return None
    try:
        info = Path(path).stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


def describe_encoding(encoding: str) -> str:
    """利用者に見せる文字コードの呼び名 (未知のものはコーデック名のまま)。"""
    return ENCODING_DISPLAY_NAMES.get(encoding, encoding)


#: 「改行しない空白」(NBSP)。**ウェブや Word から貼り付けた日本語の文書に
#: よく紛れ込む**が、cp932 / euc_jp / iso2022_jp には対応する文字が無い。
NO_BREAK_SPACE = "\u00a0"

#: :func:`downgrade_no_break_spaces` が NBSP の代わりに書き出す文字。
NBSP_FALLBACK = " "


def _encoding_keeps_no_break_space(encoding: str) -> bool:
    """``encoding`` が NBSP をそのまま表せるか。

    知らないコーデック名 (``LookupError``) は **True** を返す
    ——「表せない」と決めつけて本文を書き換えるより、何も触らずに
    :func:`encode_text` の同じ ``LookupError`` で止まる方が安全。
    """
    try:
        NO_BREAK_SPACE.encode(encoding)
    except UnicodeEncodeError:
        return False
    except LookupError:
        return True
    return True


def downgrade_no_break_spaces(text: str, encoding: str) -> tuple[str, list[int]]:
    """``encoding`` で表せない NBSP を、普通の半角空白へ黙って落とす。

    ``(落とした後の本文, 落とした位置の一覧)`` を返す。位置は ``text`` の
    Python の文字位置（NBSP も半角空白も 1 文字なので、**落としても
    本文中の位置は 1 つもずれない**）。

    **利用者の指示による特例**（2026-08-18 回答）。27 回目に
    :func:`~editor_app.editor.document_text` で NBSP を残すようにした結果、
    **Shift-JIS のファイルに web や Word から貼り付けると「保存できません」で
    止まる**ようになった。止める側に倒したのは「黙って本文が変わる」のを
    避けるためだったが、実際の作業では貼り付けが多く、いちいち止まる方が
    困るとの回答があった。そこで折衷案として、**NBSP をそもそも表せない
    文字コード（cp932 / euc_jp / iso2022_jp）で保存するときだけ**
    普通の半角空白に落とす。

    - **UTF-8 系・UTF-16/32 では今までどおり NBSP をそのまま残す**
      （表せるので、この関数は何もしない）。
    - **特例は NBSP だけ**。同じく表せない ♡ や絵文字は従来どおり
      :class:`EncodingError` で止めて利用者に知らせる。

    判定を「文字コードの一覧」ではなく **実際に encode してみる**形に
    しているのは、文字コードの判定が charset-normalizer に回って
    ``gb18030`` のような一覧に無いコーデック名になることがあるため
    （一覧で持つと、そこだけ特例が抜ける）。
    """
    if NO_BREAK_SPACE not in text or _encoding_keeps_no_break_space(encoding):
        return text, []
    positions = [index for index, char in enumerate(text) if char == NO_BREAK_SPACE]
    return text.replace(NO_BREAK_SPACE, NBSP_FALLBACK), positions


#: **画面では普通の空白や何も無い場所に見えてしまう文字**の呼び名。
#: 「保存できない文字がここにあります」と並べるときに、その文字をそのまま
#: 出しても空白にしか見えず、利用者は何を直せばよいのか分からない。
#: ウェブや Word から貼り付けた日本語の文書に紛れ込みやすいものを挙げてある。
INVISIBLE_CHAR_NAMES = {
    "\u00a0": "改行しない空白",
    "\u00ad": "折り返しのときだけ現れるハイフン",
    "\u2007": "数字と同じ幅の空白",
    "\u200b": "幅の無い空白",
    "\u200c": "幅の無いつなげない印",
    "\u200d": "幅の無いつなげる印",
    "\u200e": "左から右へ書く印",
    "\u200f": "右から左へ書く印",
    "\u2028": "行区切り",
    "\u2029": "段落区切り",
    "\u202f": "改行しない狭い空白",
    "\u2060": "幅の無い区切らない印",
    "\ufeff": "幅の無い改行しない空白 (BOM と同じ文字)",
}


#: :func:`describe_char` で「目に見えない」とみなす Unicode の分類。
#: Cc=制御文字, Cf=書式文字, Zs=空白, Zl=行区切り, Zp=段落区切り, Cn=未割り当て。
#: **普通の半角空白もここ (Zs) に入るが**、全ての文字コードで保存できるので
#: この判定に渡ってくることが無い。
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Zs", "Zl", "Zp", "Cn"})


def describe_char(char: str) -> str:
    """ダイアログに並べるための 1 文字の説明。

    普通の文字はそのまま返す（``♡`` は「♡」と出せばよい）。
    **目に見えない文字だけ**は、呼び名と ``U+XXXX`` を付けて返す
    （:data:`INVISIBLE_CHAR_NAMES`）。そうしないと
    「1 行目:「 」」のように**空白にしか見えない案内**になり、
    どこを直せばよいのか分からなくなる。

    一覧に無い文字でも、印字できない種類 (:data:`_INVISIBLE_CATEGORIES`)
    なら Unicode の名前と ``U+XXXX`` を添える。
    """
    name = INVISIBLE_CHAR_NAMES.get(char)
    if name is None:
        if len(char) != 1 or unicodedata.category(char) not in _INVISIBLE_CATEGORIES:
            return char
        name = unicodedata.name(char, "名前の無い文字")
    return f"{name} (U+{ord(char):04X})"


def encoding_choices(current: str) -> list[str]:
    """「文字コードを指定して保存」の選択肢 (コーデック名) を返す。

    :data:`SELECTABLE_ENCODINGS` そのままだが、``current`` がそこに無い
    場合だけ先頭に足す。文字コードの判定は charset-normalizer に回ることが
    あり、そのときは ``gb18030`` のような一覧に無いコーデック名になる。
    足さないと「今の文字コードを初期選択にする」ができず、選び直さない限り
    元の文字コードで保存できなくなる (＝ダイアログを開いただけで
    保存内容が変わりうる) ため。
    """
    if current in SELECTABLE_ENCODINGS:
        return list(SELECTABLE_ENCODINGS)
    return [current, *SELECTABLE_ENCODINGS]


def find_unencodable(
    text: str, encoding: str, limit: int = MAX_UNENCODABLE_SPOTS
) -> tuple[list[tuple[int, str]], bool]:
    """``text`` のうち ``encoding`` で表現できない箇所を先頭から探す。

    ``(位置と文字の一覧, 打ち切ったか)`` を返す。位置は ``text`` の Python の
    文字位置（``text`` は改行を LF に統一した本文をそのまま渡すこと）。

    ``str.encode()`` は最初の 1 か所で例外になるので、そこから先を
    もう一度 encode し直す、を繰り返して集める。``limit`` 件まで見つけたら
    そこで止める（何万件もある＝文字コードの選択そのものが違う場合に、
    保存のたびに全文を何度も走査しないため）。
    """
    spots: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        try:
            text[start:].encode(encoding)
        except UnicodeEncodeError as exc:
            position = start + exc.start
            spots.append((position, text[position : start + exc.end]))
            start += exc.end
            if len(spots) >= limit:
                # まだ先にもあるかどうかだけ、1 件見つけたら止まる形で調べる。
                rest, _ = find_unencodable(text[start:], encoding, 1)
                return spots, bool(rest)
        else:
            break
    return spots, False


class SuspiciousText(NamedTuple):
    """:func:`find_suspicious_text` の結果（怪しいと見た理由と、その該当箇所）。

    - :attr:`reason` … ``SUSPICIOUS_*`` のいずれか
    - :attr:`spots` … ``(位置, 抜粋)`` の一覧。:class:`EncodingError` の
      ``spots`` と同じ形（位置は改行を LF に統一した本文の Python の文字位置）
      にしてあるので、呼び出し側は同じやり方でカーソルを飛ばせる
    - :attr:`truncated` … 該当箇所が多すぎて :attr:`spots` を打ち切ったか
    """

    reason: str
    spots: list[tuple[int, str]]
    truncated: bool = False


def describe_text_spot(text: str) -> str:
    """該当箇所をダイアログに出すための表記（制御文字を目に見える形にする）。

    制御文字はそのまま並べても何も見えないので、``ESC`` のような名前
    (:data:`_CONTROL_CHARACTER_NAMES`) に置き換える。``\\x1b$B文字`` なら
    ``<ESC>$B文字`` になり、JIS のファイルを UTF-8 と誤判定した跡だと
    一目で分かる。
    """
    return "".join(
        f"<{_CONTROL_CHARACTER_NAMES.get(ord(char), f'0x{ord(char):02X}')}>"
        if _is_unexpected_control(char)
        else char
        for char in text
    )


def _is_unexpected_control(char: str) -> bool:
    """本文にあってはいけない C0 制御文字か（タブ・改行・復帰・改ページは除く）。"""
    return ord(char) < 0x20 and char not in _ALLOWED_CONTROL_CHARACTERS


def _halfwidth_runs(text: str) -> list[tuple[int, int]]:
    """半角カナ・半角句読点が続いている範囲を ``(開始, 終了)`` で並べて返す。"""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, char in enumerate(text):
        if ord(char) in _HALFWIDTH_BLOCK:
            if start is None:
                start = index
        elif start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(text)))
    return runs


def _halfwidth_clusters(text: str) -> list[tuple[int, int, int]]:
    """半角カナの塊のうち、**全角 1 文字を挟んだだけで隣り合うもの**を束ねる。

    ``(開始, 終了, 束ねた塊の数)`` を返す。塊の数が 3 以上なら、その範囲では
    半角カナと全角の日本語が「1 文字おき」に近い細かさで交互に現れている
    ——これが :func:`find_suspicious_text` の言う「無秩序に混ざっている」。

    本物のファイルで半角カナが混ざる場合（全角の文中の ``ﾃﾞｰﾀ`` のような語、
    全角の見出しが付いた半角カナの表）は、塊どうしが**離れている**ので
    束ならない。この「離れているか」の一点が本物と誤読を分ける
    （:func:`find_suspicious_text` の測定結果を参照）。
    """
    clusters: list[tuple[int, int, int]] = []
    for start, end in _halfwidth_runs(text):
        if clusters:
            previous_start, previous_end, count = clusters[-1]
            gap = text[previous_end:start]
            if len(gap) <= 1 and any(_is_fullwidth_japanese(ord(c)) for c in gap):
                clusters[-1] = (previous_start, end, count + 1)
                continue
        clusters.append((start, end, 1))
    return clusters


def _find_control_characters(
    text: str, limit: int
) -> tuple[list[tuple[int, str]], bool]:
    """本文にあってはいけない制御文字を先頭から探す。"""
    spots: list[tuple[int, str]] = []
    for index, char in enumerate(text):
        if not _is_unexpected_control(char):
            continue
        if len(spots) >= limit:
            return spots, True
        spots.append((index, text[index : index + _CONTROL_SPOT_CONTEXT]))
    return spots, False


def find_suspicious_text(
    text: str, encoding: str, limit: int = MAX_SUSPICIOUS_SPOTS
) -> SuspiciousText | None:
    """保存しようとしている本文が「文字化けしたまま」に見えないかを調べる。

    怪しくなければ None。**確実な検知ではなく目安**である——編集した後の
    本文には「本来どの文字コードだったか」という正解がもう無いため、
    「日本語の文章としてこの形は不自然すぎる」としか言えない。
    利用者にもその前提で了承を得てある（PROGRESS.md の引き継ぎ ⑥）。

    :func:`detect_encoding` が**開くときに間違えた**場合の二の矢。開いた
    時点の一の矢は :func:`detect_encoding_detail` の ``alternatives``
    （判定の拮抗を知らせる印）だが、**利用者の実体験では文字化けは編集した
    後に気づくことが多い**ため、保存の直前にもう一度だけ見る。

    見るのは、実際に起きた 2 つの不具合の形だけ。**当てずっぽうの
    ヒューリスティックは足さないこと**（誤検知が増えるだけで、いずれ
    利用者はこの警告を読まなくなる）。

    1. :data:`SUSPICIOUS_CONTROL` … 本文に**タブ・改行・復帰・改ページ
       以外の C0 制御文字**（特に ESC）がある。JIS (ISO-2022-JP) のファイルを
       UTF-8 と誤判定していた不具合の跡。ふつうのテキストに生の制御文字が
       混ざることはまず無いので、これ自体が強い異常。**文字コードを問わず
       見る**（JIS の 7bit のバイト列は cp932 や euc_jp としても「読めて」
       しまうため、UTF-8 でだけ見ても取りこぼす）。
    2. :data:`SUSPICIOUS_MIXED_HALFWIDTH` … cp932 / euc_jp で保存しようと
       していて、**全角の日本語と半角カナが 1 文字おきに近い細かさで
       交互に現れる**（:func:`_halfwidth_clusters`）。EUC-JP のファイルを
       cp932 として読んでしまった不具合の跡。
    3. :data:`SUSPICIOUS_HALFWIDTH_PUNCTUATION` … 同じく cp932 / euc_jp で、
       **半角の句読点 (``｡｢｣､･``) が半角の並びの 3 割以上を占める**。
       EUC-JP のひらがなの 1 バイト目 0xA4 が cp932 で ``､`` になるため、
       ``､ｳ､ﾋ､ﾁ､ﾏ`` のように 1 文字おきに ``､`` が並ぶ。

    **測って決めたこと（同じ結論に戻ってこられるように残す）**。無作為に
    作った日本語 2 万通りずつで、誤検知したら困る形と拾いたい形を測った:

    - 誤検知 0%: ふつうの日本語 / 漢字だけの短い文 / 本物の半角カナだけの
      ファイル / 全角の見出しが付いた半角カナの表 / 全角の文に半角カナの語が
      1〜2 個 / ASCII だけ。
    - 拾えた: EUC-JP を cp932 で読んだ本文の **65%**（編集した後でも同じ）。
      **漢字だけの EUC-JP を読み違えた場合は 8% しか拾えない**——読み違えても
      半角カナだけの並びにしかならず、本物の半角カナのファイルと**原理的に
      見分けられない**ため（:func:`_pick_japanese_legacy_detail` の「既知の限界」と
      同じ壁）。この形は開いた時点の一の矢（拮抗の印）の担当。

    **採らなかった案**（測って捨てた。同じ案を思いついたら、まず測ること）:

    - :func:`_japanese_plausibility` の点数が負（半角カナが全角より重い）を
      理由にする案 … **全角の見出しが 1 行付いた半角カナ主体のデータファイル
      で 100% 誤検知した**。実際にありうる形なので採れない。
    - 同じ点数を**行ごとに**見る案 … 半角カナの列が長い表で **96% 誤検知**。
    - 半角の句読点が「1 文字おきに並ぶ」ことまで求めて絞る案 … 誤検知は
      7%→3% に減るが、拾える率も 65%→57% に落ちたので採らなかった
      （減らせる誤検知は ``､`` を区切りに使う半角カナの CSV という珍しい形
      だけで、割に合わない）。

    **既知の誤検知**: 半角の読点 ``､`` を区切りに使った半角カナの CSV
    （``ｱｲｳ､ｴｵ､ｶｷ``）は 7% ほど引っかかる。ダイアログで「このまま保存する」
    を押せば保存できるが、そのファイルでは毎回出る。うるさければ
    :meth:`~editor_app.file_commands.FileCommandsMixin._confirm_suspicious_text`
    の先頭で True を返せば、この判定ごと外せる。
    """
    spots, truncated = _find_control_characters(text, limit)
    if spots:
        return SuspiciousText(SUSPICIOUS_CONTROL, spots, truncated)

    if encoding not in _MOJIBAKE_PRONE_ENCODINGS:
        return None

    # 塊が n 個束なっている＝細かい交互が n-1 回あった、ということ。
    mixed = [
        (start, text[start:end])
        for start, end, count in _halfwidth_clusters(text)
        if count - 1 >= _MIN_TIGHT_ALTERNATIONS
    ]
    if mixed:
        return SuspiciousText(
            SUSPICIOUS_MIXED_HALFWIDTH, mixed[:limit], len(mixed) > limit
        )

    runs = _halfwidth_runs(text)
    total = sum(end - start for start, end in runs)
    punctuation = sum(1 for char in text if ord(char) in _HALFWIDTH_PUNCTUATION)
    if (
        punctuation >= _MIN_HALFWIDTH_PUNCTUATION
        and total
        and punctuation / total >= _MIN_HALFWIDTH_PUNCTUATION_RATIO
    ):
        spotted = [
            (start, text[start:end])
            for start, end in runs
            if any(ord(char) in _HALFWIDTH_PUNCTUATION for char in text[start:end])
        ]
        return SuspiciousText(
            SUSPICIOUS_HALFWIDTH_PUNCTUATION, spotted[:limit], len(spotted) > limit
        )
    return None


class EncodingDetectionError(ValueError):
    """文字コードを判定できず、テキストとして読めなかった場合に送出する。"""


class EncodingError(ValueError):
    """保存しようとした文字が、その文字コードで表現できなかった場合に送出する。

    例: Shift-JIS のファイルに絵文字や ♡・— を書いて保存しようとした場合。

    メッセージだけでは「その文字が本文のどこにあるか」が分からず、長い
    実際のファイルでは探しようがない（実際に、貼り付けた記号 1 文字のせいで
    保存できなくなる）。呼び出し側 (``MainWindow``) がその場所へカーソルを
    飛ばせるよう、**該当箇所を構造化して持たせてある**。

    - :attr:`encoding` … 保存しようとした文字コード
    - :attr:`spots` … ``(位置, 文字)`` の一覧。位置は
      **改行を LF に統一した本文（エディタが持っている本文）の Python の
      文字位置**。``encode_text`` は改行を元に戻してから encode するが、
      改行そのものは必ず表現できるので、位置は LF のまま数えてよい。
    - :attr:`truncated` … 該当箇所が多すぎて :attr:`spots` を打ち切ったか
    """

    def __init__(
        self,
        message: str,
        *,
        encoding: str = "",
        spots: list[tuple[int, str]] | None = None,
        truncated: bool = False,
    ) -> None:
        super().__init__(message)
        self.encoding = encoding
        self.spots: list[tuple[int, str]] = spots or []
        self.truncated = truncated


def _japanese_plausibility(text: str) -> int:
    """読み方が「日本語の文章として、ありそうか」を点数にする（高いほどありそう）。

    cp932 と euc_jp を見分けるためだけのもの
    (:func:`_pick_japanese_legacy_detail`)。文字の種類だけを数える単純な計算で、
    文法や語彙は見ない。**片方の読み方がもう片方より「ありそう」かを比べる
    相対的な物差し**であって、単独の点数に意味は無い。

    数え方（重みは実際の日本語の文で試して決めた。下の「誤読の跡」を参照）:

    - 全角の日本語（ひらがな・カタカナ・漢字・全角記号）… **+2**
    - 半角カナ（``ｦ``〜``ﾟ``）… 全角の日本語が 1 文字も無ければ **+1**、
      混ざっていれば **−1**
    - それ以外（ASCII・半角の句読点・私用領域など）… **数えない**（下記）

    **誤読の跡**: EUC-JP の文を cp932 として読むと、EUC-JP のひらがなの
    1 バイト目 ``0xA4`` が cp932 では半角の読点 ``､`` になるため、
    ``､｢､､､ｦ､ｨ､ｪ``（＝「あいうえお」）のように**半角の読点 ``､`` が 1 文字
    おきに並ぶ**。日本語の文章でこうはならない。cp932 の外字（私用領域）に
    落ちるのも誤読のときだけ。

    半角カナを「全角と混ざっていたら減点」にしているのは、**本物の半角カナの
    ファイルは半角カナだけで書かれている**（レガシーな固定長データ等）のに対し、
    誤読で出てくる半角カナは全角と無秩序に混ざるため。

    **半角の句読点と私用領域を「数えない」のは意図的**。どちらも誤読の跡では
    あるが、減点にしても**判定が変わる入力が 1 つも見つからなかった**（全角側の
    +2 が先に効くため。無作為に作った日本語 100 万通りで確かめた。私用領域は
    3 万件以上の読み方に現れたが、1 件も勝敗を変えなかった）。テストで縛れない
    重みは、いずれ誰かが理由も分からないまま動かすので、点数から外してある。

    ただし**半角の句読点を半角カナと同じ「+」側に入れてはいけない**——``､`` が
    1 文字おきに並ぶ誤読の読み方が本物と並んでしまい、ひらがなだけの EUC-JP の
    ファイル（``あいうえお`` 等）が cp932 に化ける（``io-jp-halfwidth-punct``
    で縛ってある）。私用領域も同じ理由で「+」側に入れてはいけない。
    """
    fullwidth = 0
    halfwidth_kana = 0
    for char in text:
        code = ord(char)
        if 0xFF66 <= code <= 0xFF9F:            # 半角カナ
            halfwidth_kana += 1
        elif _is_fullwidth_japanese(code):
            fullwidth += 1
    return 2 * fullwidth + (halfwidth_kana if fullwidth == 0 else -halfwidth_kana)


def _is_fullwidth_japanese(code: int) -> bool:
    """その文字が「全角の日本語」(:data:`_FULLWIDTH_JAPANESE_RANGES`) か。

    :func:`_japanese_plausibility` と :func:`find_suspicious_text` が
    **同じ物差しで数える**ための共有部品。片方だけ範囲を足すと、判定と
    警告が食い違う（「開くときは化けていないと言い、保存するときは怪しいと
    言う」）ので、範囲を変えるときは必ずここ 1 か所で変えること。
    """
    return any(low <= code <= high for low, high in _FULLWIDTH_JAPANESE_RANGES)


def _pick_japanese_legacy_detail(data: bytes) -> tuple[str | None, tuple[str, ...]]:
    """日本語のレガシー文字コード (:data:`_STRICT_JAPANESE_ENCODINGS`) から選ぶ。

    ``(選んだもの, 他の候補)`` を返す。どれでも読めなければ ``(None, ())``。

    **「先に試して読めた方」で決めてはいけない。** cp932 と euc_jp は
    互いに無効なバイト列を弾いてくれるが、**両方で読めてしまうバイト列も
    多い**。特に EUC-JP のひらがな・カタカナは 1 バイト目が ``0xA4`` /
    ``0xA5`` で、cp932 ではこれが**半角カナ 1 文字として成立してしまう**ため、
    **ひらがなを含む EUC-JP のファイルはほぼ必ず cp932 としても読めてしまう**
    （手元の日本語の文で試すと、EUC-JP の 4 分の 1 が cp932 で「読めて」
    しまった）。並び順だけで決めていた頃は、そういうファイルが
    ``､ｳ､ﾋ､ﾁ､ﾏ`` のように**黙って化けて開いていた**——例外にならないので
    「開けません」にもならず、しかもこのアプリ自身が「文字コードを指定して
    保存」で EUC-JP を選ばせるので、**自分で保存したファイルを自分で
    開けない**状態だった（2026-08-15 の JIS の不具合と同じ形）。

    逆向き（cp932 の文が euc_jp としても読める）はほとんど起きない。cp932 の
    2 バイト目は ``0x40``〜``0xA0`` を取ることが多く、EUC-JP はそこを弾くため。
    つまり**誤りは euc_jp のファイルにだけ偏って出る**。

    そこで、読めた候補それぞれを実際に読んでみて、
    :func:`_japanese_plausibility` が高い方を採る。同点なら
    :data:`_STRICT_JAPANESE_ENCODINGS` の並び順（＝ cp932）。

    **既知の限界**: 中身が**半角カナだけ**の EUC-JP ファイルは cp932 と
    判定する（``ｱｲｳｴｵ`` のような読み方が両方で成立し、どちらも同じくらい
    「ありそう」なため、原理的に見分けられない）。半角カナだけのファイルは
    Shift-JIS で書かれている方がはるかに多いので、引き分けは cp932 に倒す。
    この「引き分けたので勝った」という事情は、次の「他の候補」として
    呼び出し元へ返る。

    **「他の候補」＝判定が拮抗した証拠**。cp932 と euc_jp の**どちらでも
    読めてしまった**とき、負けた方をここで捨てずに呼び出し元まで返す。
    この事実は選ぶ過程で必ず計算されているのに、以前は勝った方の名前しか
    返しておらず、**「たまたま勝っただけかもしれない」ことを利用者に伝える
    手立てが無かった**（誤判定に気づけるのは、開いた本文が化けて見えたときの
    見た目だけ。編集して保存した後だとファイルが本当に壊れる）。

    拮抗が無ければ空のタプル。

    **「半角カナだけに読めた場合は黙っておく」ことは、してはいけない。**
    ここは実装中に一度そうしかけて、測って撤回した箇所なので理由を残す。
    ``ｱｲｳｴｵ`` のような本物の半角カナのファイルは、確かにどちらでも読めて
    しまい、どちらも同じくらい「ありそう」で見分けられない（下の
    :func:`_pick_japanese_legacy_detail` の「既知の限界」）。だからといって
    「勝った読み方が半角カナだけなら黙る」ことにすると、**判定が実際に
    外れる場面をちょうど狙って黙る**ことになる——短い漢字だけの EUC-JP の
    ファイル（``文議確``）は cp932 で ``ﾊｸｵﾄｳﾎ`` という半角カナに化けるので、
    まさにこの形だからである。無作為な日本語 3 万通りで測ったところ、
    判定が外れた 16 件は**全てこの形**で、黙る実装では 1 件も知らせられ
    なかった。**知らせて損なのは半角カナのファイルを開いた人が印を 1 つ
    見ることだけ**（印は作業を止めない）で、黙って損なのはファイルが
    壊れることなので、釣り合わない。
    """
    readings: dict[str, str] = {}
    for encoding in _STRICT_JAPANESE_ENCODINGS:
        try:
            readings[encoding] = data.decode(encoding)
        except UnicodeDecodeError:
            continue
    if not readings:
        return None, ()

    best = max(
        readings,
        key=lambda enc: (
            _japanese_plausibility(readings[enc]),
            -_STRICT_JAPANESE_ENCODINGS.index(enc),
        ),
    )
    others = tuple(enc for enc in _STRICT_JAPANESE_ENCODINGS if enc in readings and enc != best)
    return best, others


class EncodingDetection(NamedTuple):
    """:func:`detect_encoding_detail` の結果。

    - :attr:`encoding` … 判定したコーデック名
    - :attr:`alternatives` … **同じバイト列が読めてしまった別のコーデック名**
      （＝判定が拮抗した証拠）。拮抗が無ければ空。利用者に
      「Shift-JIS として開きましたが EUC-JP かもしれません」と伝えるためのもの。
    """

    encoding: str
    alternatives: tuple[str, ...] = ()


def detect_encoding_detail(data: bytes) -> EncodingDetection:
    """:func:`detect_encoding` の中身。拮抗した別候補も一緒に返す。"""
    for bom, encoding in _BOM_TABLE:
        if data.startswith(bom):
            return EncodingDetection(encoding)

    if _ISO2022_JP_ESCAPE in data:
        # エスケープ列らしきものがあっても ISO-2022-JP とは限らない（UTF-8 の
        # ファイルにたまたま ESC $ が入っていることはありうる）ので、実際に
        # 読めたときだけ採用し、駄目なら下の UTF-8 の判定へ落とす。
        try:
            data.decode("iso2022_jp")
        except UnicodeDecodeError:
            pass
        else:
            return EncodingDetection("iso2022_jp")

    try:
        data.decode("utf-8")
        return EncodingDetection(DEFAULT_ENCODING)
    except UnicodeDecodeError:
        pass

    japanese, alternatives = _pick_japanese_legacy_detail(data)
    if japanese is not None:
        return EncodingDetection(japanese, alternatives)

    best_guess = from_bytes(data).best()
    if best_guess is not None:
        try:
            data.decode(best_guess.encoding)
        except (UnicodeDecodeError, LookupError):
            pass
        else:
            return EncodingDetection(best_guess.encoding)

    raise EncodingDetectionError(
        "文字コードを判定できませんでした "
        "(BOM・UTF-8・日本語レガシー文字コードのいずれでもなく、"
        "他の文字コードとしても推定できません)。"
    )


def detect_encoding(data: bytes) -> str:
    """バイト列から文字コードを判定してコーデック名を返す。

    判定の優先順位:

    1. BOM（UTF-8 / UTF-16 / UTF-32）
    2. ISO-2022-JP のエスケープ列（:data:`_ISO2022_JP_ESCAPE`）があり、実際に
       ISO-2022-JP として読めるか（**UTF-8 より先**でなければならない理由は
       :data:`_ISO2022_JP_ESCAPE` の説明を参照）
    3. UTF-8 として読めるか（BOM なしの UTF-8 が大半のため、これが読めれば
       それを信用する）
    4. 日本語のレガシー文字コード（:data:`_STRICT_JAPANESE_ENCODINGS`）として
       厳密にデコードできるか（無効なバイト列は `UnicodeDecodeError` になる
       ため、統計的な推定より先に確実な判定として試す）。**複数読めたときは
       並び順ではなく読んだ中身で選ぶ**（:func:`_pick_japanese_legacy_detail`）
    5. charset-normalizer による統計的な推定（日本語以外の文字コードの
       ファイルにも対応するための、最後の手段）

    どれでも読めなければ :class:`EncodingDetectionError`。

    判定が拮抗したかどうかも要る場合は :func:`detect_encoding_detail` を使う。
    """
    return detect_encoding_detail(data).encoding


def detect_newline(text: str) -> str:
    """テキスト中で最初に現れる改行コードを返す。改行が無ければ LF。"""
    index = text.find("\n")
    carriage = text.find("\r")
    if carriage >= 0 and (index < 0 or carriage < index):
        # \r が先に来る: CRLF か、古い Mac 形式の CR
        return "\r\n" if text[carriage : carriage + 2] == "\r\n" else "\r"
    if index >= 0:
        return "\n"
    return DEFAULT_NEWLINE


class TextFileContent(NamedTuple):
    """:func:`read_text_file_detail` の結果（本文・文字コード・改行コード・別候補）。"""

    text: str
    encoding: str
    newline: str
    alternatives: tuple[str, ...] = ()


def _decode_text(data: bytes, encoding: str) -> tuple[str, str]:
    """バイト列を ``encoding`` で読み、``(本文, 改行コード)`` を返す。

    本文の改行は編集しやすいように LF に統一し、元の改行コードは戻り値の
    2 つめとして返す（保存時に復元する。機能 3）。BOM の始末もここで行う。

    「判定した文字コードで読む」(:func:`read_text_file_detail`) と「利用者が
    指定した文字コードで読み直す」(:func:`read_text_file_as`) が**同じ後始末**
    を通るように 1 か所にまとめてある（片方だけ BOM を剥がし忘れる、と
    いったずれを作らないため）。
    """
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError as exc:  # BOM 付きなのに壊れている等
        raise EncodingDetectionError(
            f"{encoding} として読み込めませんでした。"
        ) from exc

    if encoding in BOM_BY_ENCODING and text.startswith("\ufeff"):
        text = text[1:]

    newline = detect_newline(text)
    return text.replace("\r\n", "\n").replace("\r", "\n"), newline


def read_text_file_detail(path: Path | str) -> TextFileContent:
    """ファイルを読み込み、文字コードの判定が拮抗した別候補まで含めて返す。"""
    data = Path(path).read_bytes()
    detection = detect_encoding_detail(data)
    text, newline = _decode_text(data, detection.encoding)
    return TextFileContent(text, detection.encoding, newline, detection.alternatives)


def read_text_file(path: Path | str) -> tuple[str, str, str]:
    """ファイルを読み込み ``(本文, 文字コード, 改行コード)`` を返す。

    判定が拮抗したかどうかまで要る呼び出し側は
    :func:`read_text_file_detail` を使う（こちらは 3 つ組だけを見たい
    呼び出し側のために残してある）。
    """
    content = read_text_file_detail(path)
    return content.text, content.encoding, content.newline


def read_text_file_as(path: Path | str, encoding: str) -> tuple[str, str]:
    """ファイルを**指定された文字コードで**読み、``(本文, 改行コード)`` を返す。

    「文字コードを指定して開き直す」用（判定に任せず、利用者が選んだ
    文字コードをそのまま信じる）。その文字コードで読めなければ
    :class:`EncodingDetectionError`。
    """
    return _decode_text(Path(path).read_bytes(), encoding)


def encode_text(
    text: str,
    encoding: str = DEFAULT_ENCODING,
    newline: str = DEFAULT_NEWLINE,
) -> bytes:
    """本文 (改行は LF) を、元のファイルと同じ形式のバイト列に戻す。

    :func:`read_text_file` の逆変換。改行コードを元に戻し、コーデック自身が
    BOM を付けてくれない UTF-16/32 には :data:`BOM_BY_ENCODING` から BOM を補う。

    その文字コードで表現できない文字が含まれていた場合は :class:`EncodingError`
    （**該当箇所の位置つき**。呼び出し側がそこへカーソルを飛ばせるように
    するため、位置は改行を LF に統一したままの ``text`` 上で数える）。
    """
    lf_text = text
    if newline != "\n":
        text = text.replace("\n", newline)

    try:
        data = text.encode(encoding)
    except UnicodeEncodeError as exc:
        spots, truncated = find_unencodable(lf_text, encoding)
        bad = spots[0][1] if spots else exc.object[exc.start : exc.end]
        raise EncodingError(
            f"{describe_encoding(encoding)} では表現できない文字 {bad!r} が"
            "含まれています。",
            encoding=encoding,
            spots=spots,
            truncated=truncated,
        ) from exc

    bom = BOM_BY_ENCODING.get(encoding)
    if bom is not None:
        data = bom + data
    return data


def default_file_mode() -> int:
    """新しく作るファイルに与えるパーミッション (``0o666`` から umask を引いたもの)。

    :func:`write_text_file` は一時ファイル経由で書くが、
    ``tempfile.mkstemp()`` は「他の誰にも見せない」``0o600`` で作る。
    そのまま :func:`os.replace` すると、**新規作成したファイルだけが
    ``0o600``** になってしまい、共有フォルダ (NAS 等) に置いたときに
    他の利用者・他の PC から読めなくなる。普通に ``open()`` して書いた
    ファイル (通常 ``0o644``) と同じ見え方に揃えるための計算。

    umask は「今の値を読む」API が無く、一度書き換えて戻すしかない
    （このアプリはファイルの読み書きを別スレッドで行わないため、
    書き換えている一瞬に他の処理が割り込むことはない）。
    """
    umask = os.umask(0o022)
    os.umask(umask)
    return 0o666 & ~umask


def write_text_file(
    path: Path | str,
    text: str,
    encoding: str = DEFAULT_ENCODING,
    newline: str = DEFAULT_NEWLINE,
) -> None:
    """本文を元の文字コード・改行コードのままファイルへ書き出す。

    :func:`encode_text` してから :func:`write_encoded_file` に渡すだけ。
    **保存の直前に「この文字コードで書いたらどう見えるか」を確かめてから
    書きたい場合**（:meth:`~editor_app.file_commands.FileCommandsMixin.
    _write_editor`）は、この 2 つを別々に呼ぶこと。同じ本文を 2 度
    encode せずに、間へ確認を挟める。
    """
    write_encoded_file(path, encode_text(text, encoding, newline))


def write_encoded_file(path: Path | str, data: bytes) -> None:
    """バイト列をファイルへ書き出す（**ディスクへ書くのはここ 1 か所だけ**）。

    書き込み途中で失敗して元のファイルを壊さないよう、同じディレクトリの
    一時ファイルに書いてから :func:`os.replace` で置き換える。
    パーミッションは、既存ファイルがあればそれを引き継ぎ、新規作成なら
    :func:`default_file_mode` （＝普通に作ったファイルと同じ）にする。
    """
    path = Path(path)

    directory = path.parent if str(path.parent) else Path(".")
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
        if path.exists():
            shutil.copymode(path, tmp_path)
        else:
            os.chmod(tmp_path, default_file_mode())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
