#!/usr/bin/env bash
# =============================================================================
# Phase 1 bootstrap — Fish Speech v1.5.1 on Oracle A1 (Ubuntu 22.04, aarch64)
# Compliant with docs/PHASE1_PLAN.md decision log (D1–D6).
#
#   sudo bash bootstrap.sh                # install + start service + health
#   sudo bash bootstrap.sh --open-firewall  # additionally open 80/443/7880/7881/7882
#
# Idempotent: safe to re-run; completed stages are skipped.
# API listens on 127.0.0.1:8880 ONLY (D4 — internal by binding, not by firewall).
# =============================================================================
set -euo pipefail

FISH_REPO="https://github.com/fishaudio/fish-speech.git"
FISH_TAG="v1.5.1"                 # D1 — pin; script asserts this tag
MODEL_ID="fishaudio/fish-speech-1.5"
INSTALL_DIR="/opt/fish-speech"
LISTEN="127.0.0.1:8880"           # D4 — loopback only
SERVICE_NAME="fish-speech"
RUN_USER="${SUDO_USER:-ubuntu}"
LOG_FILE="/var/log/fish-speech-bootstrap.log"

exec > >(tee -a "$LOG_FILE") 2>&1
echo "== Phase 1 bootstrap started $(date -Is) =="

[[ $EUID -eq 0 ]] || { echo "ERROR: run with sudo"; exit 1; }

# ---------------------------------------------------------------------------
# Stage 1 — system deps (D2/D3 support)
# ---------------------------------------------------------------------------
if [[ ! -f /var/lib/fish-speech-stage1 ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y python3-venv python3-pip git ffmpeg curl \
                     build-essential portaudio19-dev htop netfilter-persistent
  touch /var/lib/fish-speech-stage1
else
  echo "[skip] apt deps already installed"
fi
python3 --version   # expect 3.10.x on Ubuntu 22.04 (D2: >=3.10 satisfied)

# ---------------------------------------------------------------------------
# Stage 2 — pinned source tree (D1)
# ---------------------------------------------------------------------------
if [[ ! -d "$INSTALL_DIR/.git" ]]; then
  git clone "$FISH_REPO" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
CURRENT_TAG="$(git describe --tags --exact-match 2>/dev/null || true)"
if [[ "$CURRENT_TAG" != "$FISH_TAG" ]]; then
  git fetch --tags origin
  git checkout -f "$FISH_TAG"
fi
[[ "$(git describe --tags --exact-match)" == "$FISH_TAG" ]] || { echo "ERROR: tag pin failed"; exit 1; }
echo "[ok] fish-speech pinned at $FISH_TAG"

# ---------------------------------------------------------------------------
# Stage 3 — venv + aarch64 torch (D3), then `.[stable]` (D2)
# ---------------------------------------------------------------------------
if [[ ! -x venv/bin/python ]]; then
  python3 -m venv venv
fi
./venv/bin/pip install --upgrade pip wheel setuptools
if ./venv/bin/python -c "import torch" 2>/dev/null; then
  echo "[skip] torch present"
else
  ./venv/bin/pip install torch==2.4.1 torchaudio==2.4.1 \
    || ./venv/bin/pip install torch==2.4.1 torchaudio==2.4.1 \
         --index-url https://download.pytorch.org/whl/cpu
fi
./venv/bin/python -c "import torch; print('torch', torch.__version__, 'threads', torch.get_num_threads())"
./venv/bin/pip install -e ".[stable]" "huggingface_hub[cli]" requests

# ---------------------------------------------------------------------------
# Stage 4 — model weights (~4 GB)
# ---------------------------------------------------------------------------
if [[ ! -f "checkpoints/fish-speech-1.5/firefly-gan-vq-fsq-8x1024-21hz-generator.pth" ]]; then
  ./venv/bin/huggingface-cli download "$MODEL_ID" \
    --local-dir checkpoints/fish-speech-1.5 \
    --local-dir-use-symlinks False || \
  ./venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('$MODEL_ID', local_dir='checkpoints/fish-speech-1.5')"
fi
test -f "checkpoints/fish-speech-1.5/firefly-gan-vq-fsq-8x1024-21hz-generator.pth" \
  || { echo "ERROR: weights download incomplete"; exit 1; }
echo "[ok] weights present"

# ---------------------------------------------------------------------------
# Stage 5 — systemd service on loopback (D4/D5)
# ---------------------------------------------------------------------------
cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Fish Speech 1.5 API (loopback only)
After=network-online.target
Wants=network-online.target

[Service]
User=${RUN_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python -m tools.api_server \
  --listen ${LISTEN} \
  --llama-checkpoint-path ${INSTALL_DIR}/checkpoints/fish-speech-1.5 \
  --decoder-checkpoint-path ${INSTALL_DIR}/checkpoints/fish-speech-1.5/firefly-gan-vq-fsq-8x1024-21hz-generator.pth \
  --decoder-config-name firefly_gan_vq
Restart=always
RestartSec=5
Environment=GRADIO_ANALYTICS_ENABLED=False
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
chown -R "$RUN_USER":"$RUN_USER" "$INSTALL_DIR"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

# ---------------------------------------------------------------------------
# Stage 6 — health wait (model load can take minutes on CPU)
# ---------------------------------------------------------------------------
echo "waiting for /v1/health (model load)..."
OK=""
for i in $(seq 1 60); do
  sleep 10
  BODY="$(curl -s --max-time 5 http://127.0.0.1:8880/v1/health || true)"
  echo "  attempt $i: $BODY"
  if [[ "$BODY" == *'"ok"'* || "$BODY" == *'"status": "ok"'* || "$BODY" == *'"status":"ok"'* ]]; then OK=1; break; fi
done
[[ -n "$OK" ]] || { echo "ERROR: health never OK; logs:"; journalctl -u "$SERVICE_NAME" -n 50 --no-pager; exit 1; }

# ---------------------------------------------------------------------------
# Stage 7 — optional firewall (D6). 8880 is NEVER opened.
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--open-firewall" ]]; then
  for PORT in 80 443 7880 7881; do
    iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null || \
      iptables -I INPUT 1 -p tcp --dport "$PORT" -j ACCEPT
  done
  iptables -C INPUT -p udp --dport 7882 -j ACCEPT 2>/dev/null || \
    iptables -I INPUT 1 -p udp --dport 7882 -j ACCEPT
  netfilter-persistent save
  echo "[ok] iptables opened: 80,443,7880,7881/tcp 7882/udp (persisted). Remember OCI Security List too."
fi

echo "== REPORT =="
echo "public_ip: $(curl -s --max-time 8 ifconfig.me || echo unknown)"
echo "health:    $(curl -s http://127.0.0.1:8880/v1/health)"
echo "cpus:      $(nproc)"
free -h
df -h /
systemctl --no-pager --lines=3 status "$SERVICE_NAME" || true
echo "== Phase 1 bootstrap complete — next: bench_tts.py (plan §5, gate G5) =="
