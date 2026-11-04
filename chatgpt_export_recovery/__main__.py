"""Launch with python -m chatgpt_export_recovery; no pip packages required."""

import argparse
from contextlib import ExitStack
import getpass
from pathlib import Path
import sys
import threading
import warnings

from . import __version__
from .core import Cancelled, Config, Downloader, RecoveryError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recover a ChatGPT export with the native GUI or a hidden-prompt CLI.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--cli", action="store_true", help="Use a terminal instead of the GUI")
    parser.add_argument("--output", type=Path, help="New output ZIP path (CLI only)")
    parser.add_argument("--import-partial", type=Path, help="Read and verify a stopped browser partial (CLI only)")
    parser.add_argument("--sha256", default="", help="Optional trusted expected SHA256 (CLI only)")
    parser.add_argument("--firefox-profile", type=Path,
                        help="Use an existing local Linux Firefox ChatGPT session in memory (CLI only)")
    parser.add_argument("--chunk-mib", type=int, choices=(8, 16, 32, 64),
                        help="Range segment size in MiB (CLI only; default 8)")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--url-from-clipboard", action="store_true",
                        help="Read a direct HTTPS link once from the local desktop clipboard (CLI only; Tk required)")
    inputs.add_argument("--url-from-browser", action="store_true",
                        help="Accept one direct HTTPS link through a localhost-only browser form (CLI only)")
    args = parser.parse_args(argv)
    if not args.cli:
        if args.output or args.import_partial or args.sha256 or args.url_from_clipboard or args.url_from_browser or args.firefox_profile or args.chunk_mib:
            parser.error("Use --cli with download options; configure GUI downloads in the window.")
        try:
            from .gui import main as gui_main
        except ImportError:
            print("Tkinter is unavailable in this Python. Use a desktop Python with Tk support, or run --cli.", file=sys.stderr)
            return 1
        return gui_main()
    if not args.output:
        parser.error("--cli requires --output")
    if not (args.url_from_clipboard or args.url_from_browser) and not sys.stdin.isatty():
        print("Use an interactive terminal for the hidden URL prompt. URL command-line arguments are intentionally unsupported.", file=sys.stderr)
        return 2
    cancel = threading.Event()
    try:
        if args.url_from_browser:
            from .local_input import read_browser_url
            url = read_browser_url()
        elif args.url_from_clipboard:
            from .clipboard import read_clipboard_url
            url = read_clipboard_url()
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                url = getpass.getpass("Direct HTTPS download link (hidden): ").strip()
        with ExitStack() as session:
            options = {}
            if args.firefox_profile:
                from .firefox_session import FirefoxSessionTransport
                options["transport"] = session.enter_context(FirefoxSessionTransport(args.firefox_profile))
            result = Downloader(Config(url, args.output, args.import_partial, args.sha256,
                                       chunk_size=(args.chunk_mib or 8) * 1024 * 1024), cancel=cancel,
                                progress=lambda event: print(f"{event.phase}: {event.completed}/{event.total} {event.message}"),
                                **options).run()
    except getpass.GetPassWarning:
        print("Hidden terminal input is unavailable. Use the GUI instead.", file=sys.stderr)
        return 2
    except (Cancelled, RecoveryError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        cancel.set()
        print("Stopped. Resume using the same output filename.", file=sys.stderr)
        return 130
    print(f"Verified ZIP saved: {result.output}\nSHA256: {result.sha256}")
    return 0


def cli_main(argv=None):
    """Console-script entry point: terminal mode without requiring --cli."""
    arguments = sys.argv[1:] if argv is None else argv
    return main(["--cli", *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
