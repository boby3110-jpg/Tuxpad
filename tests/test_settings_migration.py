"""旧名称(Tabula)→新名称(Tuxpad)の設定移行のテスト（改名対応、2026-08-10）。

``migrate_legacy_settings`` は ``QSettings()``（新）と
``QSettings("Tabula", "Tabula")``（旧）を使い分けるので、テストでも
その 2 つを別々の ini ファイルへ振り分けて、コピー・冪等・非破壊を固定する。
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

from editor_app import settings as settings_mod


def _router(tmp_path):
    """引数に応じて新／旧で別々の ini を返す QSettings ファクトリ。"""
    new_ini = str(tmp_path / "new.ini")
    legacy_ini = str(tmp_path / "legacy.ini")

    def factory(*args, **kwargs):
        if tuple(args[:2]) == (settings_mod._LEGACY_APP_NAME, settings_mod._LEGACY_APP_NAME):
            return QSettings(legacy_ini, QSettings.Format.IniFormat)
        return QSettings(new_ini, QSettings.Format.IniFormat)

    return factory, new_ini, legacy_ini


def _write(ini: str, key: str, value) -> None:
    store = QSettings(ini, QSettings.Format.IniFormat)
    store.setValue(key, value)
    store.sync()


def _read(ini: str, key: str):
    return QSettings(ini, QSettings.Format.IniFormat).value(key)


def test_migrates_legacy_settings_when_new_is_empty(tmp_path, monkeypatch) -> None:
    factory, new_ini, legacy_ini = _router(tmp_path)
    _write(legacy_ini, "appearance/theme", "dark")
    monkeypatch.setattr("editor_app.settings.QSettings", factory)

    settings_mod.migrate_legacy_settings()

    assert _read(new_ini, "appearance/theme") == "dark"


def test_does_not_overwrite_existing_new_settings(tmp_path, monkeypatch) -> None:
    """新設定が既にあれば、旧設定で上書きしない（＝一度きりの移行）。"""
    factory, new_ini, legacy_ini = _router(tmp_path)
    _write(legacy_ini, "appearance/theme", "dark")
    _write(new_ini, "appearance/theme", "light")
    monkeypatch.setattr("editor_app.settings.QSettings", factory)

    settings_mod.migrate_legacy_settings()

    assert _read(new_ini, "appearance/theme") == "light"


def test_noop_when_no_legacy_settings(tmp_path, monkeypatch) -> None:
    """旧設定が無ければ何もしない（例外も出ない）。"""
    factory, new_ini, _legacy_ini = _router(tmp_path)
    monkeypatch.setattr("editor_app.settings.QSettings", factory)

    settings_mod.migrate_legacy_settings()

    assert QSettings(new_ini, QSettings.Format.IniFormat).allKeys() == []
