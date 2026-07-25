"""Manual MTP smoke check against a real phone over USB.

    uv run python scripts/mtp_smoke.py

Finds the phone's TeamsTranscriber folder, lists the three contract dirs
(outbox/library/sync), round-trips a probe file through sync/, and prints
timings. Exits non-zero with the MtpNotReady hint when the phone isn't
ready (locked, USB in charge-only mode, WPDBusEnum stopped, no marker, ...).
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from teams_transcriber.phone_sync.mtp import (
    MtpNotReady,
    MtpStaleSession,
    MtpTransport,
    find_phone_root,
)


def main() -> int:
    # Manual scripts run on the main thread, but CoInitialize is idempotent
    # (S_FALSE if already inited) and this is the only thing that makes the
    # async CopyHere/MoveHere ops advance (spike doc B1, device-verified).
    import pythoncom

    pythoncom.CoInitialize()

    try:
        root = find_phone_root()
    except MtpNotReady as exc:
        print(f"not ready ({exc.reason}): {exc.hint}", file=sys.stderr)
        return 1

    t = MtpTransport(root)
    try:
        for prefix in ("outbox", "library", "sync"):
            files = t.list_files(prefix)
            print(f"{prefix}: {len(files)} file(s)")
            for f in files:
                print(f"  {f.name}  ({f.size} bytes)")

        probe_name = "sync/_mtp_smoke_probe.txt"
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "probe.txt"
            src.write_text("mtp smoke test", encoding="utf-8")

            t0 = time.monotonic()
            t.push(src, probe_name)
            print(f"push: {time.monotonic() - t0:.3f}s")

            t0 = time.monotonic()
            dest = Path(d) / "probe_pulled.txt"
            t.pull(probe_name, dest)
            print(f"pull: {time.monotonic() - t0:.3f}s  content={dest.read_text()!r}")

            t0 = time.monotonic()
            t.delete(probe_name)
            print(f"delete: {time.monotonic() - t0:.3f}s")
    except MtpStaleSession as exc:
        print(f"stale session: {exc}", file=sys.stderr)
        return 1
    finally:
        t.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
