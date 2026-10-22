"""Thread-safe GUI controller, independently testable without a display."""

import queue
import threading

from .core import Cancelled, Config, Downloader, RecoveryError


class Job:
    def __init__(self):
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.thread = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, config: Config, *, transport=None):
        if self.busy:
            return False
        self.cancel_event = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(config, transport), daemon=False)
        self.thread.start()
        return True

    def pause(self):
        self.cancel_event.set()

    def _run(self, config, transport):
        try:
            result = Downloader(config, transport=transport, cancel=self.cancel_event,
                                progress=lambda value: self.events.put(("progress", value))).run()
            self.events.put(("complete", result))
        except Cancelled as error:
            self.events.put(("paused", str(error)))
        except RecoveryError as error:
            self.events.put(("error", str(error)))
        except Exception:
            # Exception repr/traceback can contain the signed URL or server input.
            self.events.put(("error", "Unexpected recovery failure. Verified checkpoint data was retained. Close and restart the app."))
