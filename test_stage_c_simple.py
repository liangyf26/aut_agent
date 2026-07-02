"""Simple Stage C verification"""
import tempfile, json
from pathlib import Path
from prototype.stage2.app.page_goal import PageGoalOrchestrator

with tempfile.TemporaryDirectory() as tmpdir:
    output_dir = Path(tmpdir) / "output"
    output_dir.mkdir()
    
    # Create orchestrator
    orch = PageGoalOrchestrator(output_dir=str(output_dir), run_id="test")
    print("OK: Orchestrator created")
    
    # Create root goal
    root_id = orch.create_root_goal(description="Test root")
    print(f"OK: Root goal {root_id}")
    
    # Create menu fixture
    menu_fixture = output_dir / "menu_entries.json"
    menus = [{"menu_id": "m1", "menu_path": ["System"], "menu_text": "System", 
              "route_hint": "/sys", "status": "discovered", "parent_id": None, "metadata": {}}]
    menu_fixture.write_text(json.dumps(menus, ensure_ascii=False), encoding='utf-8')
    print(f"OK: Menu fixture created")
    
    # Load menus
    goal_ids = orch.load_menu_entries(str(menu_fixture))
    print(f"OK: Loaded {len(goal_ids)} page goals")
    
    # Simulate discovery
    goal_id = goal_ids[0]
    goal = orch.engine.goals[goal_id]
    goal.status = "running"
    
    attempt_id = orch.adapter.record_page_attempt(goal_id=goal_id)
    print(f"OK: Attempt {attempt_id}")
    
    step1 = orch.adapter.record_navigation_step(
        attempt_id=attempt_id, action="navigate_to_page", target="/sys", observed=False)
    step2 = orch.adapter.record_navigation_step(
        attempt_id=attempt_id, action="capture_state", observed=True)
    print(f"OK: Steps {step1}, {step2}")
    
    # Attach evidence
    ss_path = output_dir / "ss.png"
    ss_path.write_text("fake")
    ev1 = orch.adapter.attach_screenshot_evidence(step_id=step2, screenshot_path=str(ss_path))
    ev2 = orch.adapter.attach_page_metadata_evidence(
        step_id=step2, page_title="System", page_url="http://ex.com/sys",
        http_status=200, dom_snapshot={"visible_text_len": 500, "dom_nodes": 100})
    print(f"OK: Evidence {ev1}, {ev2}")
    
    # Record success
    orch.adapter.record_page_success(
        attempt_id=attempt_id, page_url="http://ex.com/sys", page_title="System",
        visible_text_len=500, dom_nodes=100, blank_screenshot_ratio=0.1, evidence_refs=[ev1, ev2])
    print(f"OK: Success recorded, goal status={goal.status}")
    
    # Export
    fixture_path = orch.export_fixture()
    print(f"OK: Fixture exported to {fixture_path}")
    
    # Verify
    fixture_data = json.loads(Path(fixture_path).read_text(encoding='utf-8'))
    # Check if it's a dict with page_entries or a list
    if isinstance(fixture_data, dict):
        entries = fixture_data["page_entries"]
    else:
        entries = fixture_data
    
    assert len(entries) == 1
    entry = entries[0]
    assert entry["page_id"] == "m1"
    assert entry["status"] == "reachable"
    assert entry["http_status"] == 200
    print(f"OK: Fixture verified - {entry['page_id']} is {entry['status']}")
    
    # Summary
    summary = orch.get_summary()
    print(f"OK: Summary - total={summary['total_goals']}, succeeded={summary['succeeded']}, reachable={summary['reachable_count']}")
    
    print("\n=== ALL STAGE C TESTS PASSED ===")
