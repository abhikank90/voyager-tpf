"""Build the H2c second-rater kit (protocol §11) from a packets folder.

The kit contains only what the second rater may see:

    rater/plan_XX.md          the double-scored subset packets
    rater/rater_scores.csv    a blank score sheet (regenerated, never copied)
    H2C_RATER_GUIDE.md        the rater guide (docs/H2C_RATER_GUIDE.md)
    calib_A.md / calib_B.md   the two calibration packets
    calib_answers.md          the reference scores rendered as a table + notes

It must never include ``KEY_DO_NOT_SHARE.csv``, ``author/``, ``SAMPLING.json``,
``cal_B_source.json`` or any session/condition identifiers.

Usage:
    python scripts/build_rater_kit.py \\
        --packets results/v1.5/h2c \\
        --calibration results/v1.5/h2c_calibration \\
        --guide docs/H2C_RATER_GUIDE.md \\
        --out results/v1.5/h2c_rater_kit
"""
from __future__ import annotations

import argparse
import csv
import io
import shutil
import sys
import zipfile
from pathlib import Path

# Make the project root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.build_h2c_packets import SCORE_HEADER  # noqa: E402

# calibration source filename → the name the rater's kit uses (guide refers to
# calib_A.md / calib_B.md).
CALIB_FILES = (("cal_A.md", "calib_A.md"), ("cal_B.md", "calib_B.md"))


def _read_rows(path: Path) -> list[dict]:
    lines = [
        line for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return []
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def _blank_rater_sheet(packet_ids: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(SCORE_HEADER)
    for pid in packet_ids:
        writer.writerow([pid] + [""] * (len(SCORE_HEADER) - 1))
    return buffer.getvalue()


def _display_id(plan_id: str) -> str:
    """``cal_A`` → ``calib_A`` so the answer key matches the kit's file names."""
    return "calib_" + plan_id[4:] if plan_id.startswith("cal_") else plan_id


def render_calib_answers(reference_scores: Path) -> str:
    """Reference scores as a readable table plus the per-plan notes."""
    rows = _read_rows(reference_scores)
    columns = ["plan_id", "D1_temporal", "D2_budget", "D3_preference",
               "D4_logistics", "fully_coherent"]
    lines = ["# Calibration answer key", "", "## Scores", ""]
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("|" + "---|" * len(columns))
    for row in rows:
        display = _display_id((row.get("plan_id") or "").strip())
        lines.append("| " + " | ".join(
            [display] + [row.get(c, "") for c in columns[1:]]
        ) + " |")

    notes = [(_display_id((row.get("plan_id") or "").strip()), (row.get("notes") or "").strip())
             for row in rows]
    notes = [(pid, note) for pid, note in notes if note]
    lines += ["", "## Notes", ""]
    if notes:
        for pid, note in notes:
            lines.append(f"**{pid}**: {note}")
    else:
        lines.append("(no notes)")
    return "\n".join(lines) + "\n"


def build(packets_dir: Path, calibration_dir: Path, guide_path: Path, out_dir: Path) -> dict:
    """Assemble the rater kit and its zip. Returns a summary dict."""
    packets = Path(packets_dir)
    calibration = Path(calibration_dir)
    guide = Path(guide_path)
    out = Path(out_dir)

    rater_out = out / "rater"
    rater_out.mkdir(parents=True, exist_ok=True)

    packet_ids = []
    for src in sorted((packets / "rater").glob("plan_*.md")):
        shutil.copyfile(src, rater_out / src.name)
        packet_ids.append(src.stem)
    (rater_out / "rater_scores.csv").write_text(_blank_rater_sheet(packet_ids))

    shutil.copyfile(guide, out / guide.name)
    for src_name, dst_name in CALIB_FILES:
        shutil.copyfile(calibration / src_name, out / dst_name)
    (out / "calib_answers.md").write_text(render_calib_answers(calibration / "reference_scores.csv"))

    zip_path = Path(str(out) + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(out.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(out))

    return {"plans": len(packet_ids), "out": out, "zip": zip_path}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--packets", type=Path, required=True,
                        help="build_h2c_packets.py output folder")
    parser.add_argument("--calibration", type=Path, required=True,
                        help="results/v1.5/h2c_calibration folder")
    parser.add_argument("--guide", type=Path, default=Path("docs/H2C_RATER_GUIDE.md"))
    parser.add_argument("--out", type=Path, default=Path("results/v1.5/h2c_rater_kit"))
    args = parser.parse_args()

    result = build(args.packets, args.calibration, args.guide, args.out)
    print(f"rater kit: {result['plans']} plans -> {result['out']} + {result['zip']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
