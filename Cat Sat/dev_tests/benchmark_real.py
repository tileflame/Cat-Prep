"""
REAL benchmark, run this on YOUR machine, with real CustomTkinter.

Everything else in dev_tests/ runs against a stub Tk, so it can prove the app's
logic is fast but it cannot measure what CustomTkinter itself costs. This does.

    cd "Cat Sat"
    python dev_tests/benchmark_real.py

It opens a small window for a few seconds, measures, closes itself, and prints a
report. Paste the whole report back and we'll know exactly what to fix.

It only reads. It creates no sessions and writes nothing to your databases.
"""
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
os.chdir(APP)

REPORT = []


def line(text=""):
    print(text)
    REPORT.append(text)


def bench(label, fn, repeat=5):
    """Median of `repeat` runs, in milliseconds."""
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter() - start) * 1000)
    median = statistics.median(times)
    line(f"  {label:<46}{median:>9.1f} ms   (min {min(times):.1f} / max {max(times):.1f})")
    return median


line("=" * 74)
line("CAT SAT, REAL PERFORMANCE BENCHMARK")
line("=" * 74)

# ---------------------------------------------------------------- environment
import platform
line(f"\nPython      {sys.version.split()[0]}   {platform.system()} {platform.release()}")
line(f"Machine     {platform.machine()}")

try:
    import customtkinter as ctk
    line(f"CustomTkinter {getattr(ctk, '__version__', 'unknown')}")
except Exception as exc:
    line(f"\nCustomTkinter is not installed: {exc}")
    line("Run:  pip install customtkinter pillow")
    sys.exit(1)

try:
    import PIL
    line(f"Pillow      {PIL.__version__}")
except Exception:
    line("Pillow is not installed. Run: pip install pillow")
    sys.exit(1)

from PIL import Image

# ---------------------------------------------------------------- the bank
line("\n" + "-" * 74)
line("YOUR QUESTION BANK")
line("-" * 74)

import config
import database
import question_repo

database.init_db()
summary = question_repo.bank_summary()
line(f"  Questions imported                            {summary['total']:>9,}")
for section, count in summary["by_section"].items():
    line(f"    {section:<42}{count:>9,}")

db_bytes = os.path.getsize(config.QUESTION_DB) if os.path.exists(config.QUESTION_DB) else 0
line(f"  questions.db size                             {db_bytes / 1e6:>9.1f} MB")

image_dir = config.IMAGE_DIR
image_files = []
if os.path.isdir(image_dir):
    image_files = [os.path.join(image_dir, f) for f in os.listdir(image_dir)
                   if f.lower().endswith(".png")]
total_image_bytes = sum(os.path.getsize(f) for f in image_files[:4000])
line(f"  Image files                                   {len(image_files):>9,}")
line(f"  Images total (first 4000)                     {total_image_bytes / 1e6:>9.1f} MB")

# How big are the images REALLY? This is the number that decides everything.
sizes, pixels = [], []
sample = image_files[:40]
for path in sample:
    try:
        with Image.open(path) as img:
            sizes.append(img.size)
            pixels.append(img.size[0] * img.size[1])
    except Exception:
        pass
if pixels:
    widest = max(s[0] for s in sizes)
    tallest = max(s[1] for s in sizes)
    line(f"  Image size: median {int(statistics.median(p) / 1e6):,.0f} MP, "
         f"largest {widest}x{tallest}")
    line(f"  Median dimensions                             "
         f"{int(statistics.median([s[0] for s in sizes]))}x"
         f"{int(statistics.median([s[1] for s in sizes]))}")

# ---------------------------------------------------------------- image decode
line("\n" + "-" * 74)
line("IMAGE DECODING  (the old #1 cost)")
line("-" * 74)

import ui_kit as ui

if sample:
    ui.clear_image_cache()
    target = sample[0]

    def cold_decode():
        ui.clear_image_cache()
        ui.decode_scaled(target, 860)

    cold = bench("Decode+scale one question image (cold)", cold_decode, repeat=5)

    ui.clear_image_cache()
    ui.load_scaled_image(target, 860)
    warm = bench("Same image from cache (warm)",
                 lambda: ui.load_scaled_image(target, 860), repeat=20)
    line(f"  -> cache speedup: {cold / max(warm, 0.001):.0f}x")
else:
    cold = 0
    line("  No images found, run sat_importer.py first.")

# ---------------------------------------------------------------- CTk widgets
line("\n" + "-" * 74)
line("CUSTOMTKINTER WIDGET COST  (the suspected #1 cost now)")
line("-" * 74)

root = ctk.CTk()
root.geometry("500x400")
root.title("Cat SAT benchmark, closing shortly")
root.update()

try:
    scaling = ctk.ScalingTracker.get_window_scaling(root)
except Exception:
    scaling = 1.0
line(f"  Window scaling (Windows display scaling)      {scaling:>9.2f}")
line(f"  Screen                                        "
     f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}")

host = ctk.CTkFrame(root)
host.pack()


def make(widget_cls, n=100, **kw):
    holder = ctk.CTkFrame(host)
    made = []
    for _ in range(n):
        made.append(widget_cls(holder, **kw))
    root.update_idletasks()
    holder.destroy()
    return made


line("")
per_frame = bench("Create 100 CTkFrame", lambda: make(ctk.CTkFrame, 100), repeat=3) / 100
per_label = bench("Create 100 CTkLabel", lambda: make(ctk.CTkLabel, 100, text="x"), repeat=3) / 100
per_button = bench("Create 100 CTkButton",
                   lambda: make(ctk.CTkButton, 100, text="x"), repeat=3) / 100
per_bar = bench("Create 100 CTkProgressBar",
                lambda: make(ctk.CTkProgressBar, 100), repeat=3) / 100


def make_scrollables():
    holder = ctk.CTkFrame(host)
    frames = [ctk.CTkScrollableFrame(holder) for _ in range(10)]
    root.update_idletasks()
    holder.destroy()


per_scroll = bench("Create 10 CTkScrollableFrame", make_scrollables, repeat=3) / 10

line("")
line(f"  Per CTkFrame           {per_frame:>7.2f} ms")
line(f"  Per CTkLabel           {per_label:>7.2f} ms")
line(f"  Per CTkButton          {per_button:>7.2f} ms")
line(f"  Per CTkProgressBar     {per_bar:>7.2f} ms")
line(f"  Per CTkScrollableFrame {per_scroll:>7.2f} ms   <-- usually the expensive one")

# What a real screen costs, extrapolated from the measured per-widget cost.
avg_widget = statistics.mean([per_frame, per_label, per_button])
line("")
line("  Extrapolated screen build cost at these rates:")
for name, count in (("Today's Plan", 142), ("Home", 67), ("Drill setup", 54),
                    ("Error log (12 rows)", 432), ("Review (20 rows)", 292),
                    ("Review, un-paginated 98q", 1700)):
    line(f"    {name:<40}{count * avg_widget:>9.0f} ms")

# ---------------------------------------------------------------- packing
line("\n" + "-" * 74)
line("LAYOUT COST")
line("-" * 74)


def pack_100():
    holder = ctk.CTkFrame(host)
    for _ in range(100):
        ctk.CTkLabel(holder, text="row").pack(fill="x")
    holder.pack()
    root.update()          # force a real layout + redraw
    holder.destroy()


bench("Create + pack + render 100 labels", pack_100, repeat=3)


def pack_into_scrollable():
    holder = ctk.CTkScrollableFrame(host, width=400, height=300)
    for _ in range(100):
        ctk.CTkLabel(holder, text="row").pack(fill="x")
    holder.pack()
    root.update()
    holder.destroy()


bench("Same, inside a CTkScrollableFrame", pack_into_scrollable, repeat=3)

# ---------------------------------------------------------------- database
line("\n" + "-" * 74)
line("DATABASE")
line("-" * 74)

import adaptive_engine as engine
import attempt_repo
import random

bench("bank_summary()", question_repo.bank_summary, repeat=5)
bench("list_domains('Math')", lambda: question_repo.list_domains("Math"), repeat=5)
bench("fetch all Math questions",
      lambda: question_repo.fetch(section="Math", shuffle=False), repeat=3)
bench("build one 27q adaptive module",
      lambda: engine.build_module("Reading and Writing", 1, "baseline",
                                  rng=random.Random(1)), repeat=3)
bench("overall_stats()", attempt_repo.overall_stats, repeat=5)
bench("seen_counts()", attempt_repo.seen_counts, repeat=3)
bench("mastered_question_ids()", attempt_repo.mastered_question_ids, repeat=3)

stats = attempt_repo.overall_stats()
line(f"\n  Attempts recorded so far                      {stats['total']:>9,}")
prog_bytes = os.path.getsize(config.PROGRESS_DB) if os.path.exists(config.PROGRESS_DB) else 0
line(f"  progress.db size                              {prog_bytes / 1e6:>9.1f} MB")

# ---------------------------------------------------------------- verdict
line("\n" + "=" * 74)
line("VERDICT")
line("=" * 74)

widget_cost_20_rows = 292 * avg_widget
if widget_cost_20_rows > 400:
    line("  CustomTkinter widget creation is the bottleneck.")
    line(f"  A 20-row review screen costs ~{widget_cost_20_rows:.0f} ms just to build widgets.")
    line("  No amount of Python optimisation fixes this, the toolkit is the cost.")
elif cold > 150:
    line("  Image decoding is still expensive on this machine.")
    line(f"  One cold decode is {cold:.0f} ms; the cache hides most but not all of it.")
else:
    line("  Neither widgets nor images look pathological here.")
    line("  The lag may be elsewhere, say what specifically feels slow.")

line("")
line("Paste everything above back into the chat.")
line("=" * 74)

root.after(200, root.destroy)
try:
    root.mainloop()
except Exception:
    pass

try:
    with open(os.path.join(HERE, "benchmark_report.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(REPORT))
    print(f"\n(also saved to dev_tests/benchmark_report.txt)")
except Exception:
    pass
