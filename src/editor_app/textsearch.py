"""全タブ横断検索 (機能 6・7) のための、文字位置まわりの小さな純粋関数。

検索は Python の文字列 (``QPlainTextEdit.toPlainText()`` の戻り値) に対して
行うが、見つかった位置は ``QTextCursor.setPosition()`` に渡して選択・置換する。
ところが「文字列の長さ・位置」の数え方が Python と Qt で一致しない箇所が
2 つあり、素直に書くと**本文の別の場所を書き換えてしまう**。

1. ``str.lower()`` は文字によって文字数が変わる
   (例: ``"İ"`` (U+0130) は 2 文字の ``"i̇"`` になる)。大小文字を無視した検索の
   ために本文全体を小文字化すると、それ以降の文字位置が元の本文とずれる。
2. Python の文字列は 1 文字 = 1 コードポイントで数えるが、Qt の文書内位置は
   UTF-16 コード単位で数える。絵文字などの BMP 外の文字は Python では 1、
   Qt では 2 と数えられるため、それ以降の位置がずれる。

どちらも「日本語の実際のファイル」でも十分起こりうる (絵文字の貼り付け等) 上、
ずれたまま置換すると気づきにくい形で本文が壊れるため、ここで吸収する。

このモジュールは Qt にも ``MainWindow`` にも依存しない。1 つの文書 (文字列)
に対する「どこにマッチしたか」「何行目か」「一覧に出すプレビュー」までを
ここで完結させ、``MainWindow`` 側はタブを回してその結果を
``SearchMatch`` に詰め替えるだけにしてある。
"""

from __future__ import annotations

from dataclasses import dataclass


def fold_case(text: str) -> str:
    """大小文字を無視した検索のために、**文字数を変えずに**小文字化する。

    ``str.lower()`` は文字数が変わることがある (モジュールの説明を参照) ので、
    そのまま使うと小文字化後の文字位置が元の本文とずれてしまう。
    文字数が変わる文字だけは元のまま残して位置を保つ
    (その文字については大小文字を区別する挙動になるが、位置がずれて本文を
    壊すよりはよい)。
    """
    lowered = text.lower()
    if len(lowered) == len(text):
        return lowered  # 大半のテキストはこちら (1 回の走査で済ませる)

    folded: list[str] = []
    for char in text:
        low = char.lower()
        folded.append(low if len(low) == 1 else char)
    return "".join(folded)


def utf16_length(text: str) -> int:
    """Qt が文書内位置を数えるのと同じ「UTF-16 コード単位」での長さ。

    BMP 外の文字 (絵文字など) は 2 と数える。Python の ``len()`` との差が、
    そのまま Qt の文書内位置とのずれになる。
    """
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def surrogate_extra(text: str) -> int:
    """``text`` に含まれる BMP 外の文字の個数 (= Qt と Python の位置の差)。"""
    return utf16_length(text) - len(text)


@dataclass(frozen=True)
class MatchSpan:
    """1 つの文書内で見つかった 1 件のマッチ。

    同じ場所を 2 通りの数え方で持つ。用途が違うので取り違えないこと。

    - ``py_position`` / ``py_length`` … Python の文字列上の位置。
      行番号やプレビューの切り出し (:func:`line_number_at` /
      :func:`line_snippet`) に使う。
    - ``position`` / ``length`` … Qt の文書内位置 (UTF-16 コード単位)。
      ``QTextCursor.setPosition()`` にそのまま渡して選択・置換に使う。
    """

    py_position: int
    py_length: int
    position: int
    length: int


def find_matches(text: str, query: str) -> list[MatchSpan]:
    """``text`` から ``query`` を大小文字を区別せずに全件探す。

    検索語が改行を含む場合、マッチも複数行にまたがる (EmEditor の複数行検索と
    同様)。そのため行ごとではなく、文書全体を 1 つの文字列として扱う。

    大小文字を無視するための小文字化は :func:`fold_case` で行い、Qt の文書内
    位置とのずれは :func:`surrogate_extra` で足し込む (モジュールの説明を参照)。
    検索語が空なら 1 件も返さない。
    """
    if not query:
        return []

    needle = fold_case(query)
    haystack = fold_case(text)  # fold_case は文字数を変えない
    # 検索語側も Qt の数え方に合わせる (絵文字を含む語で置換範囲がずれないように)。
    needle_qt_length = utf16_length(query)

    spans: list[MatchSpan] = []
    start = 0
    # Python の文字位置 → Qt の文書内位置のずれ (BMP 外の文字の個数)。
    # マッチは前から順に見つかるので、差分だけを足していけば済む。
    extra = 0
    scanned = 0
    while True:
        pos = haystack.find(needle, start)
        if pos < 0:
            break
        extra += surrogate_extra(text[scanned:pos])
        scanned = pos
        spans.append(
            MatchSpan(
                py_position=pos,
                py_length=len(query),
                position=pos + extra,
                length=needle_qt_length,
            )
        )
        start = pos + len(needle)
    return spans


def line_number_at(text: str, position: int) -> int:
    """``position`` (Python の文字位置) が何行目かを 1 始まりで返す。"""
    return text.count("\n", 0, position) + 1


def line_snippet(text: str, position: int, length: int, newline_glyph: str) -> str:
    """検索結果の一覧に出すプレビュー文字列を切り出す。

    マッチを含む行を丸ごと (複数行にまたがるマッチならその全行を) 取り出し、
    前後の空白を落として 1 行に畳む。畳んだ改行は ``newline_glyph`` に置き換え、
    「ここで行が変わっている」ことが分かるようにする。
    位置・長さはどちらも Python の文字列上の数え方で渡すこと。
    """
    line_start = text.rfind("\n", 0, position) + 1  # 見つからなければ 0
    line_end = text.find("\n", position + length)
    if line_end < 0:
        line_end = len(text)
    return text[line_start:line_end].strip().replace("\n", newline_glyph)
