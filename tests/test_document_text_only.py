"""本文の取り出しに、Qt の生の API を直に呼んでいる場所が無いか。

`src/` の中では、本文は必ず :func:`editor_app.editor.document_text` を
通して取り出す約束にしてある。Qt の側の 2 つの API は、どちらも**黙って
本文を変えてしまう**ため:

- ``QTextDocument.toPlainText()`` … **改行しない空白 (U+00A0, NBSP) を
  普通の半角空白へ置き換える**。保存の経路でこれを使うと「開いて、何も
  触らずに保存した」だけで本文が書き換わる（2026-08-17（27 回目）に実際に
  そうなっていた）。**NBSP と空白は画面上で見分けが付かないので、利用者は
  気づけない。**
- ``toRawText()`` … 置き換えはしない代わりに、**改行が ``U+2029``**
  （Shift+Enter は ``U+2028``）で返る。これをそのまま本文として扱うと、
  改行が 1 文字も無い 1 行の文書として保存されたり、Shift-JIS で保存
  できなくなったりする。``document_text()`` はこの 2 文字を ``\\n`` へ
  直すためだけに ``toRawText()`` を使っている。

**なぜこの形のテストが要るか。** 既にある NBSP のテスト
（`test_invisible_characters.py`・`test_no_break_space_downgrade.py`）は
「今ある保存の経路」が正しいことを固定しているだけで、**この先増える
呼び出し口**は見ていない。本文を読む所は保存・検索・置換・外部変更の
検出・空タブの判定と既に 5 か所以上あり、新しく足した所で
``toPlainText()`` を呼んでも**既存のテストは全部緑のまま通る**。
壊れ方が「静かに本文が変わる」なので、通ってしまうと誰も気づけない。

そこで**呼んでいる場所そのもの**を機械的に見る。文字列ではなく構文木
(:mod:`ast`) で見ているので、この docstring のように**説明として名前を
書いただけのものは引っかからない**（`editor.py` と `fileio.py` の説明文が
実際に該当する）。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "src"

#: 本文を変えてしまう Qt のメソッド名。
FORBIDDEN = ("toPlainText", "toRawText")

#: ``toRawText()`` を呼んでよい唯一の場所（``document_text()`` の中身）。
#: ここが U+2029 / U+2028 を ``\n`` へ直す役なので、例外はここだけ。
RAW_TEXT_ALLOWED_IN = {("editor.py", "document_text")}


def source_files() -> list[Path]:
    """`src/` 以下の .py 全部（入り口の `main.py` も含む）。"""
    files = sorted(SOURCE_DIR.rglob("*.py"))
    assert files, "src/ に .py が 1 つも無い（探し方が壊れている）"
    return files


def enclosing_function(tree: ast.AST, lineno: int) -> str:
    """`lineno` を含む**一番内側**の関数名（関数の外なら空文字）。

    `document_text()` の中だけ ``toRawText()`` を許すため、呼び出しが
    どの関数の中にあるかを引けるようにする。**内側を採る**のが要:
    入れ子の関数の中の呼び出しを外側の関数名で報告すると、
    「`document_text()` の中だから許す」と誤って通してしまう。
    """
    containing = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= lineno <= (node.end_lineno or node.lineno)
    ]
    if not containing:
        return ""
    # 始まりが最も後ろのものが、一番内側。
    return max(containing, key=lambda node: node.lineno).name


def forbidden_calls() -> list[tuple[str, int, str, str]]:
    """(ファイル名, 行, メソッド名, 含む関数名) を集める。"""
    found: list[tuple[str, int, str, str]] = []
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN:
                found.append(
                    (
                        path.name,
                        node.lineno,
                        func.attr,
                        enclosing_function(tree, node.lineno),
                    )
                )
    return found


def test_no_source_file_calls_to_plain_text() -> None:
    """`src/` の中で ``toPlainText()`` を呼んでいる所は 1 つも無い。

    呼ぶと NBSP が黙って半角空白に変わる。本文は
    ``document_text(editor)`` で取り出すこと。
    """
    offenders = [
        f"{name}:{line} ({func or '関数の外'})"
        for name, line, attr, func in forbidden_calls()
        if attr == "toPlainText"
    ]

    assert not offenders, (
        "toPlainText() を呼んでいる。NBSP (U+00A0) が黙って半角空白に"
        " 変わるので、本文は document_text(editor) で取り出すこと: "
        f"{offenders}"
    )


def test_to_raw_text_is_called_only_inside_document_text() -> None:
    """``toRawText()`` を呼ぶのは ``document_text()`` の中だけ。

    ほかで呼ぶと、改行が ``U+2029`` / ``U+2028`` のまま本文として流れる。
    """
    offenders = [
        f"{name}:{line} ({func or '関数の外'})"
        for name, line, attr, func in forbidden_calls()
        if attr == "toRawText" and (name, func) not in RAW_TEXT_ALLOWED_IN
    ]

    assert not offenders, (
        "document_text() の外で toRawText() を呼んでいる。改行が U+2029 /"
        " U+2028 のまま本文になるので、document_text(editor) を通すこと: "
        f"{offenders}"
    )


def test_document_text_still_uses_to_raw_text() -> None:
    """許可した側が生きていること（この検査そのものが古びていないか）。

    `document_text()` の実装が変わって ``toRawText()`` を使わなくなったら、
    上の許可は**何も許していない死んだ例外**になる。そのとき
    `RAW_TEXT_ALLOWED_IN` を消し忘れると、後から誰かが
    `document_text()` に ``toRawText()`` を書き足しても気づけない。
    """
    calls = {
        (name, func)
        for name, _line, attr, func in forbidden_calls()
        if attr == "toRawText"
    }

    assert calls == RAW_TEXT_ALLOWED_IN, (
        "toRawText() を呼んでいる場所が、許可した一覧と食い違っている。"
        f" 実際: {sorted(calls)} / 許可: {sorted(RAW_TEXT_ALLOWED_IN)}"
    )
