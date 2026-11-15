"""Desktop notifications via notify-send (shown by your Serpantinum/quickshell notification daemon).
Clicking "Open" (or the notification, if the daemon supports default actions) focuses the app on Hyprland."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading

ICON = os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps/papertrader.png")


def _focus_app():
    if shutil.which("hyprctl"):
        subprocess.run(["hyprctl", "dispatch", "focuswindow", "class:papertrader"], capture_output=True, timeout=5)


def send(title: str, body: str = "", urgency: str = "normal", timeout_ms: int = 10000):
    exe = shutil.which("notify-send")
    if not exe:
        return False
    cmd = [exe, "-a", "Paper Trader", "-u", urgency, "-t", str(timeout_ms),
           "-h", "string:desktop-entry:papertrader", "-A", "default=Open"]
    if os.path.exists(ICON):
        cmd += ["-i", ICON, "-n", ICON]
    cmd += [title, body[:400]]

    def run():
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.stdout.strip() == "default":
                _focus_app()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=run, daemon=True).start()
    return True
