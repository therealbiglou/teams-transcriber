"""Module entry point.

With no args, launches the desktop UI (`teams_transcriber.ui.app.main`).
With args, dispatches to the CLI (`teams_transcriber.cli.main`) — exposes
`serve`, `list`, `retry-summary`, `smoke-test`, `ui` subcommands.

This dual-mode dispatch is what makes the frozen .exe usable for both the
default GUI launch and the build script's `smoke-test` invocation.
"""

import sys


def _bootstrap_gpu_runtime() -> bool:
    """If the runtime isn't installed, let the wizard handle it (UI mode)
    or exit cleanly (CLI mode).

    Returns True if the caller should proceed with normal app start.
    """
    import sys
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.runtime.gpu_runtime import (
        is_runtime_installed,
        register_runtime,
    )

    paths = AppPaths()
    paths.ensure_dirs()
    runtime_base = paths.runtime_dir / "nvidia"
    if is_runtime_installed(runtime_base):
        register_runtime(runtime_base)
        return True

    # smoke-test is a build-time verification — it must succeed without the
    # GPU runtime (the build machine doesn't necessarily have one cached).
    if len(sys.argv) > 1 and sys.argv[1] in {"serve", "retry-summary"}:
        print(
            "GPU runtime not installed. Launch the GUI once to set it up "
            "(it'll download ~700 MB of NVIDIA libraries).",
            file=sys.stderr,
        )
        return False
    return True  # UI mode + smoke-test fall through.


def main() -> int:
    if not _bootstrap_gpu_runtime():
        return 2

    if len(sys.argv) > 1:
        from teams_transcriber.cli import main as _main
    else:
        from teams_transcriber.ui.app import main as _main

    return _main()


sys.exit(main())
