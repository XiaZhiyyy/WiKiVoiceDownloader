# -*- mode: python ; coding: utf-8 -*-
# Onedir, console-enabled. Run this spec on Windows to produce a Windows EXE.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

project = Path(SPECPATH)
a = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    binaries=[],
    datas=(collect_data_files("certifi") +
           [(str(path), "wiki_voice_downloader/site_profiles")
            for path in (project / "wiki_voice_downloader" / "site_profiles").glob("*.json")]),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests", "tkinter", "numpy", "torch", "soundfile", "mutagen"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="WikiVoiceDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name="WikiVoiceDownloader")
