# Re-export public API for backward compatibility.
# Tests and external callers may import from this package directly.
from .constants import *
from .gh import run, gh_json, get_reactions, REPO, GITHUB_TOKEN, SKIP_TIMING
from .state import (
    read_json, write_json, read_state, write_state,
    read_stats, write_stats,
    load_event_pool, load_active_event, save_active_event,
    append_history_snapshot,
)
from .world import (
    slugify, pollution_level, env_bg_color,
    next_entity_id, entity_exists_by_name, auto_create_entity, auto_remove_entity,
    world_autonomous_tick, run_world_engine, apply_effect, apply_event_effects,
    determine_era, check_threshold_tags, apply_tags,
)
from .events import (
    fire_random_event, open_event_issue, close_event_issue,
    check_event_expiry, fire_chained_event, apply_crisis_multiplier,
)
from .svg import svg_radar, generate_dashboard_svg
from .content import (
    client, _LLM_EXCLUDE, _state_for_llm,
    generate_narrative, update_world_summary, generate_world_md,
    generate_annals, generate_citizen_narrator, _get_or_create_citizen_voices_issue,
    _PINNED_IDS_PATH, _load_pinned_ids, upsert_bot_comment,
)
from .chronicle import (
    get_or_create_dispatch_issue, _load_entity_names,
    _build_gap_dashboard, _build_chronicle_body, post_world_dispatch,
    append_history, update_laws_index,
    collect_star_income,
)
from .content import update_readme
from .citizens import (
    format_signatories, track_citizen_activity, track_citizen_proposal,
    check_proposal_cooldown, update_proposal_cooldown, select_weekly_representatives,
)
from .proposals import (
    parse_effect, next_law_number,
    get_open_proposals, get_ai_proposals, get_feedbacks,
    process_issue, process_ai_proposal, process_feedback,
    _ensure_labels,
)
