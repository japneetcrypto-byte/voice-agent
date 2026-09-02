#!/bin/bash
# =============================================================================
#  Aiva — one command: LOCK the build, START everything, VERIFY the workers
#  (updated 2026-08-31 — kills the "which build is actually running?" problem)
#
#  The old start_aiva.sh did a plain `git pull` — that is exactly how stale /
#  mixed worker states crept in (owner smokes 4-9: every session ran a
#  different build and the diagnostics never said which). This version:
#
#    1. LOCK    — deterministic checkout: fetch + dirty-guard + checkout +
#                 reset --hard origin/<branch> (never `git pull`)
#    2. SANITY  — run the precision-rail offline tests before going live
#    3. START   — token server + N workers + frontend; every process's output
#                 goes to logs/<proc>.out so it is always inspectable
#    4. VERIFY  — grep the [BUILD] git=<sha> stamp out of EVERY worker's own
#                 log file and compare it to the locked commit. Loud PASS/FAIL.
#                 No smoke may start until VERIFY passes.
#
#  Usage:
#    bash start_aiva.sh                    # default: 1 worker
#    WORKER_COUNT=2 bash start_aiva.sh     # your normal "both workers" setup
#    bash start_aiva.sh --check            # verify current workers, NO restart
#    FORCE=1 bash start_aiva.sh            # discard uncommitted tracked changes
#    AIVA_EXPECTED=<sha> bash start_aiva.sh  # require a specific commit (safety)
#    AIVA_RUN_TESTS=0 bash start_aiva.sh   # skip the offline sanity tests
# =============================================================================
set -u
cd "$(dirname "$0")"

BRANCH="${AIVA_BRANCH:-arena/01a05304-voice-agent}"
WORKER_COUNT="${WORKER_COUNT:-1}"
EXPECTED="${AIVA_EXPECTED:-}"
RUN_TESTS="${AIVA_RUN_TESTS:-1}"

# ---------------------------------------------------------------------------
# --check: report the build stamp of the workers that are ALREADY running
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--check" ]; then
    echo "== AIVA BUILD CHECK =="
    echo "checked-out: $(git log --oneline -1 2>/dev/null || echo 'unknown')"
    found=0
    for out in logs/worker_*.out; do
        [ -f "$out" ] || continue
        found=1
        line=$(grep -m1 '\[BUILD\] git=' "$out" 2>/dev/null || true)
        if [ -n "$line" ]; then
            echo "  $out : $line"
        else
            echo "  $out : NO BUILD STAMP YET (still starting, or stale pre-v9 code)"
            echo "          (check 'grep WORKER_BUILD logs/events_*.log | tail -1')"
        fi
    done
    [ "$found" = "1" ] || echo "  no logs/worker_*.out found — nothing started via this script"
    echo "== done =="
    exit 0
fi

echo "=============================================================="
echo " AIVA DEPLOY LOCK — branch=$BRANCH workers=$WORKER_COUNT"
echo "=============================================================="

# --- 1. LOCK: deterministic checkout (NOT `git pull`) -----------------------
echo ""
echo "[1/4] Locking code to $BRANCH ..."
git fetch origin "$BRANCH" || { echo "FAIL: git fetch — no network?"; exit 1; }

DIRTY=$(git status --porcelain --untracked-files=no)
if [ -n "$DIRTY" ] && [ "${FORCE:-0}" != "1" ]; then
    echo "FAIL: working tree has uncommitted tracked changes; reset --hard would destroy them:"
    echo "$DIRTY"
    echo "Commit or stash them, or rerun with FORCE=1 to discard."
    exit 1
fi

git checkout "$BRANCH" 2>/dev/null || { echo "FAIL: cannot checkout $BRANCH"; exit 1; }
git reset --hard "origin/$BRANCH" || { echo "FAIL: git reset --hard"; exit 1; }

# The worker build stamp is 12 hex chars (agent/main.py prints _head[:12]).
# Compare on the SAME width, and accept a 7- or 12-char AIVA_EXPECTED.
FULL_SHA=$(git rev-parse HEAD)
HEAD_SHA=$(git rev-parse --short=12 HEAD)
if [ -n "$EXPECTED" ] && [ "${FULL_SHA:0:${#EXPECTED}}" != "$EXPECTED" ]; then
    echo "FAIL: HEAD=$HEAD_SHA but AIVA_EXPECTED=$EXPECTED"
    exit 1
fi
echo "LOCKED: $(git log --oneline -1)"
echo "        (workers must print [BUILD] git=$HEAD_SHA below)"

# --- 2. SANITY: fast offline rail tests -------------------------------------
if [ "$RUN_TESTS" = "1" ]; then
    echo ""
    echo "[2/4] Sanity: precision-rail offline tests ..."
    if python3 phase5/tests/test_precision_rail.py >/dev/null 2>&1; then
        echo "  PASS"
    else
        echo "  FAIL — rail tests broken. Fix before going live."; exit 1
    fi
else
    echo "[2/4] Skipped (AIVA_RUN_TESTS=0)"
fi

# --- 3. START: kill stale, boot everything ----------------------------------
echo ""
echo "[3/4] Killing stale processes ..."
for port in 3001 8081 5173; do
    kill -9 $(lsof -ti:"$port") 2>/dev/null
done
pkill -9 -f "agent.main start" 2>/dev/null
pkill -9 -f "agent.token_server" 2>/dev/null
sleep 1
mkdir -p logs

echo "  token server ..."
uv run python -m agent.token_server > logs/token_server.out 2>&1 &
T1=$!

echo "  workers ($WORKER_COUNT) ..."
for i in $(seq 1 "$WORKER_COUNT"); do
    WID=$((i-1))
    env AIVA_STATE_ENGINE=1 WORKER_TARGET=cloud WORKER_ID=$WID \
        uv run python -m agent.main start > "logs/worker_$WID.out" 2>&1 &
    eval "T$((WID+2))=\$!"
done

if [ -d frontend ]; then
    echo "  frontend ..."
    (cd frontend && exec npm run dev) > logs/frontend.out 2>&1 &
    T99=$!
fi

# --- 4. VERIFY: every worker on the locked commit ---------------------------
echo ""
echo "[4/4] Waiting for workers to print their build stamp ..."
FAILED=0
WAIT_SECS=60
for i in $(seq 0 $((WORKER_COUNT-1))); do
    out="logs/worker_$i.out"
    line=""
    for _ in $(seq 1 $WAIT_SECS); do
        line=$(grep -m1 '\[BUILD\] git=' "$out" 2>/dev/null || true)
        [ -n "$line" ] && break
        sleep 1
    done
    if [ -n "$line" ]; then
        sha=$(echo "$line" | sed -E 's/.*\[BUILD\] git=([0-9a-f]+).*/\1/')
        if [ "${sha:0:12}" = "$HEAD_SHA" ]; then
            echo "  worker $i OK   : $line"
        else
            echo "  worker $i MISMATCH: $line (expected $HEAD_SHA)"; FAILED=1
        fi
    else
        echo "  worker $i NO STAMP in $out after ${WAIT_SECS}s — stale code, failed start, or still booting. Last lines:"
        tail -n 15 "$out" 2>/dev/null | sed 's/^/    /'
        FAILED=1
    fi
done

echo ""
echo "=============================================================="
if [ "$FAILED" = "0" ]; then
    echo "  DEPLOY VERIFIED — all $WORKER_COUNT worker(s) on $HEAD_SHA"
    echo "  Frontend: http://localhost:5173"
    echo "  Smoke kit: docs/SMOKE_KIT_V8.md (expected build = $HEAD_SHA)"
    echo "  Press Ctrl+C to stop everything."
else
    echo "  DEPLOY FAILED — workers NOT verified. Do NOT smoke until fixed."
    exit 1
fi
echo "=============================================================="

cleanup() {
    echo ""
    echo "Aiva stopped."
    pkill -9 -f "agent.main start" 2>/dev/null
    pkill -9 -f "agent.token_server" 2>/dev/null
    for pid in $T1 ${T2:-} ${T3:-} ${T99:-}; do
        kill -9 "$pid" 2>/dev/null
    done
    exit 0
}
trap cleanup INT TERM
wait
