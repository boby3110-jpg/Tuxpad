"""アプリアイコンの読み込みのテスト（2026-08-04 実機フィードバックで追加）。"""

from __future__ import annotations

from editor_app.app import _ICON_SIZES, _load_app_icon


def test_load_app_icon_is_not_null(qtbot) -> None:
    # QIcon の読み込みには QApplication が必要（qtbot がそれを保証する）。
    icon = _load_app_icon()
    assert not icon.isNull()


def test_load_app_icon_has_all_expected_sizes(qtbot) -> None:
    icon = _load_app_icon()
    available = {(s.width(), s.height()) for s in icon.availableSizes()}
    expected = {(size, size) for size in _ICON_SIZES}
    assert available == expected
