"""Fixed classifier + fixed playbook table coverage and consistency."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.goal_loop import classification as fc  # noqa: E402
from prototype.stage2.app.goal_loop import playbook as pb  # noqa: E402


def test_playbook_table_covers_every_fixed_class() -> None:
    # every one of the 18 fixed classes maps to a playbook with a valid exit
    pb.assert_table_complete()
    assert set(pb.PLAYBOOK_TABLE) == set(fc.FIXED_FAILURE_CLASSES)
    for trigger, spec in pb.PLAYBOOK_TABLE.items():
        assert spec.trigger_class == trigger
        assert spec.action_steps, f"{trigger} has empty action steps"
        assert spec.exit in pb.VALID_EXITS


def test_human_required_classes_match_human_exits() -> None:
    # predicates and playbook must agree on which classes route to a human task
    expected = {t for t, s in pb.PLAYBOOK_TABLE.items() if s.exit == pb.EXIT_HUMAN}
    assert pb.HUMAN_REQUIRED_CLASSES == frozenset(expected)


def test_explicit_class_wins_with_high_confidence() -> None:
    cls, conf = fc.classify_failure(explicit_class="page_blank")
    assert cls == "page_blank"
    assert conf == fc.CONFIDENCE_HIGH


def test_unrecognized_explicit_class_is_overflow() -> None:
    cls, conf = fc.classify_failure(explicit_class="totally_made_up")
    assert cls == fc.UNKNOWN
    assert conf == fc.CONFIDENCE_LOW


def test_keyword_fallback_medium_confidence() -> None:
    cls, conf = fc.classify_failure(signals="页面 白屏 blank white screen")
    assert cls == "page_blank"
    assert conf == fc.CONFIDENCE_MEDIUM


def test_no_signal_falls_back_to_unknown() -> None:
    cls, conf = fc.classify_failure(signals=None)
    assert cls == fc.UNKNOWN
    assert conf == fc.CONFIDENCE_LOW


def test_iteration_category_projection_uses_existing_vocabulary() -> None:
    # the projected categories must live in the existing aggregator vocabulary
    allowed = {
        "ui",
        "network",
        "verification",
        "data",
        "environment",
        "stability",
        "permission",
        "policy",
        "runtime",
        "preflight",
        "backend_data",
        "front_validation",
        "workflow_branch",
    }
    for failure_class in fc.FIXED_FAILURE_CLASSES:
        assert fc.to_iteration_category(failure_class) in allowed


def test_unknown_playbook_exit_is_escalate() -> None:
    assert pb.select_playbook(fc.UNKNOWN).exit == pb.EXIT_ESCALATE
