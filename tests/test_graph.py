"""
Tests for the LangGraph pipeline orchestration.

The graph must be behaviour-preserving: the same stages run in the same order
with the same data. The one deliberate behaviour CHANGE is the quiet-day path —
as a linear function, "nothing new today" called sys.exit() before the stats
write, so quiet days left no trace in the run history. As a graph it is a
conditional edge to persist, so state and stats are still recorded.
"""

import pytest

from graph import build_graph


def _nodes(calls):
    """Fake nodes that record execution order and thread minimal state."""
    def scrape(state):
        calls.append("scrape")
        return {"seen": {}, "all_jobs": [{"id": "a"}, {"id": "b"}],
                "scraped": 2, "deduped": 2}

    def filt(state):
        calls.append("filter")
        return {"new_jobs": state.get("_force_new", [{"id": "a"}])}

    def score(state):
        calls.append("score")
        return {"scored": [dict(j, score=80) for j in state["new_jobs"]]}

    def rank(state):
        calls.append("rank")
        return {"top": state["scored"], "near": []}

    def notify(state):
        calls.append("notify")
        return {"email_ok": True}

    def persist(state):
        calls.append("persist")
        return {}

    return {"scrape": scrape, "filter": filt, "score": score,
            "rank": rank, "notify": notify, "persist": persist}


class TestTopology:
    def test_full_path_runs_every_stage_in_order(self):
        calls = []
        app = build_graph(_nodes(calls))
        app.invoke({"dry_run": True})
        assert calls == ["scrape", "filter", "score", "rank", "notify", "persist"]

    def test_quiet_day_skips_scoring_but_still_persists(self):
        """The behaviour change worth having: no new jobs means no scoring,
        ranking or emailing — but state and run stats are still written."""
        calls = []
        nodes = _nodes(calls)
        nodes["filter"] = lambda s: (calls.append("filter"), {"new_jobs": []})[1]
        app = build_graph(nodes)
        app.invoke({"dry_run": True})
        assert calls == ["scrape", "filter", "persist"]
        assert "score" not in calls and "notify" not in calls

    def test_state_flows_between_nodes(self):
        calls = []
        app = build_graph(_nodes(calls))
        final = app.invoke({"dry_run": True})
        assert final["scraped"] == 2
        assert final["top"][0]["score"] == 80
        assert final["email_ok"] is True

    def test_dry_run_flag_is_visible_to_every_node(self):
        seen_flags = []
        nodes = _nodes([])
        original = nodes["notify"]

        def notify(state):
            seen_flags.append(state.get("dry_run"))
            return original(state)

        nodes["notify"] = notify
        build_graph(nodes).invoke({"dry_run": True})
        assert seen_flags == [True]


class TestPhaseTiming:
    def test_every_executed_node_records_a_duration(self):
        calls = []
        final = build_graph(_nodes(calls)).invoke({"dry_run": True})
        phases = final["phases"]
        assert set(phases) == {"scrape", "filter", "score", "rank", "notify", "persist"}
        assert all(isinstance(v, float) and v >= 0 for v in phases.values())

    def test_skipped_nodes_are_absent_from_timing(self):
        calls = []
        nodes = _nodes(calls)
        nodes["filter"] = lambda s: {"new_jobs": []}
        final = build_graph(nodes).invoke({"dry_run": True})
        assert "score" not in final["phases"]


class TestRealNodesAreWired:
    def test_main_exposes_all_six_nodes(self):
        import main
        for name in ("scrape", "filter", "score", "rank", "notify", "persist"):
            assert callable(getattr(main, f"node_{name}")), name

    def test_main_builds_the_graph_rather_than_running_linearly(self):
        import inspect, main
        src = inspect.getsource(main.main)
        assert "build_graph" in src and "invoke" in src

    def test_persist_still_publishes_cloudwatch_metrics(self):
        """A refactor once dropped this call silently; the metric only appears
        in AWS, so nothing local would have failed."""
        import inspect, main
        assert "metrics.publish" in inspect.getsource(main.node_persist)
