#!/usr/bin/env python3
"""Build docs/AIX-Store-Professor-Setup-Guide.pdf — 3 blocks only. Run: python docs/build_professor_pdf.py"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def ensure_fpdf() -> None:
    try:
        import fpdf  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "fpdf2", "-q"])


def _ascii(text: str) -> str:
    t = text
    for a, b in (
        ("\u2014", "-"),
        ("\u2013", "-"),
        ("\u2192", "->"),
        ("\u2022", "-"),
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2026", "..."),
        ("\u00a0", " "),
    ):
        t = t.replace(a, b)
    return t.encode("latin-1", "replace").decode("latin-1")


def main() -> None:
    ensure_fpdf()
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    root = Path(__file__).resolve().parents[1]
    out = root / "docs" / "AIX-Store-Professor-Setup-Guide.pdf"
    repo_url = "https://github.com/mohamedelgharawy851-spec/AiX-Store"

    env_text = _ascii((root / ".env.example").read_text(encoding="utf-8"))

    how_to = _ascii(
        "\n".join(
            [
                "1) git clone " + repo_url + ".git",
                "2) cd AiX-Store",
                "3) npm install",
                "4) python -m venv .venv",
                r"5) .\.venv\Scripts\pip install -r services\scraper-python\requirements.txt   (macOS/Linux: .venv/bin/pip)",
                "6) Create a file named .env in the repo root. Copy everything from the next page (ENV block) into it.",
                "7) Replace placeholders (<...>) with your real Supabase + Apify values.",
                "8) Start an Android emulator (Android Studio - Device Manager), then run:  npm run mobile:android",
                "   Or for Expo Go on a phone:  npm run mobile",
            ]
        )
    )

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.set_margins(14, 14, 14)

    def line(txt: str, size: int, style: str = "") -> None:
        pdf.set_font("Helvetica", style, size)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, size * 0.55, _ascii(txt), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.add_page()

    # --- Block 1: title ---
    line("AIX Store", 26, "B")
    pdf.ln(6)

    # --- Block 2: big URL + how to run ---
    line(repo_url, 16, "B")
    pdf.ln(4)
    line("How to run", 14, "B")
    pdf.ln(2)
    line(how_to, 11, "")
    pdf.ln(4)

    # --- Block 3: ENV copy-paste ---
    line("Copy into .env (repo root)", 14, "B")
    pdf.ln(2)
    pdf.set_font("Courier", "", 8)
    for ln in env_text.splitlines():
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 4, ln, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.output(str(out))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
