"""Native Tk interface; all network and ZIP operations run off the UI thread."""

from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .controller import Job
from .core import Config, RecoveryError, validate_url


HELP = """1. Request an export through ChatGPT in your normal browser.
2. Copy the actual HTTPS file download link, then paste it here. An email landing page or a browser-only authenticated endpoint may not work. No login or cookies are read by this app.
3. Choose a NEW output ZIP filename. Existing files are never overwritten.
4. Optionally select a stopped browser partial (.part, .crdownload, or another regular file). Keep the browser download paused. The app reads and compares the entire prefix against the server before adopting it, so import re-downloads those bytes. The original file is never modified.
5. Start. Pause saves verified segments; paste the link again after reopening and choose the same output filename to resume. Use a fresh link for the SAME export if it expired.

Any browser can supply a direct link: Chrome, Edge, Firefox, Safari, Brave, Opera and others. The server must permit standalone HTTPS Range requests and supply a strong ETag. Browser cookies, login, anti-bot checks, encrypted browser stores and Safari .download folders are not imported.

ZIP entries are read for CRC checks, never extracted. SHA256 is shown at completion. Enter a trusted expected SHA256 only if you have one independently.

The .recovery folder contains private export data and URL-free integrity metadata. Keep it beside your output until finished. After completion the final ZIP and checkpoint share storage (hard links); editing either also edits the other. You can delete the recovery folder manually after closing the app, leaving the ZIP intact. Choose a private local NTFS/APFS/ext4 folder; FAT/exFAT and some network folders may not support publication.

No telemetry or uploads. The link stays in memory; your system HTTPS proxy configuration is honored. A masked input does not protect against other software running as you. Never share your export link, archive, or recovery folder."""


def size_text(value):
    return f"{value / (1024 ** 2):,.1f} MiB"


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"ChatGPT Export Recovery {__version__}")
        root.minsize(660, 540)
        root.geometry("790x650")
        self.job = Job()
        self.closing = False
        self.running = False
        self.started = 0
        self.first_offset = None
        self.url = tk.StringVar()
        self.output = tk.StringVar(value=str(Path.home() / "Downloads" / "chatgpt-export.zip"))
        self.source = tk.StringVar()
        self.sha = tk.StringVar()
        self.status = tk.StringVar(value="Ready. Your link stays on this device and is never saved to a settings file.")
        self.detail = tk.StringVar(value="")
        self.show_url = tk.BooleanVar(value=False)
        self.controls = []
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Recover your export", font=("TkDefaultFont", 22, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Resume large ZIP downloads safely. Works with direct links from any browser.",
                  wraplength=710).grid(row=1, column=0, sticky="w", pady=(8, 20))
        ttk.Label(frame, text="Direct HTTPS download link").grid(row=2, column=0, sticky="w")
        self.url_entry = ttk.Entry(frame, textvariable=self.url, show="•")
        self.url_entry.grid(row=3, column=0, sticky="ew", pady=(5, 2))
        self.controls.append(self.url_entry)
        show = ttk.Checkbutton(frame, text="Show link", variable=self.show_url,
                               command=lambda: self.url_entry.configure(show="" if self.show_url.get() else "•"))
        show.grid(row=4, column=0, sticky="w")
        self.controls.append(show)
        self._file_row(frame, 5, "Output ZIP (new filename, or same filename to resume)", self.output, self._choose_output)
        self._file_row(frame, 7, "Optional stopped browser partial (original is preserved)", self.source, self._choose_source, clear=True)
        ttk.Label(frame, text="Optional trusted expected SHA256").grid(row=9, column=0, sticky="w", pady=(12, 0))
        checksum = ttk.Entry(frame, textvariable=self.sha)
        checksum.grid(row=10, column=0, sticky="ew", pady=(5, 14))
        self.controls.append(checksum)
        buttons = ttk.Frame(frame)
        buttons.grid(row=11, column=0, sticky="ew")
        self.start = ttk.Button(buttons, text="Start / resume", command=self._start)
        self.start.pack(side="left")
        self.pause = ttk.Button(buttons, text="Pause", command=self._pause, state="disabled")
        self.pause.pack(side="left", padx=8)
        ttk.Button(buttons, text="How it works / privacy", command=self._help).pack(side="right")
        self.bar = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self.bar.grid(row=12, column=0, sticky="ew", pady=(22, 8))
        ttk.Label(frame, textvariable=self.status, wraplength=710).grid(row=13, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.detail, wraplength=710).grid(row=14, column=0, sticky="w", pady=(8, 0))
        frame.rowconfigure(15, weight=1)
        ttk.Label(frame, text="Unofficial utility • No browser access • No telemetry", foreground="#646464").grid(row=16, column=0, sticky="w", pady=(18, 0))
        self.url_entry.focus_set()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)

    def _file_row(self, frame, row, label, variable, choose, clear=False):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=(12, 0))
        line = ttk.Frame(frame)
        line.grid(row=row + 1, column=0, sticky="ew", pady=(5, 0))
        line.columnconfigure(0, weight=1)
        entry = ttk.Entry(line, textvariable=variable)
        entry.grid(row=0, column=0, sticky="ew")
        button = ttk.Button(line, text="Browse…", command=choose)
        button.grid(row=0, column=1, padx=(8, 0))
        self.controls.extend([entry, button])
        if clear:
            button = ttk.Button(line, text="Clear", command=lambda: variable.set(""))
            button.grid(row=0, column=2, padx=(8, 0))
            self.controls.append(button)

    def _choose_output(self):
        value = filedialog.asksaveasfilename(parent=self.root, title="Choose a NEW output filename", defaultextension=".zip",
                                            filetypes=[("ZIP archive", "*.zip")], initialfile="chatgpt-export.zip")
        if value:
            self.output.set(value)

    def _choose_source(self):
        value = filedialog.askopenfilename(parent=self.root, title="Select a paused browser partial file",
                                           filetypes=[("All files", "*")])
        if value:
            self.source.set(value)

    def _help(self):
        window = tk.Toplevel(self.root)
        window.title("How it works / privacy")
        window.geometry("720x580")
        text = tk.Text(window, wrap="word", padx=18, pady=18)
        scrollbar = ttk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", HELP)
        text.configure(state="disabled")
        ttk.Button(window, text="Close", command=window.destroy).pack(pady=8)

    def _set_running(self, running):
        self.running = running
        self.start.configure(state="disabled" if running else "normal")
        self.pause.configure(state="normal" if running else "disabled")
        for control in self.controls:
            control.configure(state="disabled" if running else "normal")

    def _start(self):
        if self.running or self.job.busy:
            return
        try:
            link = validate_url(self.url.get().strip())
        except RecoveryError as error:
            messagebox.showerror("Check download link", str(error), parent=self.root)
            return
        if not self.output.get().strip():
            messagebox.showerror("Choose output", "Select a new ZIP filename first.", parent=self.root)
            return
        config = Config(link, Path(self.output.get()), Path(self.source.get()) if self.source.get() else None,
                        self.sha.get())
        self.started = time.monotonic()
        self.first_offset = None
        self.detail.set("")
        self.bar["value"] = 0
        self._set_running(True)
        self.job.start(config)

    def _pause(self):
        self.job.pause()
        self.pause.configure(state="disabled")
        self.status.set("Pausing after the current network read (up to the connection timeout)…")

    def _close(self):
        if self.running or self.job.busy:
            if messagebox.askyesno("Pause and close?", "Verified bytes will be saved. Pause this download and close?", parent=self.root):
                self.closing = True
                self._pause()
        else:
            self.url.set("")
            self.root.destroy()

    def _poll(self):
        try:
            while True:
                kind, value = self.job.events.get_nowait()
                if kind == "progress":
                    percent = value.completed * 100 / value.total if value.total else 0
                    self.bar["value"] = percent
                    self.status.set(value.message or value.phase.capitalize())
                    if value.total:
                        detail = f"{size_text(value.completed)} / {size_text(value.total)} ({percent:.1f}%)"
                        if value.phase in ("importing", "downloading"):
                            if self.first_offset is None:
                                self.first_offset = value.completed
                                self.started = time.monotonic()
                            elapsed = time.monotonic() - self.started
                            speed = (value.completed - self.first_offset) / elapsed if elapsed else 0
                            if speed > 0:
                                detail += f" • {size_text(speed)}/s • ~{(value.total - value.completed) / speed / 60:.1f} min left"
                        self.detail.set(detail)
                else:
                    self._set_running(False)
                    if kind == "complete":
                        self.url.set("")
                        self.status.set("Complete. ZIP integrity verified.")
                        self.detail.set(f"SHA256: {value.sha256}\nSaved: {value.output}")
                    else:
                        self.status.set(value)
        except queue.Empty:
            pass
        if self.closing and not self.job.busy:
            self.url.set("")
            self.root.destroy()
            return
        self.root.after(100, self._poll)


def main():
    try:
        root = tk.Tk()
    except tk.TclError:
        print("The graphical display could not be opened. Run on a desktop, or use --cli.")
        return 1
    App(root)
    root.mainloop()
    return 0
