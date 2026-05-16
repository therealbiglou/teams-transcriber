"""Build Teams Transcriber installer end-to-end.

Steps:
  1. Read app version from pyproject.toml.
  2. Clean dist/ and build/.
  3. Run PyInstaller against teams_transcriber.spec.
  4. Smoke-test the frozen .exe.
  5. Find ISCC.exe and compile the Inno Setup script.
  6. (Optional) Sign the installer if TT_SIGN_CERT_PATH is set.
  7. Report final installer path + size.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def app_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        pp = tomllib.load(f)
    return str(pp["project"]["version"])


def find_iscc() -> Path:
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        "ISCC.exe not found. Install Inno Setup 6 from "
        "https://jrsoftware.org/isdl.php (free, MIT licensed)."
    )


def step(name: str) -> None:
    print(f"\n=== {name} ===", flush=True)


def run(cmd: list[str], **kw) -> None:
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.check_call(cmd, **kw)


def main() -> int:
    version = app_version()
    print(f"Building Teams Transcriber {version}")

    step("Clean")
    for d in ("dist", "build"):
        p = REPO_ROOT / d
        if p.exists():
            shutil.rmtree(p)
            print(f"removed {p}")

    step("PyInstaller")
    run(
        [sys.executable, "-m", "PyInstaller", "teams_transcriber.spec",
         "--noconfirm", "--log-level", "WARN"],
        cwd=REPO_ROOT,
    )

    step("Smoke-test")
    exe = REPO_ROOT / "dist" / "TeamsTranscriber" / "TeamsTranscriber.exe"
    if not exe.is_file():
        raise SystemExit(f"PyInstaller did not produce {exe}")
    run([str(exe), "smoke-test"])

    step("Inno Setup")
    iscc = find_iscc()
    run(
        [str(iscc), f"/DAppVersion={version}",
         str(REPO_ROOT / "installer" / "teams-transcriber.iss")],
        cwd=REPO_ROOT / "installer",
    )

    step("Sign (optional)")
    cert = os.environ.get("TT_SIGN_CERT_PATH")
    pw = os.environ.get("TT_SIGN_CERT_PASSWORD", "")
    installer = REPO_ROOT / "dist" / f"TeamsTranscriberSetup-{version}.exe"
    if cert:
        run([
            "signtool", "sign",
            "/f", cert, "/p", pw,
            "/tr", "http://timestamp.digicert.com",
            "/td", "sha256", "/fd", "sha256",
            str(installer),
        ])
    else:
        print("(skipped — set TT_SIGN_CERT_PATH to sign)")

    step("Done")
    size_mb = installer.stat().st_size / (1024 * 1024)
    print(f"Installer: {installer}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
