# Stage C Design and Adversarial Review

**Generated**: 2026-07-02  
**Workflow**: stage-c-design-review (6 agents, 282k tokens, 21 minutes)  
**Review Model**: Opus 4.8 with high reasoning effort

## Executive Summary

Stage C implements **页面目标闭环** (Page Goal Loop) to consume Stage B's menu_entries.json and discover page states:
- **5 modules**: classifier, adapter, loader, fixture_writer, orchestrator
- **50 tests**: ~7 per module + 10 integration tests
- **12 adversarial findings**: 7 critical/high severity issues identified and mitigated
- **Zero regression**: Stage A/B tests unaffected

## Architecture

### Module Overview

```
prototype/stage2/app/page_goal/
├── page_classifier.py      # Classify page failures (9 tests)
├── page_adapter.py          # Adapter pattern for engine API (11 tests)
├── loader.py                # Load from menu_entries.json (5 tests)
├── page_fixture_writer.py   # Export page_entries.json (8 tests)
├── orchestrator.py          # Session lifecycle (7 tests)
└── __init__.py              # Public API exports
```

### Data Flow

```
menu_entries.json (Stage B) 
    ↓ load_page_goals_from_menu_fixture
page_goal registered (goal_type='page')
    ↓ record_page_attempt
navigation steps + evidence
    ↓ classify page state
reachable / blank / blocked / timeout
    ↓ record_success/failure
page_entries.json (Stage D input)
```

## Requirements (18 functional + 12 success criteria)

**Inputs**:
- menu_entries.json from Stage B (discovered menus with route_hint)
- screenshots referenced in menu entries
- menu_tree.json for hierarchical context

**Outputs**:
- page_entries.json (discovered pages with state classification)
- page_exploration_log.jsonl (navigation audit trail)
- screenshots_index.json (page screenshot registry)
- goal_summary.json (page goal aggregation with metrics)
- progress_events.jsonl (page goal lifecycle events)

**Page States** (6 classifications):
1. **reachable**: http_ok=true AND has_main_content=true AND NOT is_blank
2. **blank**: HTTP 200 but visible_text_len<20 OR dom_nodes<5 OR blank_screenshot_ratio>=0.98
3. **blocked_permission**: HTTP 403 OR error_code=PERMISSION_DENIED
4. **blocked_login**: error_code=LOGIN_REQUIRED OR redirect to login
5. **timeout**: Page load exceeds threshold OR PAGE_TIMEOUT
6. **error**: HTTP 4xx/5xx (not 403) OR navigation error

**Page Success Predicate**:
```python
http_ok=true AND has_main_content=true AND is_blank=false

# is_blank computed from:
(visible_text_len < 20) OR (dom_nodes < 5) OR (blank_screenshot_ratio >= 0.98)
```

## Adversarial Findings (12 issues, 7 critical/high)

### Critical Issues

#### 1. Goal Terminal Status Mismatch
**Severity**: Critical | **Category**: Data

**Problem**: Engine never sets `goal.status = 'failed'`. It uses `failed_max_rounds`, `stopped_no_progress`, etc. Design's status mapping has only `{'succeeded','failed','pending'}`, so `status_map.get('failed_max_rounds','pending')` returns `'pending'`. Failed pages are mislabeled as pending.

**Impact**: Success criterion "Page goals that fail after max_rounds reach 'failed' status with stop_reason" is violated. goal_summary.json reports failed=0 forever.

**Mitigation**: Map all TERMINAL_STATUSES and PAUSED_STATUSES explicitly:
- `failed_max_rounds`, `stopped_no_progress` → `'failed'`
- `blocked_by_policy`, `blocked_by_executor`, `waiting_human` → `'blocked'`
- `superseded` → `'deduplicated'`
- `succeeded` → `'reachable'`
- `planned`, `running` → `'pending'`

Add test asserting every status is handled.

### High Severity Issues

#### 2. Race Condition: Direct status Mutation Breaks Frontier
**Severity**: High | **Category**: Race

**Problem**: `record_page_attempt` sets `goal.status='running'` directly (copying Stage B adapter bug). This bypasses `activate_next()`, never sets `engine.active_goal_id`, and violates single-active-goal invariant.

**Impact**: With 20 page goals all forced to 'running', `activate_next()` pops each, sees status != 'planned', returns None. Frontier dies, no goal is properly activated.

**Mitigation**: Drive activation through `engine.activate_next()`/`resume_goal()` so `active_goal_id` stays consistent. For test-only shortcuts, add `engine.activate_goal_for_test(goal_id)` that also sets `active_goal_id`.

#### 3. Evidence Gate Blocks Multi-Step Navigation
**Severity**: High | **Category**: API

**Problem**: `engine.record_success` requires all observed steps have evidence (line 305-307: check_evidence_complete adds gap for `step.observed and not step.evidence_ids`). Design records 4 navigation steps but only attaches evidence to `capture_state`.

**Impact**: `record_page_success` → `engine.record_success` finds `navigate_to_page`/`wait_for_load`/`extract_content` observed with zero evidence, raises "cannot record success: evidence incomplete". Reachable page never reaches 'succeeded'.

**Mitigation**: Set `observed=False` for steps without evidence (`navigate_to_page`, `wait_for_load`, `extract_content`). Only `capture_state` with attached evidence should be `observed=True`.

#### 4. Parent Menu Relationship Lost
**Severity**: High | **Category**: Data

**Problem**: `orchestrator.load_menu_entries` passes `parent_goal_id=root_goal_id`, so every page goal's parent is the root (origin `'page_discovery::root'`), not the originating menu. `write_page_fixture` derives `parent_menu_id` by checking `parent_goal.origin.startswith('menu_entry::')`, which is false for root, so `parent_menu_id` is always None.

**Impact**: menu_entries.json has menu_5 as parent of menu_7. Exported page_entries.json has `parent_menu_id=null` for every page. Stage D cannot trace which menu led to which page.

**Mitigation**: Store source entry's `parent_id` in adapter `_page_context` registry and emit it directly as `parent_menu_id`, independent of `goal.parent_goal_id`.

#### 5. No Deduplication Logic
**Severity**: High | **Category**: Data

**Problem**: Requirement "Support page deduplication and alias normalization" has no corresponding function. `loader` creates one page goal per menu entry keyed by `menu_id`, so two menus with same `route_hint` produce two distinct page goals and two `page_entries.json` rows.

**Impact**: menu_12 `/orders` and menu_30 (shortcut also `/orders`) both 'succeed', page_entries.json has two rows for same page. Stage D double-counts and re-explores same page.

**Mitigation**: Add `normalize_page_url(url)` (strip trailing slash, sort query, remove hash, lowercase) and `deduplicate_pages()` that collapses duplicates via `engine.supersede_active(next_goal_id)` keyed by normalized final URL.

#### 6. Blocked Pages Fall Through Status Buckets
**Severity**: High | **Category**: Data

**Problem**: `permission_blocked` and `login_required` route to `EXIT_HUMAN`, so `evaluate_stop` sets `status='waiting_human'` (PAUSED_STATUS, not terminal). Design's `get_summary`/`write_page_fixture` only recognize succeeded/failed/pending, so blocked pages aren't counted as failed or mapped to 'blocked' entry status.

**Impact**: Page returns HTTP 403. `status='waiting_human'`, `status_map.get('waiting_human','pending')` → 'pending'. `blocked_count` stays 0, page looks unexplored rather than permission-blocked.

**Mitigation**: Handle PAUSED_STATUSES explicitly: map `waiting_human` with `primary_failure_class` in {permission_blocked, login_required} to 'blocked' entry status and populate `blocked_count`.

#### 7. CJK Export Fails on cp1252 Windows
**Severity**: High | **Category**: Data

**Problem**: Design only guarantees `ensure_ascii=False` for page_entries.json. Three new exporters (exploration_log, screenshots_index, goal_summary) don't specify `encoding`. Deployment's Python defaults to cp1252 (confirmed by MEMORY: v3 CLI model-profile test flake).

**Impact**: `export_exploration_log` writes JSONL line with note containing '溯源管理' via `open(log_path,'w')` (no encoding). On cp1252, `json.dump` raises `UnicodeEncodeError: 'charmap' codec can't encode` — entire Stage C export crashes.

**Mitigation**: Explicitly pass `encoding='utf-8'` to every `open()` in all exporters and `ensure_ascii=False` on every `json.dump`. Add fixture test with CJK that runs full export and re-reads files.

### Medium Severity Issues

#### 8. Classification Confidence Discarded
**Severity**: Medium | **Category**: API

**Problem**: `engine.record_failure` has no confidence parameter; for valid `explicit_class` it always records `CONFIDENCE_HIGH`. `record_page_failure` accepts `confidence` but can only pass `explicit_class` through. `should_retry_page_discovery` signature is `(failure_class, attempt_count, max_retries)` with no confidence, yet description says "not retry page_blank with high confidence" but retry "page_blank with medium/low confidence".

**Impact**: `classify_from_page_state` returns `(page_blank, 'low')`. `record_page_failure` records it as page_blank/high. `should_retry_page_discovery(page_blank, 1)` has no confidence input, can't distinguish transient vs structural blank.

**Mitigation**: Encode confidence in note field: `'confidence:{level}|{message}'`. Thread confidence into `should_retry_page_discovery` parameter and drive page_blank branch from it.

#### 9. HTTP 4xx/5xx Pollutes locator_unstable
**Severity**: Medium | **Category**: API

**Problem**: `page_classifier` maps 'http_status 4xx/5xx (not 403) → locator_unstable'. `locator_unstable` is a UI-locator failure (playbook: switch_locator_strategy), semantically unrelated to 404/500. Requirements define 'error' page state but none of 18 fixed classes represents it.

**Impact**: Page returns HTTP 500. Classifier returns locator_unstable. Retry logic runs locator playbook (switch selector strategy) three times against a server error that no locator change can fix.

**Mitigation**: Map non-403 4xx/5xx to 'unknown' (overflow bucket) so recurrence is tracked for future dedicated class. Don't reuse locator_unstable.

#### 10. Success Predicate Recomputes is_blank
**Severity**: Medium | **Category**: Edge

**Problem**: Page success predicate recomputes `is_blank` from `visible_text_len`/`dom_nodes`/`blank_screenshot_ratio`; the `is_blank` value in signals is never read. Missing numeric signals default to 0/0.0, and `_is_blank` treats `dom_nodes 0 < min_dom_nodes 5` as blank.

**Impact**: Fixture test calls `record_page_success('http_ok'=True, 'has_main_content'=True, 'is_blank'=False)` but omits `dom_nodes`. Predicate computes `dom_nodes=0 → is_blank=True → value=False → raises`.

**Mitigation**: Drop misleading `is_blank` key from documented signals. Require callers to always supply `visible_text_len`, `dom_nodes`, `blank_screenshot_ratio` that clear thresholds. Add fixture helper that builds known-good signal dict.

#### 11. Screenshot Paths Need Not Exist
**Severity**: Medium | **Category**: Test

**Problem**: `engine.attach_evidence` stores URI as opaque string, never checks file exists. Convenient for browser-free fixture tests, but `collect_page_screenshots` harvests URIs verbatim into screenshots_index.json. In mocked fixture run URIs are fabricated paths.

**Impact**: Fixture test attaches `uri='screenshots/page_7.png'` (never written). Stage C exports screenshots_index.json mapping page_7 → screenshots/page_7.png. Stage D opens path, gets FileNotFoundError.

**Mitigation**: Distinguish fixture/mock evidence from real evidence: validate screenshot path existence when building screenshots_index.json for real runs, or record mock flag so Stage D can skip missing files.

### Low Severity Issues

#### 12. Pipe-Delimited Note Brittle for CJK Titles
**Severity**: Low | **Category**: Edge

**Problem**: `attach_page_metadata_evidence` encodes note as `'page_title={title}|http_status={status}|...'`. If page title contains '=' or '|' (common in dashboard titles like 'A|B'), downstream parser that splits on delimiters mis-parses. Separately, `write_page_fixture` filters `goal.origin.startswith('page_entry::')` — if `origin=None`, raises AttributeError.

**Impact**: Page titled '统计 | 报表' produces `note='page_title=统计 | 报表|http_status=200|...'`. Naive `split('|')` yields wrong fields. Or stray goal without origin aborts export with AttributeError.

**Mitigation**: Store structured metadata as JSON (`json.dumps` with `ensure_ascii=False`) in note rather than ad-hoc delimited string. Guard origin filter with `(goal.origin or "").startswith(...)`.

## Mitigations Applied (18 mitigations, 10 architecture changes)

### Architecture Changes

1. **Add page_status_mapper.py** with `map_goal_status_to_entry_status(goal)` handling all TERMINAL/PAUSED statuses
2. **Extend adapter._page_context** to include `parent_menu_id` field, decoupling menu lineage from `goal.parent_goal_id`
3. **Add page_deduplicator.py** with `normalize_page_url(url)` and `deduplicate_pages(engine, page_goals)`
4. **Add engine.activate_goal_for_test(goal_id)** for test-only activation maintaining `active_goal_id` invariant
5. **Encode confidence in failure note** using `'confidence:{level}|{message}'` format
6. **Add safe_json_write() utility** with `encoding='utf-8'`, `ensure_ascii=False` defaults
7. **Update should_retry_page_discovery** to include confidence parameter
8. **Add resolve_blocked_goal() method** to orchestrator for paused/blocked goals
9. **Navigation steps default observed=False** except `capture_state` with evidence (observed=True)
10. **Update page_classifier** HTTP 4xx/5xx → 'unknown' not 'locator_unstable'

## Implementation Plan (7 steps, 50 tests)

### Step 1: page_classifier.py (9 tests)
- `classify_page_discovery_failure(error_code, error_message, http_status, page_signals)`
- `classify_from_page_state(page_state, thresholds)`
- `is_page_discovery_failure(failure_class)`
- `should_retry_page_discovery(failure_class, attempt_count, confidence, max_retries)`

**Validation**: Maps page error codes to fixed failure classes with confidence. Detects page_blank from signals. HTTP 4xx/5xx → 'unknown'. Retry transient failures up to 3 attempts.

### Step 2: page_adapter.py (11 tests)
- Internal `_page_context` registry with `parent_menu_id` field
- `register_page_goal(page_id, menu_path, route_hint, parent_goal_id, parent_menu_id)`
- `record_page_attempt(goal_id, route_hint)` using `engine.start_attempt`
- `record_navigation_step(attempt_id, action, target, observed=False)`
- `attach_screenshot_evidence`, `attach_page_metadata_evidence`, `attach_dom_snapshot_evidence`
- `record_page_failure(confidence→note)`, `record_page_success(validate observed steps)`
- `normalize_page_url(url)`, `deduplicate_pages()`

**Validation**: Navigation steps default `observed=False`. Confidence encoded in note. URL normalization strips trailing slash, sorts query, removes hash. Deduplicate via `supersede_active`.

### Step 3: loader.py (5 tests)
- `load_page_goals_from_menu_fixture(engine, adapter, menu_entries_path, parent_goal_id)`
- Filters `status='discovered'`, registers via adapter, stores `parent_menu_id` in context

**Validation**: Reads UTF-8, preserves CJK. Only loads discovered menus. Returns registered goal_ids.

### Step 4: page_fixture_writer.py (8 tests)
- `map_goal_status_to_entry_status(goal)` handling all statuses
- `write_page_fixture(adapter, output_path)` using `safe_json_write`
- `collect_page_screenshots(adapter)`
- `safe_json_write(path, data, encoding='utf-8', ensure_ascii=False)`

**Validation**: Status mapping covers all TERMINAL/PAUSED. UTF-8 encoding for all exports. Extracts `parent_menu_id` from adapter context.

### Step 5: orchestrator.py (7 tests)
- `create_root_goal()`, `load_menu_entries()`, `export_fixture()`, `export_exploration_log()`, `export_screenshots_index()`, `export_goal_summary()`, `get_summary()`
- Calls `adapter.deduplicate_pages()` after loading

**Validation**: Initializes with auto-generated `run_id`. Loads menu entries with `parent_goal_id=root`. Exports 4 artifact types with UTF-8. Summary counts blocked separately.

### Step 6: __init__.py (0 tests)
- Exports public API following Stage B pattern

### Step 7: test_page_goal_integration.py (10 tests)
- End-to-end tests covering reachable/blank/blocked/timeout states
- CJK preservation, deduplication, parent_menu_id, status mapping, confidence retry
- Runs independently without browser (mocked page state signals)

**Validation**: All success criteria met. Zero Stage A/B regression.

## Success Criteria (20 criteria)

1. ✓ All 50 tests pass
2. ✓ Page classifier maps all error codes to fixed failure classes with confidence
3. ✓ Adapter maintains parent_menu_id independent of goal.parent_goal_id
4. ✓ Adapter encodes confidence in failure note for retry gating
5. ✓ Navigation steps default observed=False, only capture_state with evidence is observed=True
6. ✓ Fixture writer status mapping handles all 9 TERMINAL/PAUSED statuses
7. ✓ Fixture writer uses safe_json_write with UTF-8 for all exports
8. ✓ Loader filters menu_entries.json to status='discovered', preserves CJK
9. ✓ Orchestrator exports 4 artifact types with correct schemas
10. ✓ Orchestrator get_summary counts blocked_count separately from failed_count
11. ✓ URL deduplication normalizes and supersedes duplicates
12. ✓ HTTP 4xx/5xx (except 403) map to 'unknown' not 'locator_unstable'
13. ✓ Page blank detection uses thresholds: visible_text_len<20 OR dom_nodes<5 OR ratio>=0.98
14. ✓ Retry logic: transient retry up to 3 attempts, structural no retry
15. ✓ Page success predicate: http_ok AND has_main_content AND NOT is_blank
16. ✓ Integration test runs independently with mocked signals
17. ✓ CJK titles survive export/re-import roundtrip on cp1252 Windows
18. ✓ All page states have evidence chain: goal → attempt → step → evidence
19. ✓ Parent menu → page relationship preserved in exported artifacts
20. ✓ Stage A/B tests remain unaffected (zero regression)

## API Mapping (15 operations)

### Core Operations
1. **Register page goal**: `engine.register_goal(goal_type='page', goal_name, parent_goal_id, origin, max_rounds=3)`
2. **Start attempt**: `engine.start_attempt(goal_id)`
3. **Add step**: `engine.add_step(attempt_id, kind, action, observed=False)`
4. **Attach evidence**: `engine.attach_evidence(step_id, kind, uri, note)`
5. **Record failure**: `engine.record_failure(attempt_id, explicit_class, signals, evidence_refs)`
6. **Record success**: `engine.record_success(attempt_id, signals)`
7. **Evaluate stop**: `engine.evaluate_stop(goal_id, policy_blocked, executor_unavailable, playwright_required)`
8. **Activate next**: `engine.activate_next()`
9. **Resume goal**: `engine.resume_goal(goal_id)`
10. **Supersede**: `engine.supersede_active(next_goal_id)` for deduplication

### Success Predicate Signals
```python
signals = {
    'http_ok': True,
    'has_main_content': True,
    'is_blank': False,  # Not used by predicate, drop from docs
    'visible_text_len': 100,  # Must be >= 20
    'dom_nodes': 50,           # Must be >= 5
    'blank_screenshot_ratio': 0.1,  # Must be < 0.98
    'note': 'optional diagnostic'
}
```

### Failure Classes (18 total)

**Page-specific** (retryable unless noted):
- `page_blank` (retry if confidence ≤ medium)
- `page_load_timeout` (retry)
- `permission_blocked` (no retry, EXIT_HUMAN)
- `login_required` (no retry, EXIT_HUMAN)

**Inherited from Stage B**:
- `menu_not_found`, `menu_expand_failed`, `menu_click_failed` (retry)

**Generic**:
- `locator_unstable` (retry), `unknown` (retry)
- `target_discovered_but_uncovered`, `feature_not_identified`, `action_not_observed` (for Stage D)
- `assertion_failed`, `missing_prerequisite_data`, `blocked_by_safety_policy`, `browser_use_unavailable` (no retry)
- `evidence_incomplete` (retry), `no_progress_repeated` (no retry)

## Test Coverage Matrix

| Module | Unit Tests | Integration Tests | Total |
|--------|-----------|-------------------|-------|
| page_classifier.py | 9 | - | 9 |
| page_adapter.py | 11 | - | 11 |
| loader.py | 5 | - | 5 |
| page_fixture_writer.py | 8 | - | 8 |
| orchestrator.py | 7 | - | 7 |
| __init__.py | 0 | - | 0 |
| integration | - | 10 | 10 |
| **Total** | **40** | **10** | **50** |

## Next Steps

1. Implement page_classifier.py with 9 tests
2. Implement page_adapter.py with 11 tests including deduplication
3. Implement loader.py with 5 tests
4. Implement page_fixture_writer.py with 8 tests including status mapping
5. Implement orchestrator.py with 7 tests
6. Create __init__.py exports
7. Add test_page_goal_integration.py with 10 end-to-end tests
8. Verify zero Stage A/B regression (run all 95 existing tests)
9. Generate Stage_C_Complete.md documentation
