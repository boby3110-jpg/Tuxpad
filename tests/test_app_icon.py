"""アプリアイコンの読み込みのテスト（2026-08-04 実機フィードバックで追加）。"""

from __future__ import annotations

from editor_app.app import _ICON_SIZES, _RESOURCES_DIR, _load_app_icon


def test_load_app_icon_is_not_null(qtbot) -> None:
    # QIcon の読み込みには QApplication が必要（qtbot がそれを保証する）。
    icon = _load_app_icon()
    assert not icon.isNull()


def test_load_app_icon_has_all_expected_sizes(qtbot) -> None:
    icon = _load_app_icon()
    available = {(s.width(), s.height()) for s in icon.availableSizes()}
    expected = {(size, size) for size in _ICON_SIZES}
    assert available == expected


def test_icon_sizes_are_the_eight_declared_sizes() -> None:
    """サイズの一覧を、**定数を経由せずに**書き出して縛る。

    上の :func:`test_load_app_icon_has_all_expected_sizes` は期待値を
    ``_ICON_SIZES`` から組み立てているので、**定数そのものを削っても
    緑のまま**通り抜ける（84 回目に実測。``(512,)`` の 1 つだけに
    縮めても全件緑だった）。

    小さいサイズが落ちると、タスクバーやタイトルバーの 16/24px の
    アイコンが大きな画像の縮小で描かれ、実機では「アイコンがぼやける」
    という見え方になる（アイコンの見た目は 2026-08-04 の実機
    フィードバックで直した所なので、黙って戻らないようにしておく）。
    画像はリポジトリに置いてある実ファイルなので、**一覧と実ファイルの
    両方**を見る（一覧だけ増やしても、読み込みは
    ``path.exists()`` で黙って飛ばされる）。
    """
    assert _ICON_SIZES == (16, 24, 32, 48, 64, 128, 256, 512)
    missing = [
        size for size in _ICON_SIZES if not (_RESOURCES_DIR / f"icon-{size}.png").exists()
    ]
    assert missing == []
