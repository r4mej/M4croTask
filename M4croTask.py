#!/usr/bin/env python3
"""
M4croTask — A TinyTask-equivalent macro recorder for Linux
Requires: pip install pynput pyautogui evdev --break-system-packages
          sudo apt install python3-tk
    Also(for evdev):
          sudo usermod -aG input $USER
          log out and log back in for it to take effect
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time
import json
import os
import sys
import glob

try:
    from pynput import mouse, keyboard
    from pynput.mouse import Button, Controller as MouseController
    from pynput.keyboard import Key, Controller as KeyboardController
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0
except ImportError:
    print("Missing dependencies. Install them with:")
    print("  pip install pynput pyautogui evdev")
    sys.exit(1)

try:
    import evdev
    from evdev import InputDevice, ecodes, list_devices
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False


# ─── evdev key code → pynput Key mapping ─────────────────────────────────────

EVDEV_TO_PYNPUT = {
    ecodes.KEY_LEFTCTRL:   Key.ctrl_l   if EVDEV_AVAILABLE else None,
    ecodes.KEY_RIGHTCTRL:  Key.ctrl_l   if EVDEV_AVAILABLE else None,
    ecodes.KEY_LEFTSHIFT:  Key.shift_l  if EVDEV_AVAILABLE else None,
    ecodes.KEY_RIGHTSHIFT: Key.shift_l  if EVDEV_AVAILABLE else None,
    ecodes.KEY_LEFTALT:    Key.alt_l    if EVDEV_AVAILABLE else None,
    ecodes.KEY_RIGHTALT:   Key.alt_l    if EVDEV_AVAILABLE else None,
    ecodes.KEY_LEFTMETA:   Key.cmd      if EVDEV_AVAILABLE else None,
    ecodes.KEY_RIGHTMETA:  Key.cmd      if EVDEV_AVAILABLE else None,
    ecodes.KEY_F1:  Key.f1  if EVDEV_AVAILABLE else None,
    ecodes.KEY_F2:  Key.f2  if EVDEV_AVAILABLE else None,
    ecodes.KEY_F3:  Key.f3  if EVDEV_AVAILABLE else None,
    ecodes.KEY_F4:  Key.f4  if EVDEV_AVAILABLE else None,
    ecodes.KEY_F5:  Key.f5  if EVDEV_AVAILABLE else None,
    ecodes.KEY_F6:  Key.f6  if EVDEV_AVAILABLE else None,
    ecodes.KEY_F7:  Key.f7  if EVDEV_AVAILABLE else None,
    ecodes.KEY_F8:  Key.f8  if EVDEV_AVAILABLE else None,
    ecodes.KEY_F9:  Key.f9  if EVDEV_AVAILABLE else None,
    ecodes.KEY_F10: Key.f10 if EVDEV_AVAILABLE else None,
    ecodes.KEY_F11: Key.f11 if EVDEV_AVAILABLE else None,
    ecodes.KEY_F12: Key.f12 if EVDEV_AVAILABLE else None,
} if EVDEV_AVAILABLE else {}

# Single char keys by evdev code
EVDEV_CHAR_KEYS = {
    ecodes.KEY_A: 'a', ecodes.KEY_B: 'b', ecodes.KEY_C: 'c',
    ecodes.KEY_D: 'd', ecodes.KEY_E: 'e', ecodes.KEY_F: 'f',
    ecodes.KEY_G: 'g', ecodes.KEY_H: 'h', ecodes.KEY_I: 'i',
    ecodes.KEY_J: 'j', ecodes.KEY_K: 'k', ecodes.KEY_L: 'l',
    ecodes.KEY_M: 'm', ecodes.KEY_N: 'n', ecodes.KEY_O: 'o',
    ecodes.KEY_P: 'p', ecodes.KEY_Q: 'q', ecodes.KEY_R: 'r',
    ecodes.KEY_S: 's', ecodes.KEY_T: 't', ecodes.KEY_U: 'u',
    ecodes.KEY_V: 'v', ecodes.KEY_W: 'w', ecodes.KEY_X: 'x',
    ecodes.KEY_Y: 'y', ecodes.KEY_Z: 'z',
    ecodes.KEY_0: '0', ecodes.KEY_1: '1', ecodes.KEY_2: '2',
    ecodes.KEY_3: '3', ecodes.KEY_4: '4', ecodes.KEY_5: '5',
    ecodes.KEY_6: '6', ecodes.KEY_7: '7', ecodes.KEY_8: '8',
    ecodes.KEY_9: '9',
} if EVDEV_AVAILABLE else {}


def evdev_code_to_pynput(code):
    """Convert an evdev key code to a pynput key representation."""
    if code in EVDEV_TO_PYNPUT:
        return EVDEV_TO_PYNPUT[code]
    if code in EVDEV_CHAR_KEYS:
        return EVDEV_CHAR_KEYS[code]
    return None


def find_keyboards():
    """Return all evdev keyboard devices."""
    devices = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
            caps = dev.capabilities()
            if ecodes.EV_KEY in caps:
                keys = caps[ecodes.EV_KEY]
                # Must have letter keys to count as keyboard
                if ecodes.KEY_A in keys and ecodes.KEY_Z in keys:
                    devices.append(dev)
        except Exception:
            pass
    return devices


# ─── Hotkey Manager (evdev-based, works over any focused app) ─────────────────

class HotkeyManager:
    """
    Reads /dev/input directly via evdev — works regardless of focused window.
    Falls back to pynput if evdev is unavailable or no /dev/input access.
    """

    def __init__(self):
        self._bindings = {}
        self._pressed = set()
        self._lock = threading.Lock()
        self._running = False
        self._threads = []
        self._use_evdev = EVDEV_AVAILABLE

    def bind(self, keys: set, callback):
        self._bindings[frozenset(keys)] = callback

    def unbind(self, keys: set):
        self._bindings.pop(frozenset(keys), None)

    def clear(self):
        self._bindings.clear()

    def start(self):
        self._running = True
        self._pressed.clear()
        if self._use_evdev:
            self._start_evdev()
        else:
            self._start_pynput()

    def stop(self):
        self._running = False
        for t in self._threads:
            t.join(timeout=0.5)
        self._threads.clear()
        if hasattr(self, '_pynput_listener') and self._pynput_listener:
            self._pynput_listener.stop()
            self._pynput_listener = None

    # ── evdev backend ────────────────────────────────────────────────────────

    def _start_evdev(self):
        try:
            keyboards = find_keyboards()
            if not keyboards:
                raise RuntimeError("No keyboard devices found via evdev")
            for dev in keyboards:
                t = threading.Thread(target=self._evdev_loop, args=(dev,), daemon=True)
                t.start()
                self._threads.append(t)
        except Exception as e:
            print(f"[evdev] falling back to pynput: {e}")
            self._use_evdev = False
            self._start_pynput()

    def _evdev_loop(self, dev):
        try:
            for event in dev.read_loop():
                if not self._running:
                    break
                if event.type != ecodes.EV_KEY:
                    continue
                key = evdev_code_to_pynput(event.code)
                if key is None:
                    continue
                if event.value == 1:   # key down
                    self._on_press(key)
                elif event.value == 0: # key up
                    self._on_release(key)
        except Exception:
            pass

    # ── pynput fallback ───────────────────────────────────────────────────────

    def _start_pynput(self):
        self._pynput_listener = keyboard.Listener(
            on_press=self._on_press_pynput,
            on_release=self._on_release_pynput
        )
        self._pynput_listener.start()

    def _normalize_pynput(self, key):
        aliases = {
            Key.ctrl_r: Key.ctrl_l, Key.shift_r: Key.shift_l,
            Key.alt_r: Key.alt_l,   Key.cmd_r: Key.cmd,
        }
        return aliases.get(key, key)

    def _on_press_pynput(self, key):
        self._on_press(self._normalize_pynput(key))

    def _on_release_pynput(self, key):
        self._on_release(self._normalize_pynput(key))

    # ── shared logic ──────────────────────────────────────────────────────────

    def _on_press(self, key):
        with self._lock:
            self._pressed.add(key)
            current = frozenset(self._pressed)
        cb = self._bindings.get(current)
        if cb:
            threading.Thread(target=cb, daemon=True).start()

    def _on_release(self, key):
        with self._lock:
            self._pressed.discard(key)


# ─── Recorder Core ────────────────────────────────────────────────────────────

class MacroRecorder:
    def __init__(self):
        self.events = []
        self.recording = False
        self.playing = False
        self._last_time = None
        self._mouse_listener = None
        self._keyboard_listener = None
        self._play_thread = None
        self._stop_playback = False
        self._ignored_keys = set()

    def start_recording(self, ignored_keys=None):
        self.events = []
        self.recording = True
        self._last_time = time.time()
        self._ignored_keys = ignored_keys or set()

        self._mouse_listener = mouse.Listener(
            on_move=self._on_move,
            on_click=self._on_click,
            on_scroll=self._on_scroll
        )
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self._mouse_listener.start()
        self._keyboard_listener.start()

    def stop_recording(self):
        self.recording = False
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._keyboard_listener:
            self._keyboard_listener.stop()
        self._mouse_listener = None
        self._keyboard_listener = None

    def _elapsed(self):
        now = time.time()
        delta = now - self._last_time
        self._last_time = now
        return delta

    def _on_move(self, x, y):
        if not self.recording:
            return
        self.events.append({"type": "move", "x": x, "y": y, "delay": self._elapsed()})

    def _on_click(self, x, y, button, pressed):
        if not self.recording:
            return
        self.events.append({
            "type": "click", "x": x, "y": y,
            "button": button.name, "pressed": pressed,
            "delay": self._elapsed()
        })

    def _on_scroll(self, x, y, dx, dy):
        if not self.recording:
            return
        self.events.append({
            "type": "scroll", "x": x, "y": y,
            "dx": dx, "dy": dy, "delay": self._elapsed()
        })

    def _key_to_str(self, key):
        try:
            return ("char", key.char)
        except AttributeError:
            return ("special", str(key))

    def _normalize_key(self, key):
        aliases = {
            Key.ctrl_r: Key.ctrl_l, Key.shift_r: Key.shift_l,
            Key.alt_r: Key.alt_l,   Key.cmd_r: Key.cmd,
        }
        return aliases.get(key, key)

    def _on_key_press(self, key):
        if not self.recording:
            return
        if self._normalize_key(key) in self._ignored_keys:
            return
        kind, val = self._key_to_str(key)
        self.events.append({"type": "key_press", "kind": kind, "val": val, "delay": self._elapsed()})

    def _on_key_release(self, key):
        if not self.recording:
            return
        if self._normalize_key(key) in self._ignored_keys:
            return
        kind, val = self._key_to_str(key)
        self.events.append({"type": "key_release", "kind": kind, "val": val, "delay": self._elapsed()})

    # ── Playback ──────────────────────────────────────────────────────────────

    def play(self, speed=1.0, loops=1, on_done=None):
        self._stop_playback = False
        self._play_thread = threading.Thread(
            target=self._playback_loop,
            args=(speed, loops, on_done),
            daemon=True
        )
        self._play_thread.start()

    def stop_playback(self):
        self._stop_playback = True

    def _playback_loop(self, speed, loops, on_done):
        self.playing = True
        mc = MouseController()
        kc = KeyboardController()
        btn_map = {"left": Button.left, "right": Button.right, "middle": Button.middle}
        loop_count = 0

        while not self._stop_playback:
            for event in self.events:
                if self._stop_playback:
                    break
                time.sleep(max(0, event["delay"] / speed))
                t = event["type"]
                if t == "move":
                    mc.position = (event["x"], event["y"])
                elif t == "click":
                    mc.position = (event["x"], event["y"])
                    btn = btn_map.get(event["button"], Button.left)
                    (mc.press if event["pressed"] else mc.release)(btn)
                elif t == "scroll":
                    mc.scroll(event["dx"], event["dy"])
                elif t == "key_press":
                    self._press_key(kc, event)
                elif t == "key_release":
                    self._release_key(kc, event)
            loop_count += 1
            if loops != 0 and loop_count >= loops:
                break

        self.playing = False
        if on_done:
            on_done()

    def _press_key(self, kc, event):
        try:
            if event["kind"] == "char":
                kc.press(event["val"])
            else:
                k = self._parse_special(event["val"])
                if k:
                    kc.press(k)
        except Exception:
            pass

    def _release_key(self, kc, event):
        try:
            if event["kind"] == "char":
                kc.release(event["val"])
            else:
                k = self._parse_special(event["val"])
                if k:
                    kc.release(k)
        except Exception:
            pass

    def _parse_special(self, val):
        try:
            return getattr(Key, val.replace("Key.", ""))
        except AttributeError:
            return None

    def save(self, path):
        with open(path, "w") as f:
            json.dump(self.events, f, indent=2)

    def load(self, path):
        with open(path, "r") as f:
            self.events = json.load(f)


# ─── Hotkey Capture Widget ────────────────────────────────────────────────────

class HotkeyCaptureButton(tk.Button):
    def __init__(self, parent, on_captured, default_text="Click to set…", **kwargs):
        self._on_captured = on_captured
        self._listening = False
        self._listener = None
        self._held = set()
        self._label_var = tk.StringVar(value=default_text)

        super().__init__(parent, textvariable=self._label_var,
                         command=self._start_capture, **kwargs)

    def set_label(self, text):
        self._label_var.set(text)

    def _start_capture(self):
        if self._listening:
            return
        self._listening = True
        self._held = set()
        self._label_var.set("… press keys …")
        self.config(relief="sunken")

        self._listener = keyboard.Listener(
            on_press=self._cap_press,
            on_release=self._cap_release
        )
        self._listener.start()

    def _normalize(self, key):
        aliases = {
            Key.ctrl_r: Key.ctrl_l, Key.shift_r: Key.shift_l,
            Key.alt_r: Key.alt_l,   Key.cmd_r: Key.cmd,
        }
        return aliases.get(key, key)

    def _cap_press(self, key):
        self._held.add(self._normalize(key))

    def _cap_release(self, key):
        if not self._listening:
            return
        captured = set(self._held)
        self._listening = False
        if self._listener:
            self._listener.stop()
            self._listener = None
        label = self._keys_to_label(captured)
        self.after(0, lambda: self._finish(captured, label))

    def _finish(self, keys, label):
        self._label_var.set(label)
        self.config(relief="flat")
        self._on_captured(keys)

    @staticmethod
    def _keys_to_label(keys):
        order = [Key.ctrl_l, Key.shift_l, Key.alt_l, Key.cmd]
        parts = []
        for k in order:
            if k in keys:
                parts.append({Key.ctrl_l: "Ctrl", Key.shift_l: "Shift",
                               Key.alt_l: "Alt",  Key.cmd: "Super"}[k])
        for k in keys:
            if k not in order:
                try:
                    parts.append(k.char.upper())
                except AttributeError:
                    parts.append(str(k).replace("Key.", "").capitalize())
        return " + ".join(parts) if parts else "None"


# ─── GUI ──────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    ACCENT = "#00e5ff"
    BG     = "#0d1117"
    PANEL  = "#161b22"
    BORDER = "#30363d"
    FG     = "#e6edf3"
    FG_DIM = "#8b949e"
    RED    = "#f85149"
    GREEN  = "#3fb950"
    YELLOW = "#d29922"

    def __init__(self):
        super().__init__()
        self.recorder = MacroRecorder()
        self.hotkey_mgr = HotkeyManager()

        self.countdown_var    = tk.IntVar(value=3)
        self.speed_var        = tk.DoubleVar(value=1.0)
        self.loops_var        = tk.IntVar(value=1)
        self.infinite_var     = tk.BooleanVar(value=False)
        self.status_var       = tk.StringVar(value="Ready")
        self.event_count_var  = tk.StringVar(value="0 events")

        self._record_keys = {Key.ctrl_l, Key.shift_l, 'r'}
        self._play_keys   = {Key.ctrl_l, Key.shift_l, 'p'}
        self._cd_thread   = None

        self.title("M4cro Task")
        self.resizable(False, False)
        self.configure(bg=self.BG)
        self._build_ui()
        self._apply_hotkeys()
        self.hotkey_mgr.start()

        if not EVDEV_AVAILABLE:
            self._set_status("⚠ evdev not found — install it for global hotkeys", self.YELLOW)
        elif not self.hotkey_mgr._use_evdev:
            self._set_status("⚠ No /dev/input access — run with sudo or add udev rule", self.YELLOW)
        else:
            self._set_status("Ready  (global hotkeys active via evdev)", self.GREEN)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        title_frame = tk.Frame(self, bg=self.PANEL, pady=12)
        title_frame.pack(fill="x")
        tk.Label(title_frame, text="⬡ M4croTask", bg=self.PANEL, fg=self.ACCENT,
                 font=("Courier", 18, "bold")).pack()
        tk.Label(title_frame, text="Mouse & Keyboard Macro Recorder",
                 bg=self.PANEL, fg=self.FG_DIM, font=("Courier", 9)).pack()

        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x")

        body = tk.Frame(self, bg=self.BG, padx=24, pady=16)
        body.pack(fill="both")

        # Status
        sf = tk.Frame(body, bg=self.PANEL, padx=12, pady=8)
        sf.pack(fill="x", pady=(0, 14))
        self._status_dot = tk.Label(sf, text="●", bg=self.PANEL, fg=self.FG_DIM,
                                    font=("Courier", 12))
        self._status_dot.pack(side="left", padx=(0, 6))
        tk.Label(sf, textvariable=self.status_var, bg=self.PANEL, fg=self.FG,
                 font=("Courier", 10, "bold")).pack(side="left")
        tk.Label(sf, textvariable=self.event_count_var, bg=self.PANEL, fg=self.FG_DIM,
                 font=("Courier", 9)).pack(side="right")

        # Big buttons
        bf = tk.Frame(body, bg=self.BG)
        bf.pack(fill="x", pady=(0, 14))
        self._rec_btn = self._big_btn(bf, "⏺  RECORD", self.RED, self._toggle_record)
        self._rec_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self._play_btn = self._big_btn(bf, "▶  PLAY", self.GREEN, self._toggle_play)
        self._play_btn.pack(side="left", expand=True, fill="x")

        # Options
        opts = tk.LabelFrame(body, text=" Options ", bg=self.BG, fg=self.ACCENT,
                             font=("Courier", 9, "bold"), bd=1, relief="solid",
                             highlightbackground=self.BORDER, pady=10, padx=10)
        opts.pack(fill="x", pady=(0, 10))
        self._row(opts, 0, "Countdown (s):",
                  ttk.Spinbox(opts, from_=0, to=10, textvariable=self.countdown_var,
                              width=6, font=("Courier", 10)))
        self._row(opts, 1, "Playback speed:",
                  tk.Scale(opts, from_=0.1, to=5.0, resolution=0.1,
                           orient="horizontal", variable=self.speed_var,
                           bg=self.BG, fg=self.FG, troughcolor=self.PANEL,
                           highlightthickness=0, showvalue=True,
                           font=("Courier", 8), length=180))
        self._row(opts, 2, "Repeat loops:",
                  ttk.Spinbox(opts, from_=1, to=9999, textvariable=self.loops_var,
                              width=6, font=("Courier", 10)))
        tk.Checkbutton(opts, text="Loop forever", variable=self.infinite_var,
                       bg=self.BG, fg=self.FG, selectcolor=self.PANEL,
                       activebackground=self.BG, activeforeground=self.ACCENT,
                       font=("Courier", 9)).grid(row=3, column=1, sticky="w", pady=2)

        # Hotkeys
        hk = tk.LabelFrame(body, text=" Hotkeys ", bg=self.BG, fg=self.ACCENT,
                            font=("Courier", 9, "bold"), bd=1, relief="solid",
                            highlightbackground=self.BORDER, pady=10, padx=10)
        hk.pack(fill="x", pady=(0, 10))

        evdev_note = ("✔ Global hotkeys via evdev (works over any app)"
                      if EVDEV_AVAILABLE and self.hotkey_mgr._use_evdev
                      else "⚠ Using pynput fallback — may not work over other apps")
        tk.Label(hk, text=evdev_note, bg=self.BG, fg=self.FG_DIM,
                 font=("Courier", 8)).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        tk.Label(hk, text="Record key:", bg=self.BG, fg=self.FG_DIM,
                 font=("Courier", 9)).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        self._rec_hk_btn = HotkeyCaptureButton(
            hk, on_captured=self._on_record_hotkey_set,
            default_text=HotkeyCaptureButton._keys_to_label(self._record_keys),
            bg=self.PANEL, fg=self.ACCENT, activebackground=self.BORDER,
            activeforeground=self.ACCENT, relief="flat", font=("Courier", 9),
            cursor="hand2", width=22, pady=4)
        self._rec_hk_btn.grid(row=1, column=1, sticky="w", pady=3)

        tk.Label(hk, text="Play key:", bg=self.BG, fg=self.FG_DIM,
                 font=("Courier", 9)).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=3)
        self._play_hk_btn = HotkeyCaptureButton(
            hk, on_captured=self._on_play_hotkey_set,
            default_text=HotkeyCaptureButton._keys_to_label(self._play_keys),
            bg=self.PANEL, fg=self.ACCENT, activebackground=self.BORDER,
            activeforeground=self.ACCENT, relief="flat", font=("Courier", 9),
            cursor="hand2", width=22, pady=4)
        self._play_hk_btn.grid(row=2, column=1, sticky="w", pady=3)

        # File buttons
        ff = tk.Frame(body, bg=self.BG)
        ff.pack(fill="x", pady=(0, 6))
        self._small_btn(ff, "💾 Save Macro", self._save).pack(side="left", expand=True, fill="x", padx=(0, 4))
        self._small_btn(ff, "📂 Load Macro", self._load).pack(side="left", expand=True, fill="x", padx=(4, 4))
        self._small_btn(ff, "🗑 Clear",       self._clear).pack(side="left", expand=True, fill="x", padx=(4, 0))

        tk.Label(body, text="Tip: Move mouse to top-left corner to abort playback",
                 bg=self.BG, fg=self.FG_DIM, font=("Courier", 8),
                 wraplength=340, justify="left").pack(anchor="w")

        self._style_spinboxes()

    def _row(self, parent, row, label, widget):
        tk.Label(parent, text=label, bg=self.BG, fg=self.FG_DIM,
                 font=("Courier", 9)).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
        widget.grid(row=row, column=1, sticky="w", pady=3)

    def _big_btn(self, parent, text, color, cmd):
        return tk.Button(parent, text=text, command=cmd,
                         bg=color, fg=self.BG, activebackground=color,
                         activeforeground=self.BG, relief="flat",
                         font=("Courier", 12, "bold"), pady=10,
                         cursor="hand2", borderwidth=0)

    def _small_btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd,
                         bg=self.PANEL, fg=self.FG, activebackground=self.BORDER,
                         activeforeground=self.ACCENT, relief="flat",
                         font=("Courier", 9), pady=6, cursor="hand2", borderwidth=0)

    def _style_spinboxes(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TSpinbox",
                         fieldbackground=self.PANEL, foreground=self.FG,
                         background=self.PANEL, bordercolor=self.BORDER,
                         arrowcolor=self.ACCENT)

    # ── Hotkey wiring ─────────────────────────────────────────────────────────

    def _apply_hotkeys(self):
        self.hotkey_mgr.clear()
        self.hotkey_mgr.bind(self._record_keys, lambda: self.after(0, self._toggle_record))
        self.hotkey_mgr.bind(self._play_keys,   lambda: self.after(0, self._toggle_play))

    def _on_record_hotkey_set(self, keys):
        self._record_keys = keys
        self._apply_hotkeys()
        self._set_status(f"Record hotkey: {HotkeyCaptureButton._keys_to_label(keys)}", self.ACCENT)

    def _on_play_hotkey_set(self, keys):
        self._play_keys = keys
        self._apply_hotkeys()
        self._set_status(f"Play hotkey: {HotkeyCaptureButton._keys_to_label(keys)}", self.ACCENT)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _toggle_record(self):
        if self.recorder.playing:
            self._set_status("Stop playback first!", self.YELLOW)
            return
        if self.recorder.recording:
            self._stop_record()
        else:
            cd = self.countdown_var.get()
            if cd > 0:
                self._set_status(f"Starting in {cd}…", self.YELLOW)
                self._rec_btn.config(state="disabled")
                self._cd_thread = threading.Thread(
                    target=self._countdown, args=(cd,), daemon=True)
                self._cd_thread.start()
            else:
                self._start_record()

    def _countdown(self, seconds):
        for i in range(seconds, 0, -1):
            self.after(0, lambda i=i: self._set_status(f"Recording in {i}…", self.YELLOW))
            time.sleep(1)
        self.after(0, self._start_record)

    def _start_record(self):
        ignored = set(self._record_keys)
        self.recorder.start_recording(ignored_keys=ignored)
        self._rec_btn.config(text="⏹  STOP", state="normal")
        self._play_btn.config(state="disabled")
        self._set_status("Recording…", self.RED)
        self._status_dot.config(fg=self.RED)
        self._update_count_loop()

    def _stop_record(self):
        self.recorder.stop_recording()
        self._rec_btn.config(text="⏺  RECORD")
        self._play_btn.config(state="normal")
        count = len(self.recorder.events)
        self._set_status(f"Recorded {count} events", self.GREEN)
        self._status_dot.config(fg=self.GREEN)
        self.event_count_var.set(f"{count} events")

    def _toggle_play(self):
        if self.recorder.recording:
            self._set_status("Stop recording first!", self.YELLOW)
            return
        if self.recorder.playing:
            self.recorder.stop_playback()
            self._play_btn.config(text="▶  PLAY")
            self._set_status("Playback stopped", self.FG_DIM)
            self._status_dot.config(fg=self.FG_DIM)
            self._rec_btn.config(state="normal")
            return
        if not self.recorder.events:
            self._set_status("No macro loaded!", self.YELLOW)
            return

        speed = self.speed_var.get()
        loops = 0 if self.infinite_var.get() else self.loops_var.get()
        self._play_btn.config(text="⏹  STOP")
        self._rec_btn.config(state="disabled")
        self._set_status("Playing…", self.GREEN)
        self._status_dot.config(fg=self.GREEN)
        self.recorder.play(speed=speed, loops=loops, on_done=self._on_playback_done)

    def _on_playback_done(self):
        self.after(0, self._playback_finished)

    def _playback_finished(self):
        self._play_btn.config(text="▶  PLAY")
        self._rec_btn.config(state="normal")
        self._set_status("Playback complete", self.ACCENT)
        self._status_dot.config(fg=self.ACCENT)

    def _save(self):
        if not self.recorder.events:
            messagebox.showwarning("Nothing to save", "Record a macro first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".macro",
            filetypes=[("RaMacro", "*.macro"), ("JSON", "*.json"), ("All", "*.*")]
        )
        if path:
            self.recorder.save(path)
            self._set_status(f"Saved: {os.path.basename(path)}", self.ACCENT)

    def _load(self):
        path = filedialog.askopenfilename(
            filetypes=[("RaMacro", "*.macro"), ("JSON", "*.json"), ("All", "*.*")]
        )
        if path:
            try:
                self.recorder.load(path)
                count = len(self.recorder.events)
                self.event_count_var.set(f"{count} events")
                self._set_status(f"Loaded: {os.path.basename(path)}", self.ACCENT)
            except Exception as e:
                messagebox.showerror("Load failed", str(e))

    def _clear(self):
        if messagebox.askyesno("Clear macro", "Delete the current macro?"):
            self.recorder.events = []
            self.event_count_var.set("0 events")
            self._set_status("Cleared", self.FG_DIM)

    def _set_status(self, msg, color=None):
        self.status_var.set(msg)
        if color:
            self._status_dot.config(fg=color)

    def _update_count_loop(self):
        if self.recorder.recording:
            self.event_count_var.set(f"{len(self.recorder.events)} events")
            self.after(200, self._update_count_loop)

    def _on_close(self):
        self.recorder.stop_recording()
        self.recorder.stop_playback()
        self.hotkey_mgr.stop()
        self.destroy()


# ─── Entry ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
