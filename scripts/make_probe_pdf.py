"""Generate a small probe PDF that deliberately conflicts with the starter data.

The starter documents are official publications that mostly draw on the same
statistics, so they contain no high-confidence cross-document contradiction --
a real finding, but one that leaves the contradiction detector undemonstrated.
This writes a clearly labelled analyst note that restates a handful of figures
on the *same* basis and period with different numbers, so the detector can be
seen firing on something it has never been shown.

    python scripts/make_probe_pdf.py

It is a test fixture, not a source document: it says so on its own first line.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent.parent / "data" / "probe"

NOTES: dict[str, list[str]] = {
    "probe-note-delhivery.pdf": [
        "PROBE DOCUMENT - SYNTHETIC TEST FIXTURE, NOT A REAL PUBLICATION",
        "Desk note on Delhivery Limited, prepared 12 September 2024.",
        "",
        "The revenue from operations on consolidated basis for FY24 stood at",
        "Rs 79,900.00 million as against Rs 72,253.01 million for FY23.",
        "",
        "Express parcel shipments in FY24 were 705 Mn.",
        "",
        "The loss for FY24 on a consolidated basis stood at Rs 3,100.00 million.",
    ],
    "probe-note-india.pdf": [
        "PROBE DOCUMENT - SYNTHETIC TEST FIXTURE, NOT A REAL PUBLICATION",
        "Desk note on the Indian economy, prepared 12 September 2025.",
        "",
        "India's real GDP grew by 7.1 percent in FY2024/25.",
        "",
        "Headline inflation moderated to an average of 5.9 per cent during 2024-25.",
        "",
        "Real GDP growth for 2025-26 is projected at 6.5 per cent.",
    ],
}


def write(path: Path, lines: list[str]) -> None:
    document = pymupdf.open()
    page = document.new_page()
    y = 72
    for line in lines:
        if line:
            page.insert_text((72, y), line, fontsize=11, fontname="helv")
        y += 20
    document.save(path)
    document.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, lines in NOTES.items():
        write(OUT / name, lines)
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
