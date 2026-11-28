"""Vorhandene Cloud-Ordner zum Antippen (Nupplo für Windows 0.8.0).

Anlass: Sven will am 25.11.2026, dass die Windows-App auch mit iPhones und
Macs abgleicht, die iCloud nutzen. Das geht über „iCloud für Windows“, das
iCloud Drive unter `%USERPROFILE%\\iCloudDrive` ablegt. Den Pfad tippt
niemand ab, und wer ihn nicht kennt, findet ihn nicht – die App soll ihn
vorschlagen, und nur, wenn es ihn gibt.
"""
import main  # noqa: F401
import ordner  # noqa: E402


def test_icloud_drive_wird_zuerst_vorgeschlagen(tmp_path, monkeypatch):
    (tmp_path / "iCloudDrive").mkdir()
    (tmp_path / "OneDrive").mkdir()
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive"))
    v = ordner.vorschlaege()
    assert [x["name"] for x in v] == ["iCloud Drive", "OneDrive"]
    assert v[0]["pfad"] == str(tmp_path / "iCloudDrive")


def test_ohne_cloud_programm_kein_vorschlag(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    assert ordner.vorschlaege() == []
