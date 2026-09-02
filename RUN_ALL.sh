#!/bin/bash
# Aiva — one-command automated test runner
set -e
cd "$(dirname "$0")"

echo "== STEP 0: update code =="
git pull origin arena/01a05304-voice-agent || echo "(pull skipped)"

echo ""
echo "== STEP 1: offline unit tests (no API key) =="

echo "--- Batch-2 updater replay (20 fixtures + determinism) ---"
uv run python phase4/harness/eval_runner.py --batch2 --determinism 3

echo ""
echo "--- Turn controller regression ---"
uv run python phase5/tests/test_turn_controller.py

echo ""
echo "--- STT validation gates (segment aggregation + suspicious band) ---"
uv run python phase5/tests/test_stt_validation.py

echo ""
echo "--- Semantic ack selection (Fish-voice acks) ---"
uv run python phase5/tests/test_ack_selection.py

echo ""
echo "--- TTS warmup policy (pre-warm) ---"
uv run python phase5/tests/test_tts_warmup.py

echo ""
echo "--- Contract wiring (last_claim/last_reply + priority cap) ---"
uv run python phase5/tests/test_contract_wiring.py

echo ""
echo "--- Adaptive endpointing regression ---"
uv run python phase5/tests/test_adaptive_endpointing.py

echo ""
echo "--- Control Plane V1 P1 shadow (16-row table + conflicts + pins) ---"
uv run python phase5/tests/test_control_plane_v1.py

echo ""
echo "--- Control Plane V1 invariants (S1 + I1-I9 + fail-closed) ---"
uv run python phase5/tests/test_control_plane_invariants.py

echo ""
echo "--- Correction-repair package (number edits never wipe; live 20260902_184247) ---"
uv run python phase5/tests/test_correction_repair.py

echo ""
echo "--- Episode-memory foundation (units: membership/keys/supersede; docs/EPISODE_MEMORY_SLICE_LOCK.md) ---"
uv run python phase5/tests/test_memory_units.py

echo ""
echo "--- Capture-confirm v2 detector + gate (topic-blind disclosure frames; detection only) ---"
uv run python phase5/tests/test_capture_confirm.py

echo ""
echo "--- Offline pipeline check ---"
uv run python phase5/offline_pipeline_check.py

echo ""
echo "== STEP 2: transport check (1 real Gemini call) =="
uv run python phase5/transport_check.py

echo ""
echo "== STEP 3: memory scoping acceptance (4 cases) =="
uv run python phase5/memory_scope_test.py

echo ""
echo "== STEP 4: D-C safety regression (55 items, ~8 min) =="
mkdir -p phase4/reports
uv run python phase4/harness/eval_runner.py --dc 2>&1 | tee phase4/reports/dc_full.txt

echo ""
echo "== STEP 5: golden scenario suite (19 fixtures, ~3 min) =="
uv run python phase4/harness/eval_runner.py --golden 2>&1 | tee phase4/reports/golden_full.txt

echo ""
echo "== ALL AUTOMATED TESTS DONE =="
echo "Next: manual live conversation test (see LIVE_TEST.md)"
