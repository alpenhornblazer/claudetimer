"""
Claude Usage Widget
Small floating label next to the Windows system clock showing usage % and reset timer.
Uses the OAuth token from Claude Code's credentials file.
"""

import json
import logging
import os
import sys
import threading
import tkinter as tk
import traceback
from datetime import datetime, timezone

import requests

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "widget.log")
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("claude_widget")

POLL_INTERVAL = 180
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CREDENTIALS_PATH = os.path.join(os.environ["USERPROFILE"], ".claude", ".credentials.json")
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".widget.lock")

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "claude-code/2.1.50",
    "anthropic-beta": "oauth-2025-04-20",
}


def ensure_single_instance():
    """Exit if another instance is already running."""
    import msvcrt
    try:
        # Try to get an exclusive lock on the lock file
        lock_fh = open(LOCK_FILE, "w")
        msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
        return lock_fh  # keep reference alive
    except (OSError, IOError):
        log.info("Another instance is already running, exiting")
        sys.exit(0)


def get_access_token():
    with open(CREDENTIALS_PATH, "r") as f:
        creds = json.load(f)
    return creds.get("claudeAiOauth", {}).get("accessToken", "")


def fetch_usage(token):
    try:
        resp = requests.get(
            USAGE_URL,
            headers={**HEADERS, "Authorization": f"Bearer {token}"},
            timeout=15,
        )
        log.info(f"API status={resp.status_code}")
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            return "RATE_LIMITED"
        if resp.status_code == 401:
            return "AUTH_ERROR"
    except Exception as e:
        log.error(f"Fetch error: {e}")
        return "NETWORK_ERROR"
    return "SKIP"


def format_reset_time(iso_str):
    if not iso_str:
        return ""
    try:
        reset_dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        total_seconds = int((reset_dt - now).total_seconds())
        if total_seconds <= 0:
            return "0:00"
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}:{minutes:02d}"
    except Exception:
        return ""


def get_taskbar_rect():
    import ctypes
    import ctypes.wintypes

    class APPBARDATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("hWnd", ctypes.wintypes.HWND),
            ("uCallbackMessage", ctypes.c_uint),
            ("uEdge", ctypes.c_uint),
            ("rc", ctypes.wintypes.RECT),
            ("lParam", ctypes.wintypes.LPARAM),
        ]
    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(abd)
    ctypes.windll.shell32.SHAppBarMessage(5, ctypes.byref(abd))
    return abd.rc


class ClaudeWidget:
    def __init__(self):
        self.token = get_access_token()
        self.pct = None
        self.resets_at = ""
        self.status = ""  # error status to display
        self.backoff = 1  # multiplier for rate limit backoff

        self.root = tk.Tk()
        self.root.title("Claude Usage Widget")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.9)
        self.root.configure(bg="#1e1e2e")

        self.label = tk.Label(
            self.root, text="...",
            font=("Segoe UI", 10, "bold"),
            fg="#D4A574", bg="#1e1e2e",
            padx=6, pady=2,
        )
        self.label.pack()

        # Drag to reposition
        self.label.bind("<ButtonPress-1>", self._start_drag)
        self.label.bind("<B1-Motion>", self._on_drag)
        # Double-click to refresh
        self.label.bind("<Double-Button-1>", lambda e: self._async_update())

        self.menu = tk.Menu(self.root, tearoff=0, bg="#2e2e3e", fg="#D4A574",
                            activebackground="#3e3e4e", activeforeground="#ffffff")
        self.menu.add_command(label="Refresh", command=self._async_update)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self._quit)
        self.label.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y()  # keep vertical position fixed
        self.root.geometry(f"+{x}+{y}")
        self._save_position(x, y)

    def _save_position(self, x, y):
        pos_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".widget_pos")
        with open(pos_file, "w") as f:
            f.write(f"{x},{y}")

    def _load_position(self):
        pos_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".widget_pos")
        try:
            with open(pos_file, "r") as f:
                x, y = f.read().strip().split(",")
                return int(x), int(y)
        except Exception:
            return None

    def _position_near_clock(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        taskbar = get_taskbar_rect()
        x = taskbar.right - w - 300
        y = taskbar.top - h
        self.root.geometry(f"+{x}+{y}")

    def update_usage(self):
        try:
            self.token = get_access_token()
        except Exception:
            pass
        data = fetch_usage(self.token)

        if data == "RATE_LIMITED":
            self.backoff = min(self.backoff * 2, 10)  # double wait, max 30 min
            self.status = f"API busy, retry in {self.backoff * 3}m"
            log.info(f"Rate limited, backoff={self.backoff}")
            return
        if data == "AUTH_ERROR":
            self.status = "Token expired"
            log.info("Auth error")
            return
        if data == "NETWORK_ERROR":
            self.status = "Offline"
            log.info("Network error")
            return
        if data == "SKIP":
            return

        # Success — clear any error status and reset backoff
        self.status = ""
        self.backoff = 1
        if isinstance(data, dict):
            session = data.get("five_hour", {})
            self.pct = max(0, min(100, int(session.get("utilization", 0) + 0.5)))
            self.resets_at = session.get("resets_at", "")

    def _update_display(self):
        reset_str = format_reset_time(self.resets_at)

        if self.status and self.pct is None:
            # No data yet + error
            text = self.status
            color = "#9ca3af"
        elif self.pct is not None:
            text = f"{self.pct}% {reset_str}" if reset_str else f"{self.pct}%"
            if self.status:
                text += f"  [{self.status}]"
            color = "#D4A574"
        else:
            text = "Loading..."
            color = "#9ca3af"

        self.label.config(text=text, fg=color)

    def _async_update(self):
        def do_update():
            self.update_usage()
            self.root.after(0, self._update_display)
        threading.Thread(target=do_update, daemon=True).start()

    def _tick(self):
        try:
            self._update_display()
            self._async_update()
            # Re-assert topmost in case notifications pushed us behind
            self.root.attributes("-topmost", False)
            self.root.attributes("-topmost", True)
        except Exception:
            log.error(traceback.format_exc())
        wait = POLL_INTERVAL * self.backoff * 1000
        self.root.after(wait, self._tick)

    def _quit(self):
        self.root.destroy()

    def run(self):
        log.info("Widget starting")
        self.update_usage()
        log.info(f"Initial: {self.pct}% resets_at={self.resets_at} status={self.status}")
        self._update_display()
        saved = self._load_position()
        if saved:
            self.root.geometry(f"+{saved[0]}+{saved[1]}")
        else:
            self._position_near_clock()
        self.root.after(POLL_INTERVAL * 1000, self._tick)
        log.info("Entering mainloop")
        self.root.mainloop()
        log.info("Widget exited")


if __name__ == "__main__":
    try:
        # Set process name visible in Task Manager
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("Claude Usage Widget")
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Claude.Usage.Widget")

        _lock = ensure_single_instance()
        ClaudeWidget().run()
    except Exception:
        log.error(traceback.format_exc())
