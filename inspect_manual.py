from docx import Document
from pathlib import Path
import json

src = Path(r"C:\Users\Administrator\Desktop\A股龙头战法完整手册（实战版）.docx")
out = Path(r"D:\deepseek-harness\default-workspace\manual_extract.txt")

doc = Document(src)
lines = []
lines.append("=== CORE PROPERTIES ===")
cp = doc.core_properties
for attr in ("title", "subject", "author", "last_modified_by", "created", "modified", "revision"):
    try:
        lines.append(f"{attr}: {getattr(cp, attr)}")
    except Exception as exc:
        lines.append(f"{attr}: <{exc}>")

lines.append(f"\n=== PARAGRAPHS ({len(doc.paragraphs)}) ===")
for i, p in enumerate(doc.paragraphs):
    text = p.text.replace("\t", "    ").strip()
    if text:
        lines.append(f"[P{i}] ({p.style.name}) {text}")

lines.append(f"\n=== TABLES ({len(doc.tables)}) ===")
for ti, table in enumerate(doc.tables):
    lines.append(f"\n--- TABLE {ti} ({len(table.rows)} rows x {len(table.columns)} cols) ---")
    for ri, row in enumerate(table.rows):
        cells = []
        for cell in row.cells:
            cells.append(cell.text.replace("\n", " / ").replace("\t", " ").strip())
        lines.append(f"R{ri}: " + " | ".join(cells))

lines.append(f"\n=== SECTIONS ({len(doc.sections)}) ===")
for si, sec in enumerate(doc.sections):
    lines.append(
        f"S{si}: page={sec.page_width}x{sec.page_height}, margins L/R/T/B="
        f"{sec.left_margin}/{sec.right_margin}/{sec.top_margin}/{sec.bottom_margin}"
    )

# Count inline shapes/images
lines.append(f"\n=== INLINE SHAPES ({len(doc.inline_shapes)}) ===")
for i, shape in enumerate(doc.inline_shapes):
    lines.append(f"shape {i}: type={shape.type}, {shape.width}x{shape.height}")

out.write_text("\n".join(lines), encoding="utf-8")
print(f"written {out} ({out.stat().st_size} bytes)")
print(f"paragraphs={len(doc.paragraphs)} tables={len(doc.tables)} sections={len(doc.sections)}")
