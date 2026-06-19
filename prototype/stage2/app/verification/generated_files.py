from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def build_dummy_pdf(path: Path, title: str, lines: list[str]) -> Path:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    _width, height = A4
    y = height - 72
    pdf.setFont("Helvetica", 14)
    pdf.drawString(72, y, title)
    y -= 32
    pdf.setFont("Helvetica", 10)
    for line in lines:
        pdf.drawString(72, y, line[:100])
        y -= 18
        if y < 72:
            pdf.showPage()
            pdf.setFont("Helvetica", 10)
            y = height - 72
    pdf.save()
    return path


def build_default_generated_files(generated_dir: Path, model_name: str) -> dict[str, Path]:
    now = datetime.now().isoformat()
    personnel = build_dummy_pdf(
        generated_dir / "nursery_personnel_form.pdf",
        "Nursery Personnel Form",
        [
            f"Generated at: {now}",
            f"Model tag: {model_name}",
            "Person: test",
            "Role: nursery acceptance",
            "Note: automated prototype attachment",
        ],
    )
    acceptance = build_dummy_pdf(
        generated_dir / "acceptance_file.pdf",
        "Acceptance File",
        [
            f"Generated at: {now}",
            f"Model tag: {model_name}",
            "Authority: Rongshui Forestry Bureau",
            "Note: automated prototype attachment",
        ],
    )
    return {
        "personnel_file": personnel,
        "acceptance_file": acceptance,
        "apply_file": acceptance,
    }
