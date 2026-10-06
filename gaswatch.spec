from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve()
SRC = ROOT / "src"
ICON = ROOT / "build" / "GasWatch.icns"

a = Analysis(
    [str(ROOT / "run_gui.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "web3",
        "web3.providers.rpc",
        "web3.eth",
        "eth_typing",
        "eth_utils",
        "eth_abi",
        "eth_account",
        "requests",
        "urllib3",
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "dotenv",
        "eth_hash",
        "eth_keys",
        "eth_typing.evm",
        "cytoolz",
        "eth_utils.toolz",
        "parsimonious",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "telegram",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "setuptools",
        "pip",
        "test",
        "unittest",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GasWatch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name="GasWatch",
)

bundle = BUNDLE(
    coll,
    name="GasWatch.app",
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier="com.gaswatch.desktop",
    version="1.0.0",
    info_plist={
        "CFBundleName": "GasWatch",
        "CFBundleDisplayName": "GasWatch",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "CFBundleExecutable": "GasWatch",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.15",
        "NSRequiresAquaSystemAppearance": False,
        "CFBundleDocumentTypes": [],
        "LSApplicationCategoryType": "public.app-category.utilities",
    },
)