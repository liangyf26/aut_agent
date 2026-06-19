from __future__ import annotations

import tempfile
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.runtime.artifacts import ArtifactWriter
from tools.suyuan_submit_loop import should_auto_continue_next_round


def test_should_auto_continue_only_for_scheduled_true() -> None:
    assert should_auto_continue_next_round(
        {
            "status": "scheduled",
            "should_start_next_round": True,
        }
    )
    assert not should_auto_continue_next_round(
        {
            "status": "scheduled",
            "should_start_next_round": False,
        }
    )
    assert not should_auto_continue_next_round(
        {
            "status": "needs_review",
            "should_start_next_round": True,
        }
    )
    assert not should_auto_continue_next_round(
        {
            "status": "stopped",
            "should_start_next_round": False,
        }
    )


def test_artifact_writer_avoids_same_second_name_collision() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        first = ArtifactWriter(root, "sample_run")
        second = ArtifactWriter(root, "sample_run")

        assert first.run_dir != second.run_dir
        assert first.run_dir.exists()
        assert second.run_dir.exists()
        assert second.run_dir.name.startswith(first.run_dir.name) or second.run_dir.name.endswith("_01")
