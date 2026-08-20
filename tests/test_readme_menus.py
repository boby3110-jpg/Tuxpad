"""メニューに出ている項目が、README のどこかで説明されているか。

利用者から見える機能は**ほぼ全てメニューにある**。項目を足したのに README を
直し忘れると、**その機能は存在するのに誰にも知られないまま**になる（実機で
使う人は README を読んで覚えるので、書かれていない機能は無いのと同じ）。

「表のどこに書くか」までは縛らない。README には項目ごとの表
（「表示メニュー」「設定メニュー」）と、まとまった説明の節
（「文字コード」「バックアップから復元」など）の両方があり、**どちらで
説明してもよい**からである。ここで見るのは「**名前がどこにも出てこない**」
＝説明し忘れ、だけ。

`tests/test_readme_shortcuts.py`（キーの表）・`tests/test_readme_structure.py`
（ディレクトリ構成の一覧）と合わせて、README がコードから置き去りにされて
いないかを機械的に見張る 3 つ目になる。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"

#: 名前が README に出てこなくてよい項目と、その理由。
#:
#: どれも**親の項目でまとめて説明してある**もの。「エディタの配色」の説明
#: （設定メニューの表）が「本文の背景色・文字色をテーマとは別に固定」まで
#: 書いているので、その下の 3 つを個別に並べても読む人の役には立たない。
#:
#: ここへ足すときは「親で説明済みか」を必ず確かめること。**書き忘れた項目を
#: 黙らせるために足すと、この見張りそのものが意味を失う。**
UNDOCUMENTED_BY_DESIGN = {
    "背景色を選択": "設定メニューの「エディタの配色」でまとめて説明している",
    "文字色を選択": "同上",
    "テーマに合わせる（リセット）": "同上",
}


def menu_item_names(window) -> dict[str, str]:
    """メニューに出ている項目の「説明を探すときの名前」→ 生のラベル。

    ラベルの ``&``（下線を引く位置）・末尾の ``...``・末尾の ``(&B)`` を
    落とし、README に書くときの見出しの形へ揃える。
    """

    def walk(menu) -> list[QAction]:
        found: list[QAction] = []
        for action in menu.actions():
            if action.isSeparator():
                continue
            sub = action.menu()
            if sub is not None:
                found.extend(walk(sub))
            found.append(action)
        return found

    names: dict[str, str] = {}
    for top in window.menuBar().actions():
        menu = top.menu()
        if menu is None:
            continue
        for action in walk(menu):
            label = action.text()
            plain = label.replace("&", "").rstrip(".")
            if "(" in plain:
                plain = plain.split("(")[0].strip()
            if plain:
                names[plain] = label
    return names


def test_every_menu_item_is_mentioned_in_readme(window) -> None:
    """メニューの項目名が README のどこかに出てくる。"""
    body = README.read_text(encoding="utf-8")
    names = menu_item_names(window)

    missing = sorted(
        name
        for name in names
        if name not in UNDOCUMENTED_BY_DESIGN and name not in body
    )

    assert not missing, (
        "メニューに出ているのに README のどこにも書かれていない項目がある: "
        f"{[(name, names[name]) for name in missing]}"
    )


def test_no_stale_documentation_exemption(window) -> None:
    """説明を省いてよい項目の一覧が、実際のメニューと合っている。

    項目を消したり改名したりすると、``UNDOCUMENTED_BY_DESIGN`` に**名前だけが
    取り残される**。取り残された名前は何も免除していないのに、次に同じ名前の
    項目を足したとき**黙って見逃す**ので、その場で気づけるようにしておく。
    """
    names = menu_item_names(window)
    stale = sorted(set(UNDOCUMENTED_BY_DESIGN) - set(names))

    assert not stale, (
        "UNDOCUMENTED_BY_DESIGN に、もうメニューに無い項目が残っている: "
        f"{stale}"
    )


def test_menu_items_are_actually_collected(window) -> None:
    """項目を集められていること自体を固定する。

    上の 2 件は「1 つも集められていない」でも緑になってしまう。
    """
    names = menu_item_names(window)

    assert len(names) >= 20, f"集めた項目が少なすぎる: {sorted(names)}"
    assert "上書き保存" in names
