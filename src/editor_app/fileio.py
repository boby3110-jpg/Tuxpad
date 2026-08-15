"""ファイルの読み書き (文字コード・改行コードの扱い).

このモジュールだけがバイト列と文字列の変換を担当する。呼び出し側
(MainWindow) はここの戻り値だけを見ればよいようにしてある。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

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
#: 実際に出会うのはほぼ Shift-JIS (cp932) で、稀に EUC-JP・ISO-2022-JP もありうる。
#: これらの日本語レガシー文字コードは互いに無効なバイト列を厳密に弾いてくれる
#: ため、charset-normalizer の統計的な推定より確実に判定できる
#: （実測で、charset-normalizer 単体だと漢字の多い日本語文を中国語系の
#: 文字コード (gb18030 など) に誤判定することがあったため、この方式にした）。
_STRICT_JAPANESE_ENCODINGS: tuple[str, ...] = ("cp932", "euc_jp", "iso2022_jp")

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

#: :func:`find_unencodable` が一度に探す該当箇所の上限。全部数えようとすると
#: 「文字コードが根本的に合っていない」場合に何万件も走査することになるため、
#: 先頭から数件見つけた時点で切り上げる (利用者が直すのに必要なのは
#: 「どこから直せばよいか」であって、正確な総数ではない)。
MAX_UNENCODABLE_SPOTS = 20


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


def describe_encoding(encoding: str) -> str:
    """利用者に見せる文字コードの呼び名 (未知のものはコーデック名のまま)。"""
    return ENCODING_DISPLAY_NAMES.get(encoding, encoding)


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


def detect_encoding(data: bytes) -> str:
    """バイト列から文字コードを判定してコーデック名を返す。

    判定の優先順位:

    1. BOM（UTF-8 / UTF-16 / UTF-32）
    2. UTF-8 として読めるか（BOM なしの UTF-8 が大半のため、これが読めれば
       それを信用する）
    3. 日本語のレガシー文字コード（:data:`_STRICT_JAPANESE_ENCODINGS`）として
       厳密にデコードできるか（無効なバイト列は `UnicodeDecodeError` になる
       ため、統計的な推定より先に確実な判定として試す）
    4. charset-normalizer による統計的な推定（日本語以外の文字コードの
       ファイルにも対応するための、最後の手段）

    どれでも読めなければ :class:`EncodingDetectionError`。
    """
    for bom, encoding in _BOM_TABLE:
        if data.startswith(bom):
            return encoding

    try:
        data.decode("utf-8")
        return DEFAULT_ENCODING
    except UnicodeDecodeError:
        pass

    for encoding in _STRICT_JAPANESE_ENCODINGS:
        try:
            data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return encoding

    best_guess = from_bytes(data).best()
    if best_guess is not None:
        try:
            data.decode(best_guess.encoding)
        except (UnicodeDecodeError, LookupError):
            pass
        else:
            return best_guess.encoding

    raise EncodingDetectionError(
        "文字コードを判定できませんでした "
        "(BOM・UTF-8・日本語レガシー文字コードのいずれでもなく、"
        "他の文字コードとしても推定できません)。"
    )


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


def read_text_file(path: Path | str) -> tuple[str, str, str]:
    """ファイルを読み込み ``(本文, 文字コード, 改行コード)`` を返す。

    本文の改行は編集しやすいように LF に統一する。元の改行コードは
    戻り値の 3 つめとして返し、保存時に復元する (機能 3)。
    """
    data = Path(path).read_bytes()
    encoding = detect_encoding(data)
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError as exc:  # BOM 付きなのに壊れている等
        raise EncodingDetectionError(
            f"{encoding} として読み込めませんでした。"
        ) from exc

    if encoding in BOM_BY_ENCODING and text.startswith("\ufeff"):
        text = text[1:]

    newline = detect_newline(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, encoding, newline


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

    書き込み途中で失敗して元のファイルを壊さないよう、同じディレクトリの
    一時ファイルに書いてから :func:`os.replace` で置き換える。
    パーミッションは、既存ファイルがあればそれを引き継ぎ、新規作成なら
    :func:`default_file_mode` （＝普通に作ったファイルと同じ）にする。
    """
    path = Path(path)
    data = encode_text(text, encoding, newline)

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
