# Stage B (菜单目标闭环) Progress Report

**Date**: 2026-07-02  
**Status**: IN PROGRESS

## Completed Work

### 1. Adversarial Review of Stage A (COMPLETE)
Launched comprehensive Ultracode workflow with dual adversarial review:
- **Behavioral Review**: Found 0 bugs (all Stage A invariants hold)
- **Architecture Review**: Found 3 issues

### 2. Stage A Fixes Applied (2/3 COMPLETE)

#### ✅ Finding 1: Evidence Validation (FIXED)
**Issue**: Evidence refs not validated for existence before accepting  
**Fix**: Added existence check in `record_failure()` and `record_success()`
- `state_machine.py` lines 407-430: Check `self.evidence` dict before ownership validation
- `state_machine.py` lines 485-495: Validate all step evidence exists before success
- **Test Status**: All 59 Stage A tests pass

#### ✅ Finding 2: Safety Framework Documentation (FIXED)  
**Issue**: Safety constraints are declarative only, not enforced  
**Fix**: Added comprehensive docstring to `playbook.py` module header
- Clarified that enforcement is executor responsibility (Stage D/E)
- Documented the three safety components: safety_constraints, policy_blocked, BLOCKED_BY_SAFETY_POLICY
- Added guidance for menu/page/feature goal pre-screening

#### 🔄 Finding 3: menu_entries.json Loader (TODO)
**Issue**: No bridge from menu_entries.json fixture to Goal registry  
**Blocker for**: Stage C independence per 计划 §2.6  
**Required**: Create `loader.py` with `load_menu_goals_from_fixture()`

## Stage B Design (FROM WORKFLOW)

### Architecture
```
┌─────────────────────────────────────────────────────────────┐
│ MenuGoalOrchestrator                                        │
│  • init_menu_discovery_session()                            │
│  • run_until_complete()                                     │
│  • export_menu_fixtures()                                   │
└──────┬────────────────────────────────────────┬─────────────┘
       │                                        │
       v                                        v
┌──────────────────┐                  ┌────────────────────┐
│ DiscoveryAdapter │                  │ GoalLoopEngine     │
│  • scan_to_goals │                  │  (Stage A kernel)  │
│  • bind_evidence │                  │                    │
└──────┬───────────┘                  └─────────┬──────────┘
       │                                        │
       v                                        v
┌──────────────────┐                  ┌────────────────────┐
│ LiveDiscovery    │                  │ MenuClassifier     │
│  (existing API)  │                  │  • classify_menu_  │
│                  │                  │    failure()       │
└──────────────────┘                  └────────────────────┘
```

### Key Modules (TO IMPLEMENT)

1. **orchestrator.py** - MenuGoalOrchestrator  
   Session lifecycle, fixture export

2. **discovery_adapter.py** - DiscoveryAdapter  
   Maps PageEntryRecord → Goal, binds evidence IDs

3. **menu_classifier.py** - MenuClassifier  
   Extends fixed classifier with menu_not_found, menu_expand_failed, menu_click_failed

4. **menu_fixture_writer.py** - MenuFixtureWriter  
   Serializes menu_entries.json for Stage C independence

5. **loader.py** - load_menu_goals_from_fixture()  
   Reads menu_entries.json → registers Goals (Finding 3 fix)

### Deliverables (PER 计划 §4.3)
- [ ] menu_tree.json
- [ ] menu_entries.json (frozen fixture)
- [ ] menu_traversal_log.jsonl
- [ ] screenshots_index.json
- [ ] goal_summary.json (menu goal type)
- [ ] progress_events.jsonl (menu events)

### Verification Criteria (PER 计划 §4.4)
- [ ] L1 menus discovered stably
- [ ] Expand failures classified stably
- [ ] Menu shell normalization improves success rate
- [ ] Menu goal stops on success condition
- [ ] Conclusions sync to run-level summary

## Next Steps

1. **Implement menu_goal package** (Task #4)
   - Create prototype/stage2/app/menu_goal/__init__.py
   - Implement orchestrator, discovery_adapter, menu_classifier, menu_fixture_writer, loader

2. **Write comprehensive test suite** (Task #5)
   - test_menu_goal_orchestrator.py
   - test_menu_evidence_binding.py
   - test_menu_failure_classification.py
   - test_menu_playbook.py
   - test_menu_fixtures.py
   - test_menu_goal_integration.py
   - Include CJK test cases (re.ASCII validation)

3. **Verify deliverables** (Task #6)
   - Run all Stage B tests
   - Generate all required artifacts
   - Verify acceptance criteria
   - Ensure no Stage A regressions

## Workflow Details

**Run ID**: wf_687a7275-1b4  
**Total Agents**: 10  
**Total Tokens**: 518,963  
**Duration**: 57 minutes  
**Status**: Completed (analysis & design phases)

The workflow produced complete design documentation and identified 3 architecture findings. Implementation was done in isolated worktrees but needs to be migrated to main repository.

## References

- 技术方案: docs/技术方案第二阶段v4.md §4 (菜单入口发现模块)
- 实施计划: docs/第二阶段实施计划v4.md §4 (阶段B)
- Memory: C:\Users\Administrator\.claude\projects\C--project-aut-agent\memory\goal-loop-stage-a-status.md
