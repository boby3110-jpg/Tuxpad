"""README の「キーボードショートカット」表が、実際の割り当てと食い違って
いないか。

README のこの表は、**利用者が最初に読む場所**であり、実機での操作説明その
ものになっている。ところがショートカットを足したり付け替えたりしても表を
直し忘れれば、**誰も気づかないまま説明だけが古びる**。実際に
`Ctrl+M`（メニューバーを表示）は、設定メニューの説明文の中では触れられて
いるのに**この表には載っていなかった**（2026-08-19（41 回目）に発見）。
メニューバーを隠した利用者にとっては、この表こそが戻り方を探す場所なので、
いちばん載っていてほしいものが抜けていたことになる。

食い違うと困るのは「表を見て押したのに効かない」「使えるのに表に無くて
気づけない」の両方なので、**足りない側だけでなく、消えたのに残っている側も
見る**。

この環境での限界（正直に書いておく）
------------------------------------
ここで比べられるのは、あくまで **offscreen で解決されたキー**である。
`QKeySequence.StandardKey` は実機（KDE/xcb）で別のキーに解決されることが
あり、2026-08-07 には実際に「置換が `Ctrl+H` でなく `Ctrl+R` になる」不具合が
実機でだけ出た。つまり、**README が実機で正しいことまではここでは縛れない**。
その穴は `tests/test_menus.py::test_platform_dependent_standard_keys_are_never_used`
（実機で別キーになる `StandardKey` を使っていないことを構文木で見る）が
別に塞いでいる。この 2 つを合わせて初めて「表のとおりに実機で効く」に近づく。
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtGui import QAction

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"

#: 修飾キーの付かない、キーボード上の専用キー。``Ctrl+W`` に加えて Qt が
#: 標準で付けてくる ``Close`` キーのように、**普通のキーボードには無い**もの
#: まで表へ載せても利用者の役には立たないので、比較の対象から外す。
#: （``Ctrl+F4`` のような修飾キー付きは対象に含める。）
def _is_documentable(key: str) -> bool:
    return "+" in key


def _actions_in(menu) -> list[QAction]:
    found: list[QAction] = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        sub = action.menu()
        if sub is not None:
            found.extend(_actions_in(sub))
        else:
            found.append(action)
    return found


def bound_shortcuts(window) -> dict[str, str]:
    """メニューに出ている全アクションの、実際のショートカット → 項目名。"""
    bound: dict[str, str] = {}
    for top in window.menuBar().actions():
        menu = top.menu()
        if menu is None:
            continue
        for action in _actions_in(menu):
            for seq in action.shortcuts():
                key = seq.toString()
                if _is_documentable(key):
                    bound[key] = action.text().replace("&", "")
    return bound


def documented_shortcuts() -> set[str]:
    """README の「キーボードショートカット」表の、キーの列に出てくるキー。

    1 行に 2 つ書いてある（``Ctrl+W``（``Ctrl+F4``））こともあるので、
    その行のバッククォートで囲まれたものを全部拾う。
    """
    body = README.read_text(encoding="utf-8")
    section = body.split("## キーボードショートカット", 1)
    assert len(section) == 2, "README に「## キーボードショートカット」が無い"
    table = section[1].split("\n## ", 1)[0]

    keys: set[str] = set()
    for line in table.splitlines():
        if not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 3:
            continue
        keys.update(re.findall(r"`([^`]+)`", cells[1]))
    return keys


def test_readme_documents_every_shortcut(window) -> None:
    """実際に効くショートカットが、README の表に載っている。"""
    bound = bound_shortcuts(window)
    missing = sorted(set(bound) - documented_shortcuts())

    assert not missing, (
        "README の「キーボードショートカット」表に載っていないショートカット"
        f"がある: {[(key, bound[key]) for key in missing]}"
    )


def test_readme_documents_no_unbound_shortcut(window) -> None:
    """もう効かないショートカットが、README の表に残っていない。"""
    stale = sorted(documented_shortcuts() - set(bound_shortcuts(window)))

    assert not stale, (
        "README の「キーボードショートカット」表に、実際には割り当てられて"
        f"いないキーが載っている: {stale}"
    )


def test_readme_shortcut_table_is_not_empty() -> None:
    """表そのものを取り違えていない（見出しの改名などで空にならない）。

    上の 2 件は「両方とも空集合」でも緑になってしまうため、
    **表を読めていること自体**を別に固定しておく。
    """
    keys = documented_shortcuts()

    assert len(keys) >= 8, f"表から読めたキーが少なすぎる: {sorted(keys)}"
    assert "Ctrl+S" in keys
