# Turning this into an app

Right now this folder is **source code**, it needs Python, and you start it
with `python run.py`. The double-clickable app does not exist yet. It gets
**built** from this source, and there are two ways to do that.

Nothing here is "hosted" on the internet. The app runs a small web server on
your own machine at `127.0.0.1`, which is why it looks like a website but works
with the wifi off. GitHub only stores the download.

---

## Path A: build it on your own PC (10 minutes, Windows only)

Do this first. It gets you a working `.exe` today and proves the build works
before you involve GitHub.

```
pip install pyinstaller
pyinstaller build/catprep.spec --noconfirm --clean
```

Result: **`dist/CatPrep/CatPrep.exe`**

Double-click it. From then on that is the app, no `run.py`, no terminal.
The whole `dist/CatPrep` folder is the app, so if you move it, move the folder.
Your questions and progress live in `CatPrep Data` next to the .exe.

This only builds for Windows. A Windows machine cannot build a Mac app.

---

## Path B: let GitHub build all four (for other people)

GitHub runs a Windows, a Mac and a Linux machine for you, free, on public
repositories. You push a tag; it builds all four and puts them on your
Releases page.

```
git init
git add -A
git status            # LOOK: no .db, no __pycache__, no dist/
git commit -m "Cat Prep - setup runs in the app"
git remote add origin https://github.com/YOURNAME/Cat-Prep-App.git
git branch -M main
git push -u origin main
```

**Test the build before tagging anything.** Go to the **Actions** tab →
"Build app" → **Run workflow**. Wait. If it goes green, four downloads appear
under that run.

When it is green:

```
git tag v1.0
git push origin v1.0
```

That tag is what creates the Release. Twenty minutes later your Releases page
has `CatPrep-windows.zip`, `CatPrep-macos-apple-silicon.zip`,
`CatPrep-macos-intel.zip` and `CatPrep-linux.zip`, and
anyone can download one and open it. No Python, no pip, no terminal.

---

## What will go wrong the first time

**"ModuleNotFoundError" in the build log.** PyInstaller missed something. Open
`build/catprep.spec`, add the name to `hiddenimports`, push again. One line.

**Windows says "unrecognised app".** More info → Run anyway. Every unsigned
program does this; removing it costs $99/year for a certificate.

**macOS says "unidentified developer".** Right-click the app → Open, then Open
again. Only the first time.

**The app opens and the page is blank.** `web/` did not get bundled. Check the
`datas` list in the spec.

---

## One rule

**Do not put the app in OneDrive, iCloud Drive, Dropbox or Google Drive.**
Importing writes thousands of image files and the sync client uploads each one
as it appears, fighting the import for the disk. Measured on a real machine:
about 10 minutes inside OneDrive, about 3 minutes outside it. The app checks
where it is and warns you.
