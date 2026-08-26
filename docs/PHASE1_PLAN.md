# Phase 1 — Locked Execution Plan (Oracle VM + Fish Speech)

**Status:** LOCKED — 2026-08-26 · **Branch:** `arena/01a03e6f-voice-agent` · **Rule:** no voice-agent code changes this pass (infra/docs only).

Decisions baked in from your answers: **(1)** realtime conversation is required, **(2)** no Oracle account yet + want card-free options, **(3)** access mode = I drive the VM over SSH, **(4)** personal/portfolio now, possibly commercial later ("vent-out" companion concept).

---

## 0. Verdict & strategy

**Doable — but not as a single track.** Your brief assumes self-hosted Fish Speech on a free Oracle ARM box. That works, but one fact drives everything:

> The Oracle Always Free VM is **CPU-only**. Fish Speech on 4 ARM cores runs at roughly **0.75–3× real-time generation speed** (community benchmarks: ~45 s of compute per 1 min of audio on a good desktop CPU; our 4-core Altra will be slower). Installed and healthy: yes. **Feels-instant live conversation: at risk.** The benchmark gate in §5 decides this with numbers, not opinions — before any Phase 2 work.

So the plan is two tracks that share one voice sample:

| | **Track A — Hosted clone (recommended first)** | **Track B — Oracle self-host (your brief)** |
|---|---|---|
| Card needed | **No** (signup) | **Yes** (verification only; Always Free itself is $0) |
| Realtime latency | ✅ cloud-grade | ⚠️ benchmark gate decides |
| Voice clone | ✅ upload sample on fish.audio | ✅ same sample, self-hosted |
| Cost | $0 free tier to start; cloning/API may need ~$5–15/mo tier | $0, but see idle-reclaim risk §7 |
| Status in repo | env var already exists; provider wiring is a small Phase 2 item | everything in this doc |

**Recommended order:** record the sample **today** → run Track A end-to-end this week (fastest path to a realtime demo of your clone) → start Track B in parallel (Oracle signup + provisioning) → the §5 benchmark decides whether Phase 2's TTS provider is self-hosted, hosted, or hosted-for-realtime + self-hosted-for-batch. The Oracle VM is worth building **regardless** of the TTS outcome: it becomes your 24/7 host for LiveKit + the agent worker, so the demo stops depending on your laptop.

---

## 1. What is live today (verified inventory)

- **Nothing is deployed.** The repo is a local-dev MVP meant to run in 4 terminals (docker LiveKit, token server, 2 agent workers, Vite frontend). I have no visibility into any machines outside this workspace; in this sandbox nothing is running.
- **Agent stack (as coded):** LiveKit Cloud primary + local Docker fallback · STT faster-whisper/Groq · LLM Gemini Flash · **TTS EdgeTTS (`en-IN-NeerjaNeural`)** · VAD TEN VAD.
- **⚠️ Correction to the README:** `FISH_AUDIO_API_KEY` / `FISH_AUDIO_REFERENCE_ID` are read in `agent/config.py` but **no Fish Audio provider exists in `providers/tts.py`**. The `livekit-plugins-fishaudio` package is installed but unwired. Fish Audio is *configured, not live*.
- **Fish Speech (self-hosted): not installed anywhere.** That's Track B, Phase 1.

## 2. Scope — locked for this pass

**IN (Track B, per your brief):** provision Oracle A1.Flex 4 OCPU/24 GB Ubuntu 22.04 · open 22/80/443/7880/7881/7882 · install Fish Speech pinned **v1.5.1** + `fish-speech-1.5` weights · run API on **127.0.0.1:8880** as a systemd service · `/v1/health` returns OK · resource + latency report.

**OUT (Phase 2+, explicitly):** LiveKit install, TTS provider swap in `providers/tts.py`, voice clone upload wiring, domain/TLS, `.env` changes, any `agent/` or `providers/` edits.

**IN (user side, Track A prep):** voice sample + fish.audio account + clone creation (no repo changes needed for the account steps).

## 3. Pre-made decision log (anti-deviation — no mid-execution debate)

| # | Topic | Decision | Reason |
|---|---|---|---|
| D1 | fish-speech version | Pin tag **`v1.5.1`** (commit `58046ea`) + weights `fishaudio/fish-speech-1.5` | Your brief's commands (`tools.api_server --llama-checkpoint-path … firefly_gan_vq`) match this era exactly. Repo `main` has moved (OpenAudio S1, different flags/decoder) — main is a deviation trap. |
| D2 | pip extra | **`.[stable]`**, NOT `.[inference]` | Verified against the v1.5.1 `pyproject.toml`: no `[inference]` extra exists; `stable` = `torch<=2.4.1` + `torchaudio`. Python ≥3.10 → system Python 3.10 on Ubuntu 22.04 is fine (no deadsnakes needed). |
| D3 | torch on aarch64 | `pip install torch==2.4.1 torchaudio==2.4.1` from PyPI first (aarch64 manylinux wheels exist), then `-e .[stable]` keeps it | Avoids any sdist source-build on ARM. Fallback if wheel missing: CPU index `--index-url https://download.pytorch.org/whl/cpu`. |
| D4 | 8880 exposure | Bind **`127.0.0.1:8880`**, never 0.0.0.0; port never opened in Security List or iptables | Brief says "internal only". Loopback binding achieves that with zero firewall dependence (Phase 2 agent runs on the same VM). |
| D5 | Process mgmt | systemd unit `fish-speech.service`, `Restart=always` | Survives reboots/reconnects; no tmux folklore. |
| D6 | Firewall order | Security List/NSG (OCI console) **and** iptables (Oracle Ubuntu ships a REJECT-most INPUT chain in `/etc/iptables/rules.v4`). Script inserts ACCEPTs + `netfilter-persistent save`. **Do not enable ufw.** | Missing either layer = "works on my machine" mystery. ufw risks lockout. |
| D7 | Latency gate | `bench_tts.py`: 3 runs, typical agent-length utterance. **PASS** RTF ≤ 0.75 · **PARTIAL** 0.75–2.0 (usable only w/ streaming + fillers) · **FAIL** > 2.0 | Pre-agreed thresholds so the Phase 2 provider choice is mechanical, not emotional. |
| D8 | Gate-fail fallback | FAIL ⇒ hosted Fish Audio for realtime (reference_id via existing env var + small Phase 2 provider), self-hosted stays for batch/preview; VM still hosts LiveKit+agent | Realtime is a hard requirement (your answer). |
| D9 | Oracle idle-reclaim | Accept it: instance may be **stopped** after ~7 days of <10–20% CPU/net/mem; restart is a click (boot volume persists). **No fake-load/stress tools — TOS violation, ban risk.** | Policy is documented; a used voice agent won't be idle. PAYG upgrade (still $0 within Always Free limits) exempts you and improves capacity priority. |
| D10 | License | fish-speech **code** Apache-2.0; **weights CC-BY-NC-SA-4.0 (non-commercial)**. OK for portfolio/personal now. If it goes commercial ⇒ hosted Fish Audio API (commercial terms) or commercial-friendly model — decided at that time, not hidden. | Your answer: personal now, commercial maybe later. |
| D11 | SSH access | Keypair **already generated** in this workspace: `infra/oracle/keys/oracle_vm_ed25519(.pub)` (private half gitignored). You paste the `.pub` into the Oracle console at instance creation; I SSH as `ubuntu@<ip>`. | Zero round-trips later. Rotate the key after provisioning if you want. |
| D12 | Capacity misses | If "Out of capacity": try each AD (availability domain) · retry off-peak IST early morning · last resort: upgrade to PAYG (A1 stays free; priority + no reclaim) | Capacity for A1 is the #1 Oracle friction. |

**Card-free self-host alternative (for the record):** Google Colab (free T4, no card) or Kaggle (P100/T4, ~30 h/wk) can run fish-speech for *tests/demos* but sessions are ephemeral — not an always-on agent. There is **no card-free, always-on, free GPU/VM** option worth building on. Don't burn days hunting for one.

## 4. Voice sample — your task (needed for BOTH tracks)

- 30–60 s (45 s is the sweet spot), **quiet room, no echo, no music, single speaker (you), normal pace**. Phone mic is fine.
- Content: introduce yourself + read a paragraph naturally. This becomes the reference for cloning on fish.audio (Track A) and the bench reference on the VM (Track B).
- Deliver into this workspace: `samples/voice_reference.wav` (or .mp3) **+ paste the exact transcript** to `samples/voice_reference.txt` (cloning quality improves with the reference text).
- Clean-up check before submitting: no truncation, no overlaps, constant volume. I'll validate duration/format/loudness when it lands.

## 5. Track B — gate-by-gate execution (I do the VM work)

| Gate | Step | Owner | Pass criteria | Est. |
|---|---|---|---|---|
| **G0** | Oracle signup (card verification ~₹100 refundable hold; pick home region wisely — **cannot be changed**; stay Always Free tier) | You | Account active, can reach Compute → Instances | 30 min |
| **G1** | Create instance: `VM.Standard.A1.Flex`, 4 OCPU/24 GB, **Ubuntu 22.04** (aarch64), boot volume **≥ 50 GB**, paste `oracle_vm_ed25519.pub` at creation. If Out of capacity → D12. | You (console) + me (guidance) | Instance RUNNING; you send me the **public IP** | 15 min + capacity luck |
| **G2** | I SSH in; confirm `nproc`=4, `free -h`≈24 GB, arch=aarch64 | Me | Verified | 5 min |
| **G3** | Run `infra/oracle/bootstrap.sh` (idempotent; ~15–25 min incl. ~4 GB weights download): deps → pinned clone/venv → D2/D3 install → weights → systemd service on 127.0.0.1:8880 (D4/D5) | Me (SSH) | `curl 127.0.0.1:8880/v1/health` → `{"status":"ok"}` | 25 min |
| **G4** | Firewall pass: OCI Security List for 22/80/443/7880/7881/7882 + iptables via script flag (D6) | Me | External port probe matches D6; 8880 unreachable externally | 10 min |
| **G5** | Benchmark + report: `bench_tts.py` (D7) with your reference sample; deliver IP, health OK, `free -h`/`nproc`, RTF table, PASS/PARTIAL/FAIL verdict | Me | Report delivered in chat + this doc updated | 15 min |
| **Decision** | Phase 2 TTS provider per D7/D8 | You + me | One-line decision recorded here | — |

**Report-back format (per your brief):** public IP · `/v1/health` OK · `free -h` + `nproc` after model load · RTF benchmark table.

## 6. Track A — realtime clone with zero infra (do today)

1. Record sample (§4).
2. Create fish.audio account (no card). Upload sample → create your voice → copy **reference_id**.
   - Free tier is credit-limited and API access is typically not included; if cloning/API is gated, the entry paid tier is small (~$5–15/mo). Verify current terms at signup — pricing shifts often; I won't lock a number.
3. Drop `reference_id` into `.env` as `FISH_AUDIO_REFERENCE_ID` (the config line already exists).
4. **Phase 2 item (small, later):** wire a `FishAudioProvider` in `providers/tts.py` using the already-installed `livekit-plugins-fishaudio`. ~30 lines. Not this pass, per scope lock.

## 7. Risk register

| Risk | Likelihood | Impact | Mitigation (pre-decided) |
|---|---|---|---|
| CPU TTS too slow for realtime | Medium-High | Phase 2 design | G5 gate + D8 fallback. No sunk-cost debating. |
| A1 "Out of capacity" | High (IN regions especially) | Days of delay | D12: AD switching, off-peak retries, PAYG upgrade |
| Instance stopped by Oracle (idle) | Medium (low usage periods) | Demo down until restart | Restart is a click; PAYG exempts; **never** fake-load (ban risk) |
| Install breakage on `main` fish-speech | Avoided | — | D1 pin; script asserts the tag |
| aarch64 wheel gaps | Low | Install delay | D3 PyPI→CPU-index fallback; build-essential present as last resort |
| License blocks commercial pivot | Deferred | Later decision | D10 recorded; no surprise |
| README drift (Fish Audio "already working") | Fixed | — | Inventory corrected in §1 |

## 8. Phase 2 preview (NOT started; boundary only)

Voice-clone upload wiring · TTS provider swap (self-hosted endpoint and/or hosted `reference_id`) · LiveKit server deploy on the same VM + TLS · single-VM production compose · the "vent-out" persona design (system context + your cloned voice). Entry condition: G5 report + one-line provider decision.

---

## 9. Your checklist (everything on your side)

- [ ] **Record the 45 s voice sample** → `samples/voice_reference.wav` + transcript → `samples/voice_reference.txt` (rules §4)
- [ ] **Oracle signup** if/when you're ready for Track B — card needed for verification only (G0); tell me if you want the console walkthrough
- [ ] When creating the instance (G1): shape `VM.Standard.A1.Flex` 4/24, Ubuntu 22.04, 50 GB boot, image `aarch64`, and paste this public key:
  `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICiizYj9eFrclHNq+Pov8LgjEyYBC96eq8Uo2BW5JVJx voice-agent-arena-20260826`
  …then send me the **public IP**
- [ ] Optional today: fish.audio account + clone upload (Track A, §6)
- [ ] One-line answers when I ask: sample delivered ✅/❌ · IP obtained ✅/❌
