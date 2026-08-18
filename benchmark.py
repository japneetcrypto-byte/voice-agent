import time
from gtts import gTTS
import os
from faster_whisper import WhisperModel

def create_test_audio():
    print("Generating test audio...")
    text_en = "Hello, who are you? What can you help me with?"
    text_hi = "Bhai mujhe simple language mein samjha."
    
    tts_en = gTTS(text_en, lang='en')
    tts_en.save("test_en.mp3")
    
    tts_hi = gTTS(text_hi, lang='hi')
    tts_hi.save("test_hi.mp3")
    print("Audio generated.")

def benchmark_model(model_size):
    print(f"\n--- Benchmarking {model_size} ---")
    start_load = time.time()
    # Compute type "int8" for speed, cpu since it's a generic VM
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    load_time = time.time() - start_load
    print(f"Model load time: {load_time:.2f}s")
    
    for file, lang in [("test_en.mp3", "English"), ("test_hi.mp3", "Hindi/Hinglish")]:
        start_transcribe = time.time()
        segments, info = model.transcribe(file, beam_size=5)
        text = " ".join([segment.text for segment in segments])
        transcribe_time = time.time() - start_transcribe
        print(f"[{lang}] Transcribe time: {transcribe_time:.2f}s | Result: {text.strip()}")

if __name__ == "__main__":
    if not os.path.exists("test_en.mp3"):
        create_test_audio()
        
    benchmark_model("small")
    benchmark_model("medium")
