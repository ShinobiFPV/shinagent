"""
ShinAgent HUD
=============
Desktop companion for Q2. Run from the imq2 project root:
    python hud/hud.py
    python hud/hud.py --port 8094 --q2 192.168.1.100
"""

import argparse
import threading
import sys
import os

# Ensure project root is on path so hud is a proper package -- os.path.abspath()
# first makes this robust to relative __file__ values (e.g. running `python
# hud.py` from inside the hud/ directory itself), which a bare
# os.path.dirname(os.path.dirname(__file__)) would resolve incorrectly for.
#
# Also removes Python's own auto-added sys.path[0] (this script's directory,
# imq2/hud) -- verified directly: since that directory contains a file
# literally named hud.py, "import hud" resolves to THIS FILE as the
# top-level hud module instead of the real imq2/hud/ package, no matter
# where the project root is inserted (a concrete module match wins over a
# namespace-package candidate regardless of insertion order). Only
# reproduces under `python hud/hud.py` -- `python -m hud.hud` never hits
# this, since -m adds the current working directory, not the script's own.
_hud_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_hud_dir)
sys.path = [p for p in sys.path if os.path.normcase(os.path.abspath(p) if p else os.getcwd()) != os.path.normcase(_hud_dir)]
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import webview


def parse_args():
    p = argparse.ArgumentParser(description="ShinAgent HUD")
    p.add_argument("--port", type=int, default=8094,
                   help="HUD server port (default 8094)")
    p.add_argument("--q2", default="192.168.1.100",
                   help="Q2 Pi IP address (default 192.168.1.100)")
    p.add_argument("--q2-port", type=int, default=8766,
                   help="Q2 webapp port (default 8766)")
    p.add_argument("--borderless", action="store_true",
                   help="Start in borderless (frameless) mode")
    p.add_argument("--ontop", action="store_true",
                   help="Start always on top")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("--demo", action="store_true",
                   help="Run in demo mode with spoofed data (no Q2 or game connection needed)")
    p.add_argument("--module", default="race_engineer",
                   choices=["race_engineer", "freeroam", "first_officer", "ship_computer",
                            "f1_watchalong", "ufc_watchalong", "popup_video", "whiplash",
                            "beavis_butthead", "circuit_builder", "retro"],
                   help="Demo module to simulate (default: race_engineer). Only used with --demo.")
    return p.parse_args()


class HudApi:
    """Python API exposed to JS via pywebview's js_api."""

    def __init__(self, window, args):
        self._window = window
        self._args = args

    def toggle_borderless(self):
        """
        Deliberately a no-op: pywebview doesn't support reliably switching
        a window's native frame after creation across its backends (Edge
        WebView2 on Windows, GTK/Qt elsewhere). The `--borderless` CLI flag
        controls the real OS-level frame at launch (frameless= in
        create_window below); the "borderless" toggle button in the HUD
        itself only changes CSS background transparency (see hud.js's
        toggleBorderless()), which is a real, always-available effect
        regardless of this limitation.
        """
        return False

    def toggle_always_on_top(self):
        self._window.on_top = not self._window.on_top
        return self._window.on_top

    def minimize(self):
        self._window.minimize()

    def close(self):
        self._window.destroy()

    def get_config(self):
        return {
            "q2_host": self._args.q2,
            "q2_port": self._args.q2_port,
            "hud_port": self._args.port,
            "platform": sys.platform,
            "is_windows": sys.platform == "win32",
        }

    def open_url(self, url):
        """Open a URL in the system default browser."""
        import webbrowser
        webbrowser.open(url)


def start_hud_server(args):
    """Start the Flask server (blocking) -- run in a background thread."""
    from hud.hud_server import create_app
    app = create_app(args)
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


def main():
    args = parse_args()

    server_thread = threading.Thread(target=start_hud_server, args=(args,), daemon=True)
    server_thread.start()

    # Delay for the Flask server to bind before pywebview tries to connect
    import time
    time.sleep(2.0)

    api = HudApi(None, args)
    window = webview.create_window(
        title=f"ShinAgent HUD [DEMO: {args.module}]" if args.demo else "ShinAgent HUD",
        url=f"http://127.0.0.1:{args.port}/",
        js_api=api,
        width=args.width,
        height=args.height,
        frameless=args.borderless,
        on_top=args.ontop,
        background_color="#00080a",
        min_size=(400, 300),
    )
    api._window = window

    webview.start(debug=False)


if __name__ == "__main__":
    main()
