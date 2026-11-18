"""
window.py — show the instrument UI in a native-feeling window on Windows, macOS, Linux and Raspberry Pi.

Order of preference (override with --window):
  1. pywebview      native window (Edge WebView2 on Windows, WKWebView on macOS, GTK/Qt WebKit on Linux)
  2. chromium app   Chrome / Edge / Chromium started with --app (no tabs / address bar) and its own
                    profile in the data dir; with --kiosk it is full-screen with no exit UI (Raspberry Pi)
  3. browser        the default browser, last resort
On a Raspberry Pi, Chromium kiosk is preferred over pywebview: it is GPU-accelerated and
pre-installed on Raspberry Pi OS, whereas WebKitGTK canvas rendering is noticeably slower there.
"""
import os, shutil, subprocess, sys, webbrowser


def _chromium_candidates():
    if os.name == "nt":
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        rel = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
               r"Chromium\Application\chrome.exe", r"BraveSoftware\Brave-Browser\Application\brave.exe"]
        return [os.path.join(r, p) for r in roots if r for p in rel]
    if sys.platform == "darwin":
        apps = ["Google Chrome", "Microsoft Edge", "Chromium", "Brave Browser"]
        out = []
        for a in apps:
            for base in ("/Applications", os.path.expanduser("~/Applications")):
                out.append(f"{base}/{a}.app/Contents/MacOS/{a}")
        return out
    names = ["chromium-browser", "chromium", "google-chrome", "google-chrome-stable", "microsoft-edge", "brave-browser"]
    return [p for p in (shutil.which(n) for n in names) if p]


def find_chromium():
    for p in _chromium_candidates():
        if p and os.path.isfile(p):
            return p
    return None


def have_pywebview():
    try:
        import webview  # noqa: F401
        return True
    except Exception:
        return False


def open_chromium(url, profile_dir, kiosk=False, fullscreen=False, pi=False):
    exe = find_chromium()
    if not exe:
        return None
    os.makedirs(profile_dir, exist_ok=True)
    args = [exe, f"--app={url}", f"--user-data-dir={profile_dir}", "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate,MediaRouter", "--disable-session-crashed-bubble",
            "--overscroll-history-navigation=0", "--autoplay-policy=no-user-gesture-required",
            "--window-size=1600,900"]
    if kiosk:
        args += ["--kiosk", "--noerrdialogs", "--disable-infobars", "--check-for-update-interval=31536000"]
    elif fullscreen:
        args += ["--start-fullscreen"]
    if pi:
        # Raspberry Pi: keep GPU raster on, avoid the keyring prompt, quieter on small SD cards
        args += ["--enable-gpu-rasterization", "--ignore-gpu-blocklist", "--password-store=basic",
                 "--disk-cache-size=10000000"]
    try:
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


class WindowApi:
    """Exposed to the page as window.pywebview.api.  The HTML Fullscreen API is disabled in macOS WKWebView and
    in Windows WebView2 only fills the web area, so the display asks the native window to go full screen."""

    def __init__(self, fullscreen=False):
        self._window = None          # leading underscore: pywebview does not expose it to JavaScript
        self._fs = bool(fullscreen)

    def toggle_fullscreen(self):
        if self._window is not None:
            self._window.toggle_fullscreen()
            self._fs = not self._fs
        return self._fs

    def is_fullscreen(self):
        return self._fs


def run_pywebview(url, title, fullscreen=False, on_closed=None):
    """Blocks on the main thread until the window closes (required by macOS / GTK)."""
    import webview
    api = WindowApi(fullscreen)
    w = webview.create_window(title, url, width=1600, height=900, min_size=(800, 480), js_api=api,
                              background_color="#05070A", fullscreen=fullscreen, text_select=False)
    api._window = w
    if on_closed:
        w.events.closed += on_closed
    webview.start(private_mode=False)


def open_default_browser(url):
    try:
        return webbrowser.open(url, new=1)
    except Exception:
        return False
