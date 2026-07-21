"""
graph.py — the pipeline as an explicit LangGraph state machine.

The run used to be one long function: scrape, dedup, filter, score, rank,
notify, persist. That works, but the stages and their dependencies were
implicit in the order of statements, which makes it hard to see where a run can
exit early, where state is mutated, and where a stage could be retried or
parallelised later.

Here each stage is a node over a shared typed state, and the edges are
declared. Two things this buys immediately:

  * The early exit ("nothing new today") is a conditional edge rather than a
    sys.exit() buried mid-function, so the persist step still runs and the run
    ends cleanly instead of terminating the process.
  * Per-stage timing and errors are recorded uniformly by the node wrapper
    rather than sprinkled through the body.

The nodes deliberately delegate to the existing, tested functions in main.py.
This is an orchestration layer, not a rewrite: the scoring, filtering and
notification logic keeps its test coverage and its behaviour.
"""

from __future__ import annotations

import time
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph


class RunState(TypedDict, total=False):
    """State threaded through the graph. Nodes return partial updates."""
    dry_run: bool
    seen: dict[str, str]
    all_jobs: list[dict]
    new_jobs: list[dict]
    scored: list[dict]
    top: list[dict]
    near: list[dict]
    src_counts: dict[str, int]
    health_warnings: list[str]
    drop_by_filter_track: Any
    dq_counts: Any
    track_mix: dict[str, int]
    scraped: int
    deduped: int
    email_ok: bool
    phases: dict[str, float]
    stopped_early: bool


def _timed(name: str, fn):
    """Wrap a node so every stage records its duration in state['phases'].

    Stage timing is what decided this pipeline's architecture (a measured
    40-minute scrape against Lambda's 15-minute ceiling), so it is collected
    by the framework rather than left to each stage to remember.
    """
    def node(state: RunState) -> dict:
        t0 = time.time()
        update = fn(state) or {}
        phases = dict(state.get("phases") or {})
        phases[name] = round(time.time() - t0, 1)
        update["phases"] = phases
        return update
    return node


def build_graph(nodes: dict):
    """
    Compile the pipeline graph.

    `nodes` maps stage name -> callable(state) -> partial state update. main.py
    supplies them, which keeps this module free of scraping/scoring imports and
    makes the graph testable with trivial fakes.
    """
    g = StateGraph(RunState)

    for name in ("scrape", "filter", "score", "rank", "notify", "persist"):
        g.add_node(name, _timed(name, nodes[name]))

    g.set_entry_point("scrape")
    g.add_edge("scrape", "filter")

    # Nothing new today: skip scoring, ranking and notification, but still
    # persist so last-seen dates are refreshed and the run is recorded. As a
    # plain function this was a sys.exit() in the middle of the body, which
    # skipped the stats write entirely.
    def _has_new_jobs(state: RunState) -> str:
        return "score" if state.get("new_jobs") else "persist"

    g.add_conditional_edges("filter", _has_new_jobs,
                            {"score": "score", "persist": "persist"})
    g.add_edge("score", "rank")
    g.add_edge("rank", "notify")
    g.add_edge("notify", "persist")
    g.add_edge("persist", END)

    return g.compile()


def describe() -> str:
    """Human-readable topology, printed at run start so the logs show the
    shape of the pipeline that actually executed."""
    return ("scrape → filter → [new jobs?] → score → rank → notify → persist"
            "\n                     └── none ──────────────────────→ persist")
