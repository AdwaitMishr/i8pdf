import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture(scope="session")
def probe_pdfs() -> list[Path]:
    """The small synthetic notes; regenerated if missing so tests are standalone."""
    folder = ROOT / "data" / "probe"
    pdfs = sorted(folder.glob("*.pdf")) if folder.exists() else []
    if not pdfs:
        sys.path.insert(0, str(ROOT / "scripts"))
        import make_probe_pdf
        make_probe_pdf.main()
        pdfs = sorted(folder.glob("*.pdf"))
    return pdfs


@pytest.fixture(scope="session")
def starter_pdf() -> Path:
    return ROOT / "data" / "starter" / "delhivery" / "02-delhivery-annual-report-fy24-excerpt.pdf"
