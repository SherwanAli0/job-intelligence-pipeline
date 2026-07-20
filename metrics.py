"""
metrics.py — publish run telemetry to CloudWatch.

Cost per run has been quoted from memory all along ("about EUR 3-5/month").
The API reports exact token usage and the run knows its own duration, so the
pipeline now measures what it actually spends and publishes it. In CloudWatch
that becomes a graph: cost drift, a scrape that starts taking twice as long, or
a digest that quietly shrinks to nothing are all visible without reading logs.

No-op unless JOBHUNTER_METRICS=1, so laptop and GitHub Actions runs neither
need AWS credentials nor pay for metrics they will not look at.
"""

import os

NAMESPACE = "JobHunter"


def enabled() -> bool:
    return os.environ.get("JOBHUNTER_METRICS", "").strip() in ("1", "true", "yes")


def publish(*, duration_seconds: float, llm_cost_usd: float, scraped: int,
            digest: int, near: int, email_ok: bool,
            phases: dict | None = None) -> None:
    """
    Send one datapoint per metric. Never raises: telemetry must not be able to
    fail a run that otherwise succeeded.
    """
    if not enabled():
        return
    try:
        import boto3
        cw = boto3.client("cloudwatch")

        # Fargate compute cost for this run, from the task size we actually
        # configured (0.5 vCPU / 2 GB) at eu-central-1 rates.
        hours = duration_seconds / 3600.0
        fargate_usd = hours * (0.5 * 0.04656 + 2 * 0.00511)

        data = [
            {"MetricName": "RunDurationSeconds", "Value": float(duration_seconds), "Unit": "Seconds"},
            {"MetricName": "LLMCostUSD", "Value": float(llm_cost_usd), "Unit": "None"},
            {"MetricName": "ComputeCostUSD", "Value": round(fargate_usd, 4), "Unit": "None"},
            {"MetricName": "TotalCostUSD", "Value": round(llm_cost_usd + fargate_usd, 4), "Unit": "None"},
            {"MetricName": "JobsScraped", "Value": float(scraped), "Unit": "Count"},
            {"MetricName": "DigestSize", "Value": float(digest), "Unit": "Count"},
            {"MetricName": "NearMisses", "Value": float(near), "Unit": "Count"},
            # 1/0 so an alarm can fire on a run that scraped fine but never
            # delivered — the failure mode that used to be invisible.
            {"MetricName": "EmailDelivered", "Value": 1.0 if email_ok else 0.0, "Unit": "Count"},
        ]
        for name, seconds in (phases or {}).items():
            data.append({"MetricName": f"Phase_{name}", "Value": float(seconds), "Unit": "Seconds"})

        # PutMetricData accepts at most 20 datapoints per call.
        for i in range(0, len(data), 20):
            cw.put_metric_data(Namespace=NAMESPACE, MetricData=data[i:i + 20])
        print(f"  [Metrics] published {len(data)} datapoints to {NAMESPACE} "
              f"(run cost ~${llm_cost_usd + fargate_usd:.4f})")
    except Exception as e:
        print(f"  [Metrics] publish failed (ignored): {e}")
