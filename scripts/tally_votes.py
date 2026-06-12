#!/usr/bin/env python3
"""
Tally votes on all open proposal Issues and apply effects.
Called by tally-votes.yml every 6 hours.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure scripts/ is in sys.path so the engine package resolves correctly
# whether run directly (python scripts/tally_votes.py) or imported as
# scripts.tally_votes from the project root during tests.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import (
    # constants
    VOTING_PERIOD_DAYS, AI_VOTING_HOURS, SIGNATURE_THRESHOLD, COOLDOWN_DAYS,
    ANNALS_INTERVAL, REPRESENTATIVE_DAYS, CATEGORIES, CATEGORY_COLORS,
    POLICY_METRICS, POLICY_COST, BASE_STATE_FIELDS, WORLD_GENERATION_RULES,
    THRESHOLD_TAGS, RARITY_WEIGHTS,
    # gh
    run, gh_json, get_reactions, push_with_retry, REPO, GITHUB_TOKEN, SKIP_TIMING,
    # state
    read_json, write_json, read_state, write_state,
    read_stats, write_stats,
    load_event_pool, load_active_event, save_active_event,
    append_history_snapshot,
    # world
    slugify, pollution_level, env_bg_color,
    next_entity_id, entity_exists_by_name, auto_create_entity, auto_remove_entity,
    world_autonomous_tick, run_world_engine, apply_effect, apply_event_effects,
    determine_era, check_threshold_tags, apply_tags,
    # events
    fire_random_event, open_event_issue, close_event_issue,
    check_event_expiry, fire_chained_event, apply_crisis_multiplier,
    # svg
    svg_radar, generate_dashboard_svg,
    # content
    client, generate_narrative, update_world_summary, generate_world_md,
    generate_annals, generate_citizen_narrator, upsert_bot_comment, update_readme,
    _LLM_EXCLUDE, _state_for_llm,
    _PINNED_IDS_PATH, _load_pinned_ids, _get_or_create_citizen_voices_issue,
    # chronicle
    get_or_create_dispatch_issue, save_dispatch, publish_dispatch,
    append_history, update_laws_index, collect_star_income,
    _load_entity_names, _build_gap_dashboard, _build_chronicle_body,
    # citizens
    format_signatories, track_citizen_activity, track_citizen_proposal,
    check_proposal_cooldown, update_proposal_cooldown, select_weekly_representatives,
    # proposals
    parse_effect, next_law_number,
    get_open_proposals, get_ai_proposals, get_feedbacks,
    process_issue, process_ai_proposal, process_feedback,
    _ensure_labels,
)


def _validate_state():
    path = Path("world/state.json")
    if not path.exists():
        raise SystemExit("[FATAL] world/state.json missing — cannot run tally")
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"[FATAL] world/state.json is corrupted: {e}")


def main():
    _validate_state()
    _ensure_labels()
    collect_star_income()
    tick_changed = world_autonomous_tick()

    proposals = get_open_proposals()
    print(f"Open proposals: {len(proposals)}")
    laws_this_tick = 0
    for proposal in proposals:
        try:
            laws_before = read_state().get("laws_count", 0)
            process_issue(proposal)
            if read_state().get("laws_count", 0) > laws_before:
                laws_this_tick += 1
        except Exception as e:
            print(f"  [ERROR] proposal #{proposal.get('number')}: {e}")

    for ai_proposal in get_ai_proposals():
        try:
            laws_before = read_state().get("laws_count", 0)
            process_ai_proposal(ai_proposal)
            if read_state().get("laws_count", 0) > laws_before:
                laws_this_tick += 1
        except Exception as e:
            print(f"  [ERROR] ai-proposal #{ai_proposal.get('number')}: {e}")

    feedbacks_applied = 0
    for feedback in get_feedbacks():
        if process_feedback(feedback):
            feedbacks_applied += 1

    active_before = load_active_event()
    resolved_event_title = active_before.get("title", "") if active_before else ""
    event_resolved = check_event_expiry(laws_this_tick)

    active_event_title = ""
    if not load_active_event():
        state = read_state()
        new_event = fire_random_event(state)
        if new_event:
            active_event_title = new_event["title"]
            print(f"  Firing event: {new_event['title']} ({new_event['rarity']})")
            apply_event_effects(new_event, "immediate_effects")
            issue_num = open_event_issue(new_event)
            new_event["fired_at"] = datetime.now(timezone.utc).isoformat()
            new_event["issue_number"] = issue_num
            save_active_event(new_event)
            print(f"  Event issue #{issue_num} opened")
    else:
        active_event_title = load_active_event().get("title", "")

    from auto_propose import should_generate, generate_ai_proposal, generate_feedbacks as gen_feedbacks
    should_prop, should_fb = should_generate(REPO)
    try:
        if should_prop:
            generate_ai_proposal(client, read_state(), REPO)
        if should_fb:
            gen_feedbacks(client, read_state(), REPO)
    except Exception as e:
        print(f"  [ERROR] auto_propose: {e}")

    save_dispatch(
        read_state(), tick_changed, laws_this_tick,
        active_event_title, feedbacks_applied,
    )

    append_history_snapshot(read_state())

    try:
        hist_data = json.loads(Path("world/history.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        hist_data = []
    generate_annals(hist_data)

    select_weekly_representatives()
    generate_citizen_narrator()

    _now = datetime.now(timezone.utc)
    _next_hour = ((_now.hour // 4) + 1) * 4
    if _next_hour >= 24:
        _next_tick = _now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        _next_tick = _now.replace(hour=_next_hour, minute=0, second=0, microsecond=0)
    _state = read_state()
    _state["next_tick_at"] = _next_tick.strftime("%Y-%m-%dT%H:%M:%SZ")
    write_state(_state)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dirty = run(["git", "status", "--porcelain", "world/"])
    if dirty:
        stats = read_stats()
        generate_dashboard_svg(stats, today)
        state = read_state()
        generate_world_md(state, None, today)
        update_readme(state, stats, None, today)
        run(["git", "add", "-A"])
        if event_resolved and resolved_event_title:
            commit_msg = f"[EVENT] resolved: {resolved_event_title[:50]}"
        elif event_resolved:
            commit_msg = "[EVENT] event resolved"
        elif tick_changed:
            commit_msg = "[WORLD] autonomous tick"
        else:
            commit_msg = "[WORLD] state update"
        run(["git", "commit", "-m", commit_msg])

    if push_with_retry():
        publish_dispatch()


if __name__ == "__main__":
    main()
