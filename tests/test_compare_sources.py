"""
Tests for compare_sources.py — the migration guard that catches a job source
silently degrading on a new compute platform.

The two failure modes that matter are opposite: crying wolf on a noisy small
source (which would block a healthy cutover) and staying silent when an
IP-sensitive source gets throttled (which is the whole point of the tool).
Both are pinned here, using the real observed variance from run_stats.jsonl:
`indeed` legitimately swung 70 -> 46 between two healthy GitHub runs.
"""

import compare_sources as cs


def _baseline(series_by_source):
    """Build the baseline structure from {source: [run1, run2, ...]}."""
    runs = []
    for i in range(len(next(iter(series_by_source.values())))):
        runs.append({
            "platform": "github-actions",
            "ts": f"2026-07-{19 + i}T12:00:00+00:00",
            "sources": {s: v[i] for s, v in series_by_source.items()},
        })
    return cs.build_baseline(runs, "github-actions", window=5)


class TestNoFalsePositives:
    def test_noisy_small_source_within_historical_range_is_not_flagged(self):
        # indeed's real observed range; a target inside it must not be reported
        base = _baseline({"indeed": [70, 46, 63, 58]})
        findings, _ = cs.compare(base, {"indeed": 43})
        assert findings == []

    def test_uniform_mild_dip_across_sources_is_not_flagged(self):
        base = _baseline({"Greenhouse": [2806, 2558, 2700], "Ashby": [2377, 2376, 2380]})
        findings, _ = cs.compare(base, {"Greenhouse": 2400, "Ashby": 2200})
        assert findings == []

    def test_source_already_dead_before_migration_is_not_a_regression(self):
        # BraveSearch died on GitHub Actions before the cutover
        base = _baseline({"BraveSearch": [17, 19, 0, 0]})
        findings, pre_existing = cs.compare(base, {"BraveSearch": 0})
        assert findings == []
        assert "BraveSearch" in pre_existing

    def test_growth_is_never_a_regression(self):
        base = _baseline({"linkedin": [396, 360, 378]})
        findings, _ = cs.compare(base, {"linkedin": 500})
        assert findings == []


class TestCatchesRealDegradation:
    def test_ip_sensitive_source_throttled_is_flagged(self):
        base = _baseline({"linkedin": [396, 360, 378]})
        findings, _ = cs.compare(base, {"linkedin": 8})
        assert len(findings) == 1
        assert findings[0]["source"] == "linkedin"
        assert findings[0]["ip_sensitive"] is True

    def test_ip_sensitive_source_blocked_to_zero_is_gone(self):
        base = _baseline({"indeed": [70, 46, 63]})
        findings, _ = cs.compare(base, {"indeed": 0})
        assert findings[0]["severity"] == "GONE"

    def test_healthy_source_dropping_to_zero_is_flagged(self):
        base = _baseline({"Greenhouse": [2806, 2558, 2700]})
        findings, _ = cs.compare(base, {"Greenhouse": 0})
        assert findings[0]["severity"] == "GONE"

    def test_ip_sensitive_gets_a_tighter_threshold_than_ordinary_source(self):
        # Same 30% drop below floor: flagged for LinkedIn, tolerated elsewhere
        ip = _baseline({"linkedin": [400, 400, 400]})
        ordinary = _baseline({"Personio": [400, 400, 400]})
        ip_findings, _ = cs.compare(ip, {"linkedin": 280})
        ord_findings, _ = cs.compare(ordinary, {"Personio": 280})
        assert ip_findings and not ord_findings

    def test_missing_source_key_counts_as_zero(self):
        base = _baseline({"XING": [143, 142, 140]})
        findings, _ = cs.compare(base, {})  # source absent entirely
        assert findings[0]["severity"] == "GONE"


class TestPlatformTagging:
    def test_runs_without_platform_field_are_treated_as_github_actions(self):
        # Stats lines predate the platform field; they must still form a baseline
        assert cs._platform({"ts": "x", "sources": {}}) == "github-actions"

    def test_explicit_platform_is_respected(self):
        assert cs._platform({"platform": "aws-lambda"}) == "aws-lambda"
