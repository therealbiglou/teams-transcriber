# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Teams Transcriber.

Build via the orchestrator:
    python scripts/build_installer.py

Or directly:
    pyinstaller teams_transcriber.spec --noconfirm
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

REPO_ROOT = Path(SPECPATH)
SITE_PACKAGES = REPO_ROOT / ".venv" / "Lib" / "site-packages"


def _is_nvidia_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    return p.startswith("nvidia/") or "/nvidia/" in p


# Collect every dep that ships native binaries or data files.
av_datas, av_binaries, av_hidden = collect_all("av")
ct2_datas, ct2_binaries, ct2_hidden = collect_all("ctranslate2")
sd_datas, sd_binaries, sd_hidden = collect_all("soundcard")
fw_datas, fw_binaries, fw_hidden = collect_all("faster_whisper")

extra_hidden = [
    "keyring.backends.Windows",
    "win32timezone",
    *collect_submodules("anthropic"),
    *collect_submodules("keyboard"),
]

a = Analysis(
    [str(REPO_ROOT / "src" / "teams_transcriber" / "__main__.py")],
    pathex=[str(REPO_ROOT / "src")],
    binaries=av_binaries + ct2_binaries + sd_binaries + fw_binaries,
    datas=av_datas + ct2_datas + sd_datas + fw_datas,
    hiddenimports=[
        *av_hidden, *ct2_hidden, *sd_hidden, *fw_hidden, *extra_hidden,
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

a.binaries = [b for b in a.binaries if not _is_nvidia_path(b[0])]
a.datas    = [d for d in a.datas    if not _is_nvidia_path(d[0])]

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TeamsTranscriber",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(REPO_ROOT / "installer" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TeamsTranscriber",
)
