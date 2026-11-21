"""Tests for scripts/build_rater_kit.py — fixture packet folder, no real packets."""
import zipfile
from pathlib import Path

from scripts import build_rater_kit as brk

FORBIDDEN = [
    "baseline", "targeted", "full_rerun", "broadcast", "two_way",
    "session_id", "session-abc123", "calibration_edit", "SECRET",
]


def _packets(root: Path) -> Path:
    packets = root / "packets"
    (packets / "rater").mkdir(parents=True)
    (packets / "author").mkdir(parents=True)
    (packets / "rater" / "plan_01.md").write_text("# plan one\nA clean packet.\n")
    (packets / "rater" / "plan_02.md").write_text("# plan two\nAnother clean packet.\n")
    # Filled score sheet must NOT be copied; the kit regenerates a blank one.
    (packets / "rater" / "rater_scores.csv").write_text(
        "plan_id,D1_temporal,D2_budget,D3_preference,D4_logistics,fully_coherent,notes\n"
        "plan_01,targeted,session-abc123,calibration_edit,P,P,Y,leak\n"
    )
    (packets / "author" / "plan_01.md").write_text("targeted session-abc123\n")
    (packets / "KEY_DO_NOT_SHARE.csv").write_text("plan_id,session_id,condition\n"
                                                  "plan_01,session-abc123,targeted\n")
    (packets / "SAMPLING.json").write_text('{"seed": 1, "n": 30, "session_ids": ["session-abc123"]}')
    return packets


def _calibration(root: Path) -> Path:
    cal = root / "calibration"
    cal.mkdir(parents=True)
    (cal / "cal_A.md").write_text("# cal A\nA clean calibration packet.\n")
    (cal / "cal_B.md").write_text("# cal B\nAnother clean calibration packet.\n")
    (cal / "cal_B_source.json").write_text('{"session_id": "session-abc123", "condition": "targeted"}')
    (cal / "reference_scores.csv").write_text(
        "plan_id,D1_temporal,D2_budget,D3_preference,D4_logistics,fully_coherent,notes\n"
        "cal_A,P,P,P,P,Y,\"Lands 13:00, Day 1 usable\"\n"
        "cal_B,P,F,P,F,N,\"Total $2,745 vs $2,000 budget\"\n"
    )
    return cal


def _guide(root: Path) -> Path:
    guide = root / "H2C_RATER_GUIDE.md"
    guide.write_text("# Rater guide\nScore the plans independently.\n")
    return guide


def _all_text(kit: Path) -> str:
    return "\n".join(f.read_text(errors="replace") for f in sorted(kit.rglob("*")) if f.is_file())


def test_kit_contains_only_rater_material(tmp_path):
    packets = _packets(tmp_path)
    calibration = _calibration(tmp_path)
    guide = _guide(tmp_path)
    out = tmp_path / "kit"

    result = brk.build(packets, calibration, guide, out)

    names = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert names == [
        "H2C_RATER_GUIDE.md",
        "calib_A.md",
        "calib_B.md",
        "calib_answers.md",
        "rater/plan_01.md",
        "rater/plan_02.md",
        "rater/rater_scores.csv",
    ]
    assert result["plans"] == 2

    # The score sheet is blank: header only, no leaked scores.
    sheet = (out / "rater" / "rater_scores.csv").read_text()
    assert "session-abc123" not in sheet and "targeted" not in sheet
    assert sheet.count("plan_0") == 2  # header's plan_id excluded

    # calib_answers renders the reference scores as a table + notes.
    answers = (out / "calib_answers.md").read_text()
    assert "| plan_id" in answers and "calib_B" in answers and "Lands 13:00" in answers

    text = _all_text(out)
    for needle in FORBIDDEN:
        assert needle not in text, needle


def test_kit_zip_matches_folder(tmp_path):
    packets = _packets(tmp_path)
    calibration = _calibration(tmp_path)
    guide = _guide(tmp_path)
    out = tmp_path / "kit"

    brk.build(packets, calibration, guide, out)
    zip_path = Path(str(out) + ".zip")
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path) as zf:
        entries = sorted(zf.namelist())
    assert "rater/plan_01.md" in entries
    assert "H2C_RATER_GUIDE.md" in entries
    for forbidden_file in ("KEY_DO_NOT_SHARE.csv", "SAMPLING.json", "cal_B_source.json"):
        assert not any(forbidden_file in e for e in entries)
    assert not any("author/" in e for e in entries)
