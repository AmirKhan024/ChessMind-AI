import json
import logging

from prometheus_client import generate_latest
from prometheus_client.registry import CollectorRegistry

from app import logs, metrics


def scrape(monkeypatch) -> str:
    monkeypatch.setattr(metrics, "_queue_depths", lambda: {"analysis": 3})
    reg = CollectorRegistry()
    reg.register(metrics.RedisCollector())
    return generate_latest(reg).decode()


def test_counters_and_histograms_from_many_processes_add_up(monkeypatch):
    metrics.inc("positions_analysed", 40)
    metrics.inc("positions_analysed", 2)
    metrics.inc("grounding_checks", kind="report", result="pass")
    metrics.observe("llm_latency_seconds", 1.5)
    metrics.observe("llm_latency_seconds", 100)
    out = scrape(monkeypatch)
    assert "chessmind_positions_analysed_total 42.0" in out
    assert 'chessmind_grounding_checks_total{kind="report",result="pass"} 1.0' in out
    # Buckets are cumulative: 1.5 s falls in le=2 and above; 100 s only in +Inf.
    assert 'chessmind_llm_latency_seconds_bucket{le="1"} 0.0' in out
    assert 'chessmind_llm_latency_seconds_bucket{le="2"} 1.0' in out
    assert 'chessmind_llm_latency_seconds_bucket{le="+Inf"} 2.0' in out
    assert "chessmind_llm_latency_seconds_sum 101.5" in out
    assert 'chessmind_queue_depth{queue="analysis"} 3.0' in out


def test_metrics_never_break_the_caller(monkeypatch):
    from redis import RedisError

    class Down:
        def __getattr__(self, name):
            def fail(*a, **k):
                raise RedisError("down")

            return fail

    monkeypatch.setattr(metrics, "_redis", lambda: Down())
    metrics.inc("games_analysed")
    metrics.observe("report_seconds", 3)
    assert scrape(monkeypatch) == ""


def test_json_log_lines_carry_the_job_id(capsys):
    logs.setup()
    token = logs.job_id.set("job-123")
    try:
        logging.getLogger("t").info("analysed", extra={"positions": 80})
    finally:
        logs.job_id.reset(token)
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["msg"] == "analysed" and line["job_id"] == "job-123"
    assert line["positions"] == 80 and line["level"] == "info"
