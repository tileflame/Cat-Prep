# PyInstaller build spec — turns this folder into one double-clickable app.
#
# Run it yourself with:   pyinstaller build/catprep.spec
# or let GitHub Actions do all three platforms (see .github/workflows/build.yml).
#
# The point of this file is that a student should not have to install Python,
# PyMuPDF or Pillow to use the app. They download one file and open it.
#
# THE THREE THINGS THAT BREAK A PYINSTALLER BUILD, AND WHAT IS DONE ABOUT THEM
# ---------------------------------------------------------------------------
# 1. Data files vanish. PyInstaller bundles imported MODULES; it does not know
#    about web/app.js or web/index.html, which are read from disk at runtime.
#    They are listed in `datas` below.
# 2. Relative paths stop meaning what they meant. A frozen app's working
#    directory is wherever the user launched it from, while sat_importer.py
#    resolves "pdfs", "images" and "database/questions.db" relative to the
#    current directory. launcher.py fixes the working directory before anything
#    else runs.
# 3. multiprocessing re-launches the whole app. Not an issue here: the importer
#    deliberately uses threads, for exactly this reason. See setup_api.py.

import os
import sys

block_cipher = None
# SPECPATH is injected by PyInstaller and is the folder holding THIS file, so
# the build works no matter which directory you launch it from. Using getcwd()
# here meant `pyinstaller build/catprep.spec` only worked if you happened to be
# standing in the project root, and failed with a confusing missing-file error
# if you weren't.
HERE = os.path.dirname(os.path.abspath(SPECPATH))  # noqa: F821  (PyInstaller global)
APP = os.path.join(HERE, "Cat Sat")

a = Analysis(
    [os.path.join(HERE, "build", "launcher.py")],
    pathex=[APP],
    binaries=[],
    # (source, destination-inside-the-bundle)
    datas=[
        (os.path.join(APP, "web"), "web"),
        (os.path.join(HERE, "LICENSE"), "."),
        (os.path.join(HERE, "README.md"), "."),
    ],
    # Modules reached only by string name at runtime — PyInstaller's static
    # analysis cannot see these, so they have to be named explicitly.
    hiddenimports=[
        "sat_importer", "setup_api", "web_api", "server", "database",
        "question_repo", "attempt_repo", "adaptive_engine", "models",
        "config", "diagnostic", "test_flow", "study_plan", "plan_builder",
        # user_profile, NOT profile. Python's standard library already owns the
        # name `profile`, and a frozen app has one flat namespace where two
        # modules called `profile` are one collision — see user_profile.py.
        "user_profile", "score_report",
        "fitz", "PIL", "PIL.Image",
    ],
    hookspath=[],
    runtime_hooks=[],
    # The desktop UI is the old CustomTkinter build; the app ships the web UI.
    # Excluding tkinter keeps the download tens of megabytes smaller.
    # tkinter: the desktop UI is the old CustomTkinter build; the app ships the
    # web UI, and excluding it keeps the download tens of megabytes smaller.
    # profile/cProfile/pstats: the standard library's profiler, excluded so it
    # cannot collide with the app's own module (now renamed user_profile, so
    # this is belt and braces rather than the fix).
    excludes=["tkinter", "customtkinter", "matplotlib", "numpy", "pytest",
              "profile", "cProfile", "pstats"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="CatPrep",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=True on purpose. If the app fails to start on a machine I cannot
    # test, a visible window with the traceback is the difference between a bug
    # report and "it just doesn't open".
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="CatPrep",
)

# macOS gets a proper .app so it can be dragged to Applications.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="CatPrep.app",
        icon=None,
        bundle_identifier="app.catprep.studytool",
        info_plist={"NSHighResolutionCapable": True, "LSBackgroundOnly": False},
    )
