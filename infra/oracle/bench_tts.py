#!/usr/bin/env python3
"""Phase 1 latency gate (plan §5 / D7) — measures Fish Speech TTS RTF on the VM.

Usage:
  venv/bin/python bench_tts.py                       # base voice, 3 runs
  venv/bin/python bench_tts.py --reference ref.wav --reference-text "..."
Verdict: PASS RTF<=0.75 · PARTIAL 0.75–2.0 · FAIL >2.0  (typical agent utterance)
"""
import argparse, base64, json, statistics, subprocess, tempfile, time, urllib.request

TEXT = ("Hey, it's good to hear from you. Take your time, I'm listening — "
        "tell me what happened today and we'll figure it out together.")

def synth(base_url: str, text: str, reference: str | None, ref_text: str | None) -> bytes:
    payload = {"text": text, "format": "mp3", "chunk_length": 200,
               "top_p": 0.7, "temperature": 0.7}
    if reference:
        with open(reference, "rb") as f:
            payload["reference_audio"] = base64.b64encode(f.read()).decode()
        if ref_text:
            payload["reference_text"] = ref_text
    req = urllib.request.Request(
        f"{base_url}/v1/tts",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        audio = r.read()
    return audio, time.perf_counter() - t0

def duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8880")
    p.add_argument("--reference", default=None)
    p.add_argument("--reference-text", default=None)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--text", default=TEXT)
    a = p.parse_args()

    health = urllib.request.urlopen(f"{a.base_url}/v1/health", timeout=10).read().decode()
    print(f"health: {health}")

    rows = []
    for i in range(1, a.runs + 1):
        audio, wall = synth(a.base_url, a.text, a.reference, a.reference_text)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio); path = f.name
        dur = duration(path)
        rtf = wall / dur if dur else float("inf")
        rows.append((wall, dur, rtf))
        print(f"run {i}: gen={wall:6.2f}s  audio={dur:6.2f}s  RTF={rtf:5.2f}")

    r = statistics.median(x[2] for x in rows)
    verdict = "PASS" if r <= 0.75 else ("PARTIAL" if r <= 2.0 else "FAIL")
    print(f"\nmedian RTF: {r:.2f}  ->  gate verdict: {verdict}")
    if verdict == "PASS":
        print("CPU self-hosted TTS is viable for realtime conversation (with streaming).")
    elif verdict == "PARTIAL":
        print("Usable only with streaming + filler strategy; expect noticeable delay.")
    else:
        print("Per plan D8: use hosted Fish Audio for realtime; keep VM for LiveKit/agent.")

if __name__ == "__main__":
    main()
