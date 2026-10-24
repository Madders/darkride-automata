"""The bots' ears: a small speech recogniser for their voice commands (docs/how-it-works.md, "Voice commands").

A bot listening to a visitor keeps what it hears (Agent.takeHeardAudio: 16-bit mono PCM at 24 kHz) and, when they
pause, posts the last few seconds here. This turns it into words with Vosk, offline, listening only for the bots'
command words and their names (a fixed vocabulary is quick and hard to fool), and answers with what it heard:

  POST /hear   body: raw PCM, 16-bit little-endian mono at 24 kHz   ->   {"text": "pip sit", "words": ["pip", "sit"]}
  GET  /       -> {"ok": true, "words": [...the vocabulary...]}

It also keeps the bots' memories (who they've met, who praised or scolded them, which bots are friends), so they
last across restarts: GET /memory/<bot id> gives one back (404 if none), POST /memory/<bot id> stores it. They live
in one JSON file, bots-memory.json beside the roster (--memory to put it elsewhere).

Run it on the machine the bots run on (they reach it at the roster's "hearing" URL, normally http://127.0.0.1:40195/hear).
Needs: vosk, numpy, and a Vosk model (the small English one: https://alphacephei.com/vosk/models, unzipped into
.models/). Usage: python hearing.py [--roster bots.json] [--model .models/vosk-model-small-en-us-0.15] [--port 40195] [--dump last.wav]
[--memory bots-memory.json]
"""
import http.server
import json
import pathlib
import re
import sys
import threading
import wave

import numpy as np
import vosk

KIT = pathlib.Path(__file__).resolve().parents[1]
COMMANDS = ["sit", "sit down", "jump", "roll", "come", "come here", "here", "follow", "follow me", "stay", "stop",
            "wait", "go away", "away", "dance", "spin", "hello", "hi", "good", "bad", "no",
            "tour", "yes", "okay", "sure"]


def arg(name, default=None):
    argv = sys.argv[1:]
    return argv[argv.index(name) + 1] if name in argv else default


class Ears:
    def __init__(self, model_path, names):
        vosk.SetLogLevel(-1)
        self.model = vosk.Model(str(model_path))
        self.words = sorted(set(COMMANDS + [n.lower() for n in names]))
        self.grammar = json.dumps(self.words + ["[unk]"])
        self.lock = threading.Lock()

    def hear(self, pcm24):
        x = np.frombuffer(pcm24, np.int16).astype(np.float32)
        if len(x) < 2400:
            return ""
        n = int(len(x) * 16000 / 24000)                                         # Vosk's small model wants 16 kHz
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16)
        rec = vosk.KaldiRecognizer(self.model, 16000, self.grammar)
        rec.AcceptWaveform(x.tobytes())
        return json.loads(rec.FinalResult()).get("text", "").replace("[unk]", "").strip()


def main():
    roster_path = pathlib.Path(arg("--roster", KIT / "examples" / "bots.json"))
    names = [b["name"] for b in json.loads(roster_path.read_text(encoding="utf-8"))["bots"]]
    model = pathlib.Path(arg("--model", KIT / ".models" / "vosk-model-small-en-us-0.15"))
    ears = Ears(model, names)
    memory_path = pathlib.Path(arg("--memory", roster_path.with_name("bots-memory.json")))
    memories = json.loads(memory_path.read_text(encoding="utf-8")) if memory_path.exists() else {}
    memory_lock = threading.Lock()
    dump = arg("--dump")

    class Handler(http.server.BaseHTTPRequestHandler):
        def reply(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def memory_id(self):
            m = re.fullmatch(r"/memory/([a-z0-9_-]{1,40})", self.path)
            return m and m.group(1)

        def do_GET(self):
            bot = self.memory_id()
            if bot:
                with memory_lock:
                    kept = memories.get(bot)
                if kept is None:
                    self.send_error(404)
                else:
                    self.reply(kept)
                return
            self.reply({"ok": True, "words": ears.words})

        def do_POST(self):
            size = int(self.headers.get("Content-Length", 0))
            bot = self.memory_id()
            if bot:
                if size > 1_000_000:
                    self.send_error(413)
                    return
                try:
                    kept = json.loads(self.rfile.read(size))
                except ValueError:
                    self.send_error(400)
                    return
                with memory_lock:
                    memories[bot] = kept
                    memory_path.write_text(json.dumps(memories, indent=1), encoding="utf-8")
                print("kept %s's memory (%d visitors)" % (bot, len(kept.get("visitors", {}))), flush=True)
                self.reply({"ok": True})
                return
            pcm = self.rfile.read(size)
            with ears.lock:
                text = ears.hear(pcm)
            print("heard %.1f s: %r" % (len(pcm) / 48000, text), flush=True)
            if dump:                                                            # --dump: keep the last clip, to listen to
                with wave.open(str(dump), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(pcm)
            self.reply({"text": text, "words": text.split()})

        def log_message(self, *a):
            pass

    host, port = arg("--host", "127.0.0.1"), int(arg("--port", "40195"))
    print("listening for %d words on http://%s:%d/hear" % (len(ears.words), host, port), flush=True)
    http.server.ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
