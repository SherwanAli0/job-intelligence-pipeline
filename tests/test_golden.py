"""
Golden-set pre-screen regression (the FREE calibration layer).

Every labeled case in golden/golden_set.jsonl either must be hard-disqualified
(with the labeled category) or must survive the pre-screen. Runs offline on
every push — a prompt/filter edit that flips a golden case fails CI.
The paid LLM band layer lives in calibrate.py (manual).
"""
import calibrate


def test_golden_prescreen_layer():
    rows = calibrate.load_golden()
    assert len(rows) >= 15, "golden set went missing?"
    failures, lines = calibrate.run_prescreen(rows)
    assert failures == 0, "Golden pre-screen regressions:\n" + "\n".join(
        l for l in lines if "FAIL" in l)
