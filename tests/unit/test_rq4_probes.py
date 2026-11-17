"""RQ4 probe states: schema, rule construction, and the labeling sheet."""
import csv
import io
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.collaboration_hub import CollaborationHubAgent

ROOT = Path(__file__).resolve().parent.parent.parent
PROBES_PATH = ROOT / "results" / "v1.5" / "rq4_probes.json"
SHEET_PATH = ROOT / "results" / "v1.5" / "rq4_labeling_sheet.csv"
KEY_PATH = ROOT / "results" / "v1.5" / "rq4_sheet_key.csv"
RULES = ("location_mismatch", "timing_inefficiency", "weather_activity_mismatch")
BANNED_WORDS = ("fires", "silent", "bait", "clean", "pilot")


@pytest.fixture
def hub():
    with patch("agents.collaboration_hub.Anthropic"), \
         patch("agents.collaboration_hub.get_api_config") as cfg:
        cfg.return_value.llm.api_key = "ci-test-key"
        cfg.return_value.llm.collaboration_hub_model = "ci-model"
        return CollaborationHubAgent()


@pytest.fixture(scope="module")
def doc():
    with open(PROBES_PATH) as fh:
        return json.load(fh)


def _rule_output(hub, state):
    out = {rule: "silent" for rule in RULES}
    for conflict in hub.detect_conflicts_only(state):
        if conflict.get("type") in out:
            out[conflict["type"]] = "fires"
    return out


def test_probe_file_loads(doc):
    assert doc["version"] == 1
    assert len(doc["probes"]) >= 20


def test_probe_ids_are_unique(doc):
    ids = [p["id"] for p in doc["probes"]]
    assert len(ids) == len(set(ids))


def test_every_gold_is_null(doc):
    assert all(p.get("gold") is None for p in doc["probes"])


def test_pilot_probes_carry_v141_truth(doc):
    for p in doc["probes"]:
        if p["group"] == "pilot":
            assert p["pilot_truth_v141"] is not None
        else:
            assert p["pilot_truth_v141"] is None


def test_every_state_runs_through_rules(doc, hub):
    for p in doc["probes"]:
        hub.detect_conflicts_only(p["state"])  # must not raise


def test_construction_matches_rules(doc, hub):
    for p in doc["probes"]:
        assert p["construction"] == _rule_output(hub, p["state"]), (
            f"{p['id']}: construction {p['construction']} "
            f"!= rules {_rule_output(hub, p['state'])}"
        )


def test_labeling_sheet_regenerates_byte_identical(doc):
    from scripts.eval_hybrid_detection import build_labeling_sheet

    assert build_labeling_sheet(doc).encode("utf-8") == SHEET_PATH.read_bytes()


def test_sheet_key_regenerates_byte_identical(doc):
    from scripts.eval_hybrid_detection import build_sheet_key

    assert build_sheet_key(doc).encode("utf-8") == KEY_PATH.read_bytes()


def test_state_summaries_are_blind(doc):
    from scripts.eval_hybrid_detection import state_summary

    for p in doc["probes"]:
        summary = state_summary(p["state"]).lower()
        assert p["name"].lower() not in summary, f"{p['id']}: probe name leaked"
        assert p["group"].lower() not in summary, f"{p['id']}: group leaked"
        for word in BANNED_WORDS:
            assert word not in summary, f"{p['id']}: banned word {word!r} in summary"


def _leaf_values(obj, skip=("collaboration_round",)):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in skip:
                continue
            yield from _leaf_values(value, skip)
    elif isinstance(obj, list):
        if obj:
            for item in obj:
                yield from _leaf_values(item, skip)
    elif obj is not None:
        yield str(obj).lower()


def test_state_summary_is_complete(doc):
    from scripts.eval_hybrid_detection import state_summary

    for p in doc["probes"]:
        summary = state_summary(p["state"]).lower()
        for value in _leaf_values(p["state"]):
            assert value in summary, f"{p['id']}: value {value!r} missing from summary"


def _parse_rows(text):
    lines = [line for line in text.splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    reader = csv.reader(lines)
    header = next(reader)
    return [dict(zip(header, row)) for row in reader]


def _serialize(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["sheet_id", "state_summary", "gold_types", "notes"])
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _filled_sheet_text(doc, labeler):
    from scripts.eval_hybrid_detection import build_labeling_sheet

    rows = _parse_rows(build_labeling_sheet(doc))
    for row in rows:
        row["gold_types"] = labeler(row)
    return _serialize(rows)


@pytest.fixture
def tmp_probes(tmp_path):
    probes = tmp_path / "rq4_probes.json"
    shutil.copy(PROBES_PATH, probes)
    shutil.copy(KEY_PATH, tmp_path / "rq4_sheet_key.csv")
    return probes


def test_merge_gold_happy_path(tmp_probes):
    from scripts.eval_hybrid_detection import merge_gold

    doc = json.loads(tmp_probes.read_text())
    key = {r["sheet_id"]: r["probe_id"] for r in _parse_rows((tmp_probes.parent / "rq4_sheet_key.csv").read_text())}
    sheet = tmp_probes.parent / "filled.csv"
    sheet.write_text(_filled_sheet_text(
        doc,
        lambda r: "location_mismatch;timing_inefficiency" if r["sheet_id"] == "S01" else "none",
    ))
    merged = merge_gold(str(tmp_probes), str(sheet))
    by_id = {p["id"]: p["gold"] for p in merged["probes"]}
    assert by_id[key["S01"]] == ["location_mismatch", "timing_inefficiency"]
    for probe_id, gold in by_id.items():
        if probe_id != key["S01"]:
            assert gold == [], f"{probe_id}: expected [] got {gold}"
    # the file was rewritten with gold filled in
    assert all(p["gold"] is not None for p in json.loads(tmp_probes.read_text())["probes"])


def _assert_refused(tmp_probes, doc, labeler, mutate=None, match=None):
    from scripts.eval_hybrid_detection import build_labeling_sheet, merge_gold

    before = tmp_probes.read_text()
    rows = _parse_rows(build_labeling_sheet(doc))
    for row in rows:
        row["gold_types"] = labeler(row)
    if mutate:
        mutate(rows)
    sheet = tmp_probes.parent / "filled.csv"
    sheet.write_text(_serialize(rows))
    with pytest.raises(ValueError, match=match):
        merge_gold(str(tmp_probes), str(sheet))
    assert tmp_probes.read_text() == before


def test_merge_gold_blank_refused(tmp_probes):
    doc = json.loads(tmp_probes.read_text())
    _assert_refused(tmp_probes, doc, lambda r: "none",
                    mutate=lambda rows: rows[0].update(gold_types=""), match="blank")


def test_merge_gold_unknown_type_refused(tmp_probes):
    doc = json.loads(tmp_probes.read_text())
    _assert_refused(tmp_probes, doc, lambda r: "foo", match="unknown type")


def test_merge_gold_none_combined_refused(tmp_probes):
    doc = json.loads(tmp_probes.read_text())
    _assert_refused(tmp_probes, doc, lambda r: "none;location_mismatch", match="combined")


def test_merge_gold_unknown_sheet_id_refused(tmp_probes):
    doc = json.loads(tmp_probes.read_text())
    _assert_refused(tmp_probes, doc, lambda r: "none",
                    mutate=lambda rows: rows[0].update(sheet_id="S99"), match="sheet_ids")


def test_merge_gold_duplicate_sheet_id_refused(tmp_probes):
    doc = json.loads(tmp_probes.read_text())
    _assert_refused(tmp_probes, doc, lambda r: "none",
                    mutate=lambda rows: rows.append(dict(rows[0])), match="duplicate")


def test_merge_gold_stale_refused(tmp_probes):
    doc = json.loads(tmp_probes.read_text())
    _assert_refused(tmp_probes, doc, lambda r: "none",
                    mutate=lambda rows: rows[0].update(state_summary="tampered"), match="stale")
