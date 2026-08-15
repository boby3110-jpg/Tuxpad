"""コマンドライン引数でのファイルオープン（Dolphin 等の「開く」連携）のテスト."""

from __future__ import annotations

from pathlib import Path

from editor_app.app import _resolve_file_arguments
from editor_app.main_window import MainWindow


def test_resolve_file_arguments_passes_plain_paths_through() -> None:
    assert _resolve_file_arguments(["/tmp/a.txt", "/tmp/b.txt"]) == [
        "/tmp/a.txt",
        "/tmp/b.txt",
    ]


def test_resolve_file_arguments_converts_file_uri() -> None:
    assert _resolve_file_arguments(["file:///tmp/a.txt"]) == ["/tmp/a.txt"]


def test_resolve_file_arguments_mixes_uri_and_plain_paths() -> None:
    result = _resolve_file_arguments(["file:///tmp/a.txt", "/tmp/b.txt"])
    assert result == ["/tmp/a.txt", "/tmp/b.txt"]


def test_resolve_file_arguments_empty_list_is_empty() -> None:
    assert _resolve_file_arguments([]) == []


def test_window_opens_files_resolved_from_argv(
    window: MainWindow, tmp_path: Path
) -> None:
    """Dolphin から %U 経由で渡ってきたパスが、無題タブでなく実際に開かれる。

    実機バグ: .desktop の Exec=... %U で渡した引数が main() で捨てられ、
    常に空の無題タブが開いてしまっていた。
    """
    sample = tmp_path / "sample.txt"
    sample.write_text("あいうえお\n", encoding="utf-8")

    paths = _resolve_file_arguments([str(sample)])
    opened = window.open_paths(paths)

    assert len(opened) == 1
    assert opened[0].toPlainText() == "あいうえお\n"
    assert opened[0].path == sample.resolve()
