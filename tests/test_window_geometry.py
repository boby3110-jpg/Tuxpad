"""ウィンドウ位置・サイズの永続化のテスト（実機フィードバックにより追加）。"""

from __future__ import annotations

from editor_app.main_window import MainWindow
from editor_app.settings import load_window_geometry


def test_no_saved_geometry_uses_default_size(window: MainWindow) -> None:
    assert load_window_geometry() is None
    assert window.size().width() == 1000
    assert window.size().height() == 700


def test_close_event_saves_geometry(window: MainWindow) -> None:
    window.resize(640, 480)
    window.move(50, 60)

    window.close()

    saved = load_window_geometry()
    assert saved is not None


def test_saved_geometry_is_restored_in_new_window(
    window: MainWindow, make_window
) -> None:
    window.resize(640, 480)
    window.close()

    restored = make_window()

    assert restored.size().width() == 640
    assert restored.size().height() == 480


def test_empty_saved_geometry_is_treated_as_absent() -> None:
    """空の位置情報が保存されていたら「無い」ものとして扱うこと。

    ``load_window_geometry`` の約束は「保存されていなければ None」で、
    呼び出し側 (``MainWindow.__init__``) はその約束に乗って
    ``is not None`` だけを見ている。空の ``QByteArray`` をそのまま
    返すと、その約束が破れる（``restoreGeometry`` は空を渡されても
    何もせず False を返すので**画面上の見え方は今のところ変わらない**が、
    「値があるなら復元できる」という前提が崩れる）。
    """
    from PySide6.QtCore import QByteArray

    from editor_app.settings import save_window_geometry

    save_window_geometry(QByteArray())

    assert load_window_geometry() is None
