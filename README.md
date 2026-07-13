# 📚 kindle2pdf

> 🌐 日本語: [README.ja.md](README.ja.md)

A self-scanning tool that turns Kindle books you own into **searchable PDFs** on your Mac: screenshot → OCR → searchable PDF.

## ⚖️ Legal & disclaimer (read first)

- 🔓 **Does NOT remove DRM.** It screenshots what the Kindle app draws on screen and adds an OCR text layer — closer to "self-scanning" than decryption.
- 👤 **Personal use of books you purchased only.** No redistribution or sharing.
- ⚠️ **Provided as-is, with no warranty. Use at your own risk.** You are responsible for complying with the Kindle / Amazon terms of service and the laws of your jurisdiction.

See chapter 0 of [`docs/kindle2pdf/01_spec-design.md`](docs/kindle2pdf/01_spec-design.md) for details (Japanese).

## 📦 What it is / status

- 🖼️ Produces a searchable PDF (page image + invisible text layer) from screenshots.
- 🖥️ **Target environment**: macOS (Apple Silicon assumed).
- 🚧 **Status**: the 4-stage pipeline (P1–P8) is **implemented** and runs end-to-end, from capture to searchable PDF. That said, this is an **individual PoC-stage project — macOS only, no warranty.**

## 🏗 Architecture (4-stage pipeline)

```text
capture    → controls Kindle, captures pages, detects last page (only stage that touches Kindle / real time)
preprocess → crop & normalize (1 shot = 1 page)                        (batch / no spread splitting)
ocr        → Apple Vision OCR (extraction with coordinates)            (batch)
build      → searchable PDF from image + invisible text layer          (batch)
```

**Separation of concerns**: only `capture.py` touches Kindle. preprocess / ocr / build take already-captured images as input, so they can be re-run any number of times without operating Kindle. Interruption / resume is tracked in `state.json`.

Key decisions (validated on real hardware in a PoC):

- **OCR = Apple Vision (`ocrmac`)** — almost no stray spaces in Japanese text, so phrase search does not break.
- **Capture = `screencapture -x` CLI** — avoids the floating thumbnail bleeding into shots.
- **Capture region = window auto-detection + dynamic title-bar crop** — no configuration needed. It re-detects the Kindle window on every shot, captures via `screencapture -l`, and trims only the macOS title-bar band whose height is measured live via Accessibility (AX). Because the traffic-light buttons sit centered in that band, it computes `band height = 2 × (button center − window top)` each time — so it holds **no fixed px**, survives retina-scale changes, and stays correct even on cover pages. It never trims the body's white margins or running heads. **A normal (non-fullscreen) window is required** (fullscreen blocks background auto page-turn at the OS level). Hide Kindle's own progress footer/header via Kindle's display settings (immersive view).
- **Pagination = 1 shot = 1 page (no splitting)** — left/right spread splitting was removed. It captures the window content as-is; single-page vs. spread is chosen by the **Kindle window width**. Make the Kindle window half-screen-width and Kindle shows a single page (good for text); widen it to full-screen-width and Kindle shows a spread, which becomes one wide PDF page (good for manga — running heads and large panels are not cut at the gutter). Both stay in a normal window; macOS fullscreen is not used (it blocks auto page-turn).
- **Stop detection = pHash (distance ≤ 2 = identical)** — stable last-page detection given the thumbnail is removed.
- **Coordinate transform = no Y flip** — both Vision and reportlab use a bottom-left origin.

## ▶️ Usage

Just run the interactive wizard and answer its questions — it runs everything from capture to searchable PDF. **No per-book config editing and no activating a virtualenv on each run** (the Python core is installed once).

> [!IMPORTANT]
> **What you need** (first time only):
> - **Node.js ≥ 18** (used to run `npx`)
> - **The Python core** (the capture engine — `pip install` it once via the "Development" section below)
> - **Two macOS permissions** (Screen Recording, Accessibility — see "Setup" below)
>
> `npx kindle2pdf` internally calls `python -m kindle2pdf`, so without the Python core it stops right after launch with "cannot start the Python core."

```bash
# 1. Open the target book in Kindle at page 1
# 2. Launch the wizard and answer its questions
npx kindle2pdf
```

It effectively asks two things:

- 📖 **Book title** (becomes the output PDF filename)
- 📐 **Page layout** (single page / spread) — it also tells you to make the Kindle window half-width / full-width

Answer, and capture starts, progress shows as a spinner, and the PDF opens automatically when done. If interrupted, pick the same book again to resume from where it stopped.

> [!NOTE]
> `npx kindle2pdf` is a Node interactive front end. The capture engine itself is the Python core.
> Advanced users can drive the Python core directly for non-interactive full control (see "Development").

### 🔑 Setup (macOS permissions)

The process that captures and turns pages (the terminal you launched the wizard from) needs **two permissions**. OCR and PDF building need no extra permission.

| Permission | Used for | Without it |
|------|------|--------------|
| 🖥️ Screen Recording | capturing pages with `screencapture` | shots contain only the desktop wallpaper, not the book |
| ⌨️ Accessibility | turning pages (key events), bringing Kindle to front, measuring title-bar height via AX | page turns fail with `-1719` / capture stalls because title-bar height can't be measured |

- After granting, **restart the terminal** (especially for Screen Recording).
- The Kindle app name varies by environment, but is **auto-detected without configuration**. It verifies existence via AppleScript in the order `"Amazon Kindle"` → `"Kindle"`, caches the one that works, and reuses it.
  - Only for an unusual app name that auto-detection can't resolve, set `capture.app_name` in `config.yaml` manually (troubleshooting only).

## 🗂 Repository layout

```text
kindle2pdf/
├─ pyproject.toml
├─ config.example.yaml
├─ src/kindle2pdf/         # cli / config / state / imaging / capture / preprocess / ocr / build_pdf / pipeline
├─ cli/                    # the Node interactive front end for npx kindle2pdf
├─ tests/                  # test_*.py (pytest, pure logic) + test_smoke.sh (CI)
│  └─ fixtures/            # real PoC images (copyrighted, not committed)
├─ docs/kindle2pdf/        # spec/design & implementation-plan docs (Japanese)
└─ .claude/               # AI-agent operations (roles, rules, knowledge)
```

## 🛠 Implementation status

The 4-stage pipeline (P1–P8) is implemented after a real-hardware PoC. Each ticket's acceptance criteria are below (details in [`docs/kindle2pdf/02_implementation-plan.md`](docs/kindle2pdf/02_implementation-plan.md), Japanese).

| # | Content | Acceptance criteria |
|---|------|-------------|
| P1 | region measurement (calibrate) | no UI / running heads in shots, body only |
| P2 | capture loop | saved to `raw/` in sequence with no gaps or dupes |
| P3 | last-page detection | stops at the end, zero false positives |
| P4 | crop (1 shot = 1 page) | each `pages/` image has no UI, window content as-is (no splitting) |
| P5 | Vision OCR | returns `(text, conf, [x,y,w,h])`, xy ∈ [0,1] |
| P6 | invisible-text-layer PDF | known words hit in search, glyph positions overlap the body |
| P7 | integration + resume | kill mid-run → re-run completes from where it stopped |
| P8 | hardening | stable over hundreds of pages; failed pages are logged and skipped to continue |

## 🧑‍💻 Development (for contributors)

Users only need `npx kindle2pdf`. The following is for developing/testing the core and for advanced non-interactive runs.

### 🐍 Python core setup

The system Python on macOS ships without a compiler and fails to build, so Homebrew Python + venv is required (to avoid PEP 668).

```bash
brew install python@3.12
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
# ocrmac is a macos extra; omit it on non-mac
pip install -e ".[macos,dev]"
```

### ⚙️ Non-interactive full control (advanced / CI)

Without the wizard, you can drive everything via `config.yaml` (two-step).

```bash
# prepare config
cp config.example.yaml config.yaml
# measure the capture region
python -m kindle2pdf calibrate --config config.yaml
# capture → preprocess → ocr → build, fully automatic
python -m kindle2pdf run --config config.yaml
# resume with the same command after an interruption
```

The `npx kindle2pdf` front end drives the core through this same path, passing flags (`--title` / `--reading-order` / `--progress json`).

### 🖥️ Node front-end development

```bash
# launch the wizard locally (bin: kindle2pdf)
node cli/index.mjs
# unit tests for the front end
node --test cli/test/*.test.mjs
```

## 🤖 On the development workflow

This repository is operated with [vibecorp](https://github.com/hirokimry/vibecorp) (an AI-agent role plugin, full preset), running from Issue to PR. See [vibecorp](https://github.com/hirokimry/vibecorp) for setup.
