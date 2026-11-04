"""Explicit, single-read local clipboard input for the terminal interface."""

from .core import RecoveryError, validate_url


def read_clipboard_url() -> str:
    """Read an HTTPS link without echoing, persisting, or changing the clipboard."""
    root = None
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        value = root.clipboard_get()
    except Exception:
        # Native clipboard errors can contain clipboard data. Keep them private.
        raise RecoveryError(
            "The local desktop clipboard is unavailable. Use the hidden terminal prompt or the GUI."
        ) from None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
    if not isinstance(value, str) or len(value) > 65536:
        raise RecoveryError("Copy one complete HTTPS download link to the local desktop clipboard.")
    return validate_url(value.strip())
