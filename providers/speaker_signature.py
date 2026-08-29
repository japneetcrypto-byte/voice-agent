"""Speaker / echo signature primitives (owner brief 2026-08-29).

Owner proposal: use voice attributes to attribute audio to a speaker BEFORE
ASR — agent echo dies at the audio level, and a second human becomes
speaker_2 with their own key. Adopted refinement: similarity + confidence +
temporal consistency, never binary "voice changed".

STAGE 1 (this module): acoustic echo correlation, multi-band.

Key insight: we possess the EXACT PCM Aiva played. Echo is that signal
replayed through speaker+room and captured by the mic. A single broadband
envelope correlation has a high false-match floor (any two syllabic signals
peak ~0.6-0.8 over a long alignment search), so we correlate FOUR frequency
band envelopes JOINTLY: a true echo matches across all bands at the SAME
alignment, while unrelated speech's chance peaks do not co-occur.

  score = max_over_alignments( mean_b( NCC_b(alignment) ) )

Per-lag energy normalization (exact normalized cross-correlation), FFT for
speed. Pure numpy, deterministic, no model downloads, ~tens of ms.

STAGE 3 (multi-speaker registry, speaker_2 keys) needs real speaker
embeddings — env-gated, owner decision (docs/SPEAKER_ATTRIBUTION_DESIGN.md).
"""
from __future__ import annotations

import numpy as np

SR = 16000
ENV_DECIM = 16            # envelope rate 1000 Hz (1 ms/sample)
ENV_SMOOTH_K = 80         # 5 ms smoothing at 16 kHz
MAX_CAP_S = 8.0           # correlate at most this much of the capture
BANDS = ((100.0, 500.0), (500.0, 1200.0), (1200.0, 2600.0), (2600.0, 5500.0))
EPS = 1e-9


def _band_envelopes(x: np.ndarray) -> list[np.ndarray]:
    """Per-band smoothed envelopes at ~1 kHz. x: float32 @16 kHz."""
    n = len(x)
    X = np.fft.rfft(x.astype(np.float64))
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    kernel = np.ones(ENV_SMOOTH_K, dtype=np.float32) / ENV_SMOOTH_K
    envs = []
    for lo, hi in BANDS:
        Xb = X.copy()
        Xb[(freqs < lo) | (freqs > hi)] = 0.0
        b = np.fft.irfft(Xb, n)
        env = np.abs(b)
        env = np.convolve(env, kernel, mode="same")[::ENV_DECIM].astype(np.float32)
        env -= env.mean()
        nn = float(np.linalg.norm(env))
        envs.append(env / nn if nn > EPS else env)
    return envs


def _ncc_norms(play_env: np.ndarray, m: int) -> np.ndarray:
    """Per-lag local-energy norms for windows of length m over play_env."""
    p2 = np.concatenate(([0.0], np.cumsum(play_env.astype(np.float64) ** 2)))
    e = p2[m:] - p2[: len(play_env) - m + 1]
    return np.sqrt(np.maximum(e, EPS))


def echo_score(captured_16k: np.ndarray, played_16k: np.ndarray) -> float | None:
    """Joint multi-band echo score of the captured utterance against the
    recent played audio. Returns None when there isn't enough signal to judge
    (caller falls back to the text-level filter). ~1.0 only when the capture
    is essentially a replay of what was played."""
    if captured_16k is None or played_16k is None or len(captured_16k) == 0:
        return None
    cap_raw = np.asarray(captured_16k, dtype=np.float32)[: int(MAX_CAP_S * SR)]
    play_raw = np.asarray(played_16k, dtype=np.float32)
    m = len(cap_raw) // ENV_DECIM
    n = len(play_raw) // ENV_DECIM
    if m < 25 or n < m + 5:
        return None

    cap_bands = _band_envelopes(cap_raw)
    play_bands = _band_envelopes(play_raw)

    joint = None
    for cb, pb in zip(cap_bands, play_bands):
        if len(cb) < m or len(pb) < len(cb) + 5:
            return None
        cb = cb[:m]
        pb = pb[:n]
        nfft = 1 << int(n + m - 1).bit_length()
        cc = np.fft.irfft(np.conj(np.fft.rfft(cb, nfft)) * np.fft.rfft(pb, nfft),
                          nfft)[: n - m + 1]
        joint = cc / _ncc_norms(pb, m) if joint is None else joint + cc / _ncc_norms(pb, m)
    return round(float(joint.max()) / len(BANDS), 4)
