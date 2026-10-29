"""CI model quality gate: the shipped peer model is the registered one and meets the limits."""

import json

import pytest

from ml import gate


def test_shipped_model_is_the_registered_model_and_passes_the_gate():
    registered = json.loads(gate.GATE_FILE.read_text())
    shipped = gate.score(gate.MODEL_DIR)
    for k in ("log_loss", "brier", "ece"):
        assert shipped[k] == pytest.approx(registered[k], abs=1e-6), (
            f"{k} differs from gate.json: register new models with `python -m ml.gate register`"
        )
    assert gate.failures(shipped, registered) == []


def test_gate_rejects_a_worse_or_miscalibrated_model():
    reg = {"log_loss": 0.200, "brier": 0.057, "ece": 0.003}
    assert gate.failures(reg, reg) == []
    assert gate.failures({**reg, "log_loss": 0.205}, reg)
    assert any("limit" in f for f in gate.failures({**reg, "ece": 0.012}, {**reg, "ece": 0.011}))
