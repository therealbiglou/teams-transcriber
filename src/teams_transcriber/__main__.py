"""Module entry point.

With no args, launches the desktop UI (`teams_transcriber.ui.app.main`).
With args, dispatches to the CLI (`teams_transcriber.cli.main`) — exposes
`serve`, `list`, `retry-summary`, `smoke-test`, `ui` subcommands.

This dual-mode dispatch is what makes the frozen .exe usable for both the
default GUI launch and the build script's `smoke-test` invocation.
"""

import sys

if len(sys.argv) > 1:
    from teams_transcriber.cli import main as _main
else:
    from teams_transcriber.ui.app import main as _main

sys.exit(_main())
