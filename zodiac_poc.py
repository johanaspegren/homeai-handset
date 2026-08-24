import logging
import os
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time

import ollama
from dotenv import load_dotenv
from faster_whisper import WhisperModel

load_dotenv()


# ============================================================
# Configuration
# ============================================================

MODEL = os.getenv("OLLAMA_MODEL", "gemma4:12b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://homeai.local:11434")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "tiny")
WHISPER_VAD = os.getenv("WHISPER_VAD", "false").lower() in {"1", "true", "yes", "on"}
AUDIO_DEVICE = os.getenv("AUDIO_DEVICE", "default")

SYSTEM_PROMPT = """
You are HomeAI speaking to Johan through an old telephone handset.

This is a spoken telephone conversation.

Rules:
- Be brief.
- Usually answer in one or two short sentences.
- Speak naturally and conversationally.
- Never use markdown, bullet points, headings, tables or formatting.
- Do not give long explanations unless explicitly asked.
- Prefer short sentences because speech begins before your full response is generated.
- Do not describe yourself as an AI unless asked.
"""

GREETING = "HomeAI. Hello Johan."

# Sentence boundary used to start TTS as early as possible.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("zodiac")


# ============================================================
# Whisper
# ============================================================

log.info("Loading Whisper model: %s", WHISPER_MODEL)

whisper = WhisperModel(
    WHISPER_MODEL,
    device="cpu",
    compute_type="int8",
)

log.info("Whisper ready")


# ============================================================
# Conversation
# ============================================================

messages = [
    {
        "role": "system",
        "content": SYSTEM_PROMPT,
    }
]


# ============================================================
# TTS worker
# ============================================================

tts_queue = queue.Queue()


def tts_worker():
    """
    Runs independently from Ollama.

    Gemma can therefore continue generating while we're already
    speaking completed sentences.
    """

    while True:

        text = tts_queue.get()

        if text is None:
            break

        log.info("TTS >>> %s", text)

        started = time.perf_counter()

        subprocess.run(
            [
                "espeak-ng",
                "-s", "155",
                "-p", "35",
                text,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        elapsed = time.perf_counter() - started

        log.info("TTS finished in %.2fs", elapsed)

        tts_queue.task_done()


threading.Thread(
    target=tts_worker,
    daemon=True,
).start()


def speak(text):
    """
    Queue speech without blocking LLM generation.
    """

    if text.strip():
        tts_queue.put(text.strip())


# ============================================================
# Recording
# ============================================================

def record():

    fd, filename = tempfile.mkstemp(suffix=".wav")
    os.close(fd)

    log.info("MIC OPEN")
    print("\n🎙️  Speak now — ENTER when finished")

    started = time.perf_counter()

    process = subprocess.Popen(
        [
            "arecord",
            "-D", AUDIO_DEVICE,
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1",
            "-t", "wav",
            filename,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    input()

    process.send_signal(signal.SIGINT)
    _, stderr_text = process.communicate(timeout=10)

    elapsed = time.perf_counter() - started

    with open(filename, "rb") as fh:
        header = fh.read(12)

    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        os.remove(filename)
        message = (
            "Recording did not produce a valid WAV file. "
            "Check your microphone and AUDIO_DEVICE setting."
        )
        if stderr_text:
            message += f" arecord reported: {stderr_text.strip()}"
        raise ValueError(message)

    log.info(
        "MIC CLOSED | %.2fs recorded | %s",
        elapsed,
        filename,
    )

    return filename


# ============================================================
# Speech recognition
# ============================================================

def transcribe(filename):

    log.info("STT starting")

    started = time.perf_counter()

    segments, info = whisper.transcribe(
        filename,
        beam_size=1,
        vad_filter=WHISPER_VAD,
        without_timestamps=True,
    )

    text = " ".join(
        segment.text.strip()
        for segment in segments
    ).strip()

    elapsed = time.perf_counter() - started

    log.info(
        "STT finished | %.2fs | language=%s",
        elapsed,
        info.language,
    )

    log.info("HEARD <<< %s", text or "[nothing]")

    try:
        os.remove(filename)
    except OSError:
        pass

    return text


# ============================================================
# Sentence extraction
# ============================================================

def extract_sentences(buffer):
    """
    Pull complete sentences out of the streaming LLM response.

    Returns:
        completed sentences
        remaining unfinished text
    """

    parts = SENTENCE_END.split(buffer)

    if len(parts) == 1:
        return [], buffer

    completed = parts[:-1]
    remaining = parts[-1]

    return completed, remaining


# ============================================================
# Ollama
# ============================================================

ollama_client = ollama.Client(host=OLLAMA_HOST)


def ask_homeai(user_text):

    messages.append(
        {
            "role": "user",
            "content": user_text,
        }
    )

    log.info("PROMPT ------------------------------------------------")
    log.info("SYSTEM: %s", " ".join(SYSTEM_PROMPT.split()))
    log.info("USER:   %s", user_text)
    log.info("MODEL:  %s", MODEL)
    log.info("OLLAMA HOST: %s", OLLAMA_HOST)
    log.info("HISTORY: %d messages", len(messages))
    log.info("-------------------------------------------------------")

    started = time.perf_counter()
    first_token_time = None
    first_speech_time = None

    response = ""
    speech_buffer = ""

    print("\n☎️  HomeAI: ", end="", flush=True)

    log.info("OLLAMA request sent")

    stream = ollama_client.chat(
        model=MODEL,
        messages=messages,
        stream=True,
        # If your Ollama/model supports this argument:
        think=False,
        options={
            "temperature": 0.3,
            "num_predict": 120,
        },
    )

    for chunk in stream:

        token = chunk["message"].get("content", "")

        if not token:
            continue

        now = time.perf_counter()

        if first_token_time is None:
            first_token_time = now

            log.info(
                "FIRST TOKEN after %.3fs",
                first_token_time - started,
            )

        response += token
        speech_buffer += token

        print(token, end="", flush=True)

        sentences, speech_buffer = extract_sentences(
            speech_buffer
        )

        for sentence in sentences:

            sentence = sentence.strip()

            if not sentence:
                continue

            if first_speech_time is None:
                first_speech_time = time.perf_counter()

                log.info(
                    "FIRST SPEECH queued after %.3fs",
                    first_speech_time - started,
                )

            speak(sentence)

    # Speak whatever remains after generation finishes.
    if speech_buffer.strip():

        if first_speech_time is None:
            first_speech_time = time.perf_counter()

            log.info(
                "FIRST SPEECH queued after %.3fs",
                first_speech_time - started,
            )

        speak(speech_buffer)

    print()

    total = time.perf_counter() - started

    log.info("RESPONSE <<< %s", response)
    log.info("OLLAMA finished in %.3fs", total)

    messages.append(
        {
            "role": "assistant",
            "content": response,
        }
    )

    return response


# ============================================================
# Telephone behaviour
# ============================================================

def answer_phone():

    print()
    print("☎️  *click*")

    log.info("HANDSET OFF HOOK")
    log.info("CALL STARTED")

    print("☎️  HomeAI:", GREETING)

    speak(GREETING)

    # For the greeting, wait until HomeAI has finished speaking.
    # Later the physical handset/PTT state can control this.
    tts_queue.join()


def hang_up():

    log.info("CALL ENDING")

    speak("Goodbye.")

    tts_queue.join()

    print("\n☎️  *click*")

    log.info("HANDSET ON HOOK")
    log.info("CALL ENDED")


# ============================================================
# Main
# ============================================================

def main():

    answer_phone()

    while True:

        print("\n[ENTER = talk | Q = hang up]")

        command = input("> ").strip().lower()

        if command == "q":
            hang_up()
            break

        audio_file = record()

        text = transcribe(audio_file)

        if not text:
            log.warning("No speech detected")
            continue

        print(f"\n👤 Johan: {text}")

        ask_homeai(text)

        # Don't start listening while we're still speaking.
        tts_queue.join()


if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:

        print("\n")
        log.info("Interrupted")

    finally:
        tts_queue.put(None)