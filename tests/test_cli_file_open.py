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


def test_resolve_file_arguments_drops_file_uri_without_a_path() -> None:
    """ローカルパスに直せない ``file://`` は、空文字を混ぜずに捨てる。

    ``QUrl("file://").toLocalFile()`` は空文字を返す。ここで捨てそこねると
    **空文字がファイルパスとして後段へ流れる**——`_handle_forwarded_paths()`
    はそれを `open_path("")` まで運んでしまい、開けるはずの残りのファイルの
    前に無関係なエラーが出る。`if local_path:` の 1 行がそれを防いでいるが、
    34 回目まで、この行を外してもテストは全て緑のままだった（変異
    `app-uri-empty-guard`）。
    """
    assert _resolve_file_arguments(["file://"]) == []


def test_resolve_file_arguments_keeps_valid_paths_beside_a_broken_uri() -> None:
    """壊れた ``file://`` が混ざっていても、まともなファイルは残す。"""
    assert _resolve_file_arguments(["file://", "/tmp/b.txt"]) == ["/tmp/b.txt"]


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
