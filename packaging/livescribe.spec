# PyInstaller spec for the macOS .app and the Windows build.   pyinstaller packaging/livescribe.spec
import re, sys
from pathlib import Path
ROOT = Path(SPECPATH).parent
VER = re.search(r'__version__ = "(.+?)"', (ROOT / "livescribe.py").read_text()).group(1)
IS_MAC = sys.platform == "darwin"

hidden = ["livescribe", "shell_pywebview", "pystray", "PIL.Image", "PIL.ImageDraw"]
hidden += ["pystray._darwin", "webview.platforms.cocoa", "AppKit"] if IS_MAC else \
          ["pystray._win32", "webview.platforms.winforms", "webview.platforms.edgechromium", "soundcard", "numpy"]

a = Analysis([str(ROOT / "livescribe_gui.py")], pathex=[str(ROOT)],
             datas=[(str(ROOT / "ui.html"), "."), (str(ROOT / "icons"), "icons")],
             hiddenimports=hidden, excludes=["faster_whisper", "ctranslate2", "torch", "tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Livescribe", console=False,
          icon=str(ROOT / "icons" / "livescribe-512.png"))
coll = COLLECT(exe, a.binaries, a.datas, name="Livescribe")
if IS_MAC:
    app = BUNDLE(coll, name="Livescribe.app", icon=str(ROOT / "icons" / "livescribe-512.png"),
                 bundle_identifier="no.livescribe.app", version=VER,
                 info_plist={"CFBundleShortVersionString": VER, "CFBundleVersion": VER,
                             "LSUIElement": True, "NSHighResolutionCapable": True,
                             "NSMicrophoneUsageDescription":
                                 "Livescribe transcribes your microphone and the audio you hear."})
