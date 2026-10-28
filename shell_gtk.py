#!/usr/bin/python3
"""Native Linux window + tray icon for Lisanote (GTK3 + WebKit2GTK). Runs under the *system* python3.

  shell_gtk.py URL [--tray] [--hidden]

--tray   : put an icon in the panel (XApp status icon on Cinnamon/MATE/Xfce, else Ayatana AppIndicator).
           Closing the window then only hides it; the process (and any recording) keeps running
           until "Quit Lisanote" in the tray menu.
--hidden : start with only the tray icon (used for start-at-login).
"""
import json, os, sys, threading, urllib.request
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, WebKit2, GLib  # noqa: E402

URL = sys.argv[1]
TRAY = "--tray" in sys.argv
HIDDEN = "--hidden" in sys.argv
HERE = Path(__file__).resolve().parent
ICONS = HERE / "icons"
_u = urlparse(URL)
BASE, TOKEN = f"{_u.scheme}://{_u.netloc}", parse_qs(_u.query).get("t", [""])[0]

GLib.set_prgname("lisanote-window")  # WebKit stores its data under ~/.local/share/<prgname>
GLib.set_application_name("Lisanote")
Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)


def api(path, body=None):
    try:
        req = urllib.request.Request(f"{BASE}{path}?t={TOKEN}", data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def api_bg(path, body=None):
    threading.Thread(target=api, args=(path, body), daemon=True).start()


# ───────────────────────── window ─────────────────────────
win = Gtk.Window(title="Lisanote")
win.set_default_size(460, 700)
if (ICONS / "lisanote.svg").exists():
    win.set_icon_from_file(str(ICONS / "lisanote.svg"))
else:
    win.set_icon_name("audio-input-microphone")
win.set_keep_above(True)

manager = WebKit2.UserContentManager()
manager.register_script_message_handler("lisanote")


def on_msg(_m, res):
    try:
        v = res.get_js_value()
        if v.is_object() and v.object_has_property("pin"):
            win.set_keep_above(v.object_get_property("pin").to_boolean())
    except Exception:
        pass


manager.connect("script-message-received::lisanote", on_msg)
view = WebKit2.WebView.new_with_user_content_manager(manager)
s = view.get_settings()
s.set_enable_developer_extras(False)
s.set_javascript_can_access_clipboard(True)
s.set_enable_back_forward_navigation_gestures(False)
view.connect("context-menu", lambda *a: False)
view.load_uri(URL)
win.add(view)
view.show()


def show_window(*_):
    win.show()
    win.deiconify()
    win.present()


def toggle_window(*_):
    if win.get_visible() and win.is_active():
        win.hide()
    else:
        show_window()


# ───────────────────────── tray ─────────────────────────
tray = {"kind": None, "obj": None, "items": {}}
state = {"state": None, "show_seq": None, "syncing": False}


def make_menu():
    m = Gtk.Menu()
    items = {}

    def add(label, cb, key=None):
        it = Gtk.MenuItem(label=label)
        it.connect("activate", cb)
        m.append(it)
        if key:
            items[key] = it

    add("Show Lisanote", show_window)
    add("Start recording", lambda *_: api_bg("/toggle"), "rec")
    add("Open transcripts folder", lambda *_: api_bg("/open"))
    m.append(Gtk.SeparatorMenuItem())
    login = Gtk.CheckMenuItem(label="Start at login")

    def on_login(w):
        if not state["syncing"]:
            api_bg("/autostart", {"on": w.get_active()})
    login.connect("toggled", on_login)
    m.append(login)
    items["login"] = login
    m.append(Gtk.SeparatorMenuItem())
    add("Quit Lisanote", lambda *_: Gtk.main_quit())
    m.show_all()
    return m, items


def icon_for(st):
    p = ICONS / ("tray-rec.svg" if st in ("recording", "stopping") else "tray-idle.svg")
    return str(p) if p.exists() else ("media-record" if st == "recording" else "audio-input-microphone")


def setup_tray():
    menu, items = make_menu()
    tray["items"] = items
    try:
        gi.require_version("XApp", "1.0")
        from gi.repository import XApp
        if XApp.StatusIcon.any_monitors():           # Cinnamon / MATE / Xfce panels
            ic = XApp.StatusIcon()
            ic.set_name("lisanote")
            ic.set_icon_name(icon_for("idle"))
            ic.set_tooltip_text("Lisanote")
            ic.set_secondary_menu(menu)              # right-click = menu
            ic.connect("activate", lambda _i, button, _t: toggle_window() if button == 1 else None)
            ic.set_visible(True)
            tray.update(kind="xapp", obj=ic, menu=menu)
            return
    except Exception:
        pass
    try:                                              # GNOME (with extension) / KDE / others
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AI
        ind = AI.Indicator.new("lisanote", icon_for("idle"), AI.IndicatorCategory.APPLICATION_STATUS)
        ind.set_status(AI.IndicatorStatus.ACTIVE)
        ind.set_title("Lisanote")
        ind.set_menu(menu)
        ind.set_secondary_activate_target(menu.get_children()[0])
        tray.update(kind="ayatana", obj=ind, menu=menu)
    except Exception:
        tray.update(kind=None)                        # no tray → behave like a normal window


def apply_state(st):
    if st is None:
        return False
    rec = st["state"]
    if rec != state["state"]:
        state["state"] = rec
        if tray["kind"]:
            it = tray["items"]["rec"]
            it.set_label("Stop recording" if rec == "recording" else
                         "Finishing…" if rec == "stopping" else "Start recording")
            it.set_sensitive(rec != "stopping")
            icon, tip = icon_for(rec), ("Lisanote — recording" if rec == "recording" else "Lisanote")
            if tray["kind"] == "xapp":
                tray["obj"].set_icon_name(icon)
                tray["obj"].set_tooltip_text(tip)
            else:
                tray["obj"].set_icon_full(icon, tip)
        win.set_title("● Lisanote — recording" if rec == "recording" else "Lisanote")
    if state["show_seq"] is not None and st["show_seq"] != state["show_seq"]:
        show_window()                  # another launch (menu / desktop icon) asked us to come forward
    state["show_seq"] = st["show_seq"]
    if tray["kind"]:
        login = tray["items"]["login"]
        if login.get_active() != st["autostart"]:
            state["syncing"] = True
            login.set_active(st["autostart"])
            state["syncing"] = False
    return False


def poll_worker():
    import time
    while True:
        try:
            st = json.loads(urllib.request.urlopen(f"{BASE}/state?t={TOKEN}", timeout=2).read())
        except Exception:
            st = None
        GLib.idle_add(apply_state, st)   # UI changes only on the GTK main loop
        time.sleep(0.7)


def on_delete(*_):
    if tray["kind"]:
        win.hide()          # keep running in the tray; recording continues
        return True
    Gtk.main_quit()
    return False


if TRAY:
    setup_tray()
    if tray["kind"]:
        api("/hello", {"tray": True})
win.connect("delete-event", on_delete)
threading.Thread(target=poll_worker, daemon=True).start()
if not (HIDDEN and tray["kind"]):
    win.show()
Gtk.main()
