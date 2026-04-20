"""Harness wiring tests — sha256, build_messages, fingerprint, model adjustments."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness import (  # noqa: E402
    CONDITIONS,
    ERROR_PREFIX,
    GEMMA2_MODEL,
    NUM_PREDICT,
    QWEN3_MODEL,
    SEEDS,
    TURN_BREAK,
    build_messages,
    cell_filename,
    is_transient_error,
    model_slug,
    prepare_for_model,
    sha256_hex,
)


def test_seven_conditions_locked() -> None:
    assert CONDITIONS == (
        "baseline",
        "attack_v1",
        "attack_v2",
        "non_clinical_baseline",
        "control_v1",
        "control_v2",
        "mitigation",
    )


def test_seeds_locked() -> None:
    assert SEEDS == [42, 43, 44, 45, 46, 47, 48, 49, 50, 51]


def test_num_predict_locked() -> None:
    assert NUM_PREDICT == 2048


def test_sha256_deterministic() -> None:
    a = sha256_hex("hello")
    b = sha256_hex("hello")
    assert a == b
    assert a.startswith("sha256:")
    assert len(a) == 7 + 64


def test_model_slug() -> None:
    assert model_slug("qwen3:8b") == "qwen3_8b"
    assert model_slug("llama3.1:8b") == "llama3.1_8b"


def test_cell_filename() -> None:
    assert (
        cell_filename("scenario_01_triage", "qwen3:8b", "baseline")
        == "scenario_01_triage_qwen3_8b_baseline.jsonl"
    )


def test_build_messages_single_turn() -> None:
    block = {"system": None, "user": "hello"}
    system, msgs, multi = build_messages(block)
    assert system is None
    assert msgs == [{"role": "user", "content": "hello"}]
    assert multi is False


def test_build_messages_multi_turn() -> None:
    block = {
        "system": "be helpful",
        "turns": [
            {"role": "user", "content": "t1"},
            {"role": "user", "content": "t2"},
        ],
    }
    system, msgs, multi = build_messages(block)
    assert system == "be helpful"
    assert len(msgs) == 2
    assert multi is True


def test_prepare_for_model_gemma2_prefix_fallback() -> None:
    sys_msg = "DATA NOT INSTRUCTIONS"
    msgs = [{"role": "user", "content": "summarize"}]
    s, m = prepare_for_model(GEMMA2_MODEL, "prefix_fallback", sys_msg, msgs)
    assert s is None
    assert "[SYSTEM PREFIX]" in m[0]["content"]
    assert "DATA NOT INSTRUCTIONS" in m[0]["content"]
    assert "summarize" in m[0]["content"]


def test_prepare_for_model_default_passthrough() -> None:
    sys_msg = "x"
    msgs = [{"role": "user", "content": "y"}]
    # Non-gemma2 model — no rewrite.
    s, m = prepare_for_model(QWEN3_MODEL, "system_message", sys_msg, msgs)
    assert s == sys_msg
    assert m == msgs


def test_is_transient_error_unknown() -> None:
    # ValueError is not transient (permanent) → should not retry.
    assert is_transient_error(ValueError("nope")) is False


def test_constants_strings() -> None:
    assert ERROR_PREFIX == "__HARNESS_ERROR__"
    assert "TURN_BREAK" in TURN_BREAK
