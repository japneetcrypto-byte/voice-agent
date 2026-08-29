#!/usr/bin/env python3
"""Regression: multi-band acoustic echo correlation (stage 1 of speaker
attribution, owner brief 2026-08-29).

Evidence chain: 141753 — echo filter ate a real user word-repeat; text-level
similarity alone cannot tell replay from repetition.
Run: python3 phase5/tests/test_speaker_signature.py"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import numpy as np
from providers.speaker_signature import echo_score

SR = 16000
rng = np.random.default_rng(7)
fails = 0
def check(name, cond, extra=""):
    global fails
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails += 1

def speechlike(n, seed):
    """Harmonic synthetic speech: pitch + overtones, syllabic envelope."""
    r = np.random.default_rng(seed)
    x = np.zeros(n, dtype=np.float32)
    i = int(r.integers(2000, 6000))
    while i < n - 8000:
        dur = int(r.integers(2400, 6400))
        t = np.arange(dur) / SR
        f0 = r.uniform(120, 250)
        syl = (np.sin(2*np.pi*f0*t) + 0.5*np.sin(2*np.pi*2.2*f0*t)
               + 0.3*np.sin(2*np.pi*3.5*f0*t))
        syl *= np.sin(np.linspace(0, np.pi, dur)) * r.uniform(0.4, 1.0)
        x[i:i+dur] = syl
        i += dur + int(r.integers(1300, 4800))
    return (x + r.normal(0, 0.02, n)).astype(np.float32)

def highpass(x, cutoff=3200, g=0.35):
    X = np.fft.rfft(x)
    fr = np.fft.rfftfreq(len(x), 1/SR)
    X[fr > cutoff] *= g
    return np.fft.irfft(X, len(x)).astype(np.float32)

played = (speechlike(SR*12, 1) * 3000).astype(np.float32)
utt_n = SR*3

# --- true echo (degraded like a real room): attenuated + noisy + mic highpass ---
cap_echo = highpass(played[1300*16:1300*16+utt_n]*0.5
                    + rng.normal(0, 80, utt_n).astype(np.float32))
s_echo = echo_score(cap_echo, played)

# --- unrelated speakers: the false-match floor ---
s_others = [echo_score((speechlike(utt_n, s)*3000).astype(np.float32), played)
            for s in (99, 123, 77, 55, 31)]
mx_other = max(s_others)

check(f"true echo scores high ({s_echo})", s_echo is not None and s_echo > 0.45)
check(f"unrelated speech floor low ({mx_other:.2f})", mx_other is not None and mx_other < 0.35)
check("separation >= 1.5x", s_echo > 1.5 * mx_other)

# --- heavy degradation still separable ---
s_heavy = echo_score(highpass(played[2000*16:2000*16+utt_n]*0.25
                              + rng.normal(0, 250, utt_n).astype(np.float32)), played)
check(f"heavy-noise echo above floor ({s_heavy})", s_heavy is not None and s_heavy > 0.35
      and s_heavy > 1.2 * mx_other)

# --- degenerates -> None (caller falls back to text filter) ---
check("tiny capture -> None", echo_score(np.zeros(100), played) is None)
check("played too short -> None", echo_score(np.zeros(utt_n), played[:1000]) is None)
check("empty capture -> None", echo_score(None, played) is None)

# --- latency budget (shadow telemetry runs per turn) ---
big = (speechlike(SR*8, 5)*3000).astype(np.float32)
t0 = time.time(); echo_score(big, played); dt = (time.time()-t0)*1000
check(f"worst-case latency < 200ms ({dt:.0f}ms)", dt < 200)

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)
