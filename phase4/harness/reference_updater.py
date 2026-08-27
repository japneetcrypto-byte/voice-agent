"""Thin re-export — implementation lives in agent/state_updater.py (production,
phase-identical port of the Phase 4 reference). Batch-2 replay therefore tests
the production module directly (G-DET + 18 fixtures)."""
from agent.state_updater import (  # noqa: F401
    PARAMS, TAXONOMY, RISK_LEVELS, NEGATIVE_LABELS, NORM_TABLE,
    normalize_label, update, merge_state, default_state,
)
