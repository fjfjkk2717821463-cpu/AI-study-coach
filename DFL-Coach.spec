# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

SPEC_ROOT = Path(SPECPATH)

datas = [
    (str(SPEC_ROOT / "templates"), "templates"),
    (str(SPEC_ROOT / "static"), "static"),
]
binaries = []
hiddenimports = []
tmp_ret = collect_all("webview")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]


a = Analysis(
    [str(SPEC_ROOT / "desktop.py")],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DFL Coach",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DFL Coach",
)
app = BUNDLE(
    coll,
    name="DFL Coach.app",
    icon=None,
    bundle_identifier="com.dflcoach.app",
    info_plist={
        "CFBundleDisplayName": "DFL Coach",
        "NSMicrophoneUsageDescription": "用于「语音复述」：把你的口头讲解转成文字，教练据此点评。",
        "NSSpeechRecognitionUsageDescription": "用于「语音复述」功能的语音识别。",
    },
)
