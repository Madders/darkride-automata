"""Speaking bots' voices: each line a bot can say, spoken by the computer and turned into a machine (Windows).

For bots with a "voice" in the roster ({"name": "line", ...}, docs/designing-bots.md): each line is spoken by
Windows' own speech engine (System.Speech, through PowerShell), then made a machine's: slowed and lowered (a big
bot's slow drawl), ring-modulated, crunched a little, squeezed into a radio's band and given a metallic echo. Written
as sounds/<name>.wav (24 kHz mono, what a bot plays) into the output folder. The brain says them by name.

The voices are Windows': check their terms before you publish the sounds they make. The bots' beeps and chirps
(make_sounds.py) are synthesised and free of that.

Usage: python make_voice.py --roster <bots.json> --out <bot folder> [--voice "Microsoft Hazel Desktop"] [--pitch 0.72]
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import wave

import numpy as np

RATE = 24000


def arg(name, default=None):
    argv = sys.argv[1:]
    return argv[argv.index(name) + 1] if name in argv else default


def speak(text, path, voice):
    """Windows' speech engine saying text into a WAV file."""
    script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "try { $s.SelectVoice('%s') } catch {}; $s.Rate = 1; "
              "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(%d, "
              "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
              "$s.SetOutputToWaveFile('%s', $f); $s.Speak('%s'); $s.Dispose()"
              % (voice.replace("'", "''"), RATE, str(path).replace("'", "''"), text.replace("'", "''")))
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True)


def read(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(float) / 32768


def machine(x, pitch):
    """A big machine's voice from a person's: slower and lower, ring-modulated, crunched, a radio's band, an echo."""
    loud = np.nonzero(np.abs(x) > 0.02)[0]                                     # trim the engine's silence
    if len(loud):
        x = x[max(0, loud[0] - 400):loud[-1] + 1200]
    n = int(len(x) / pitch)
    x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)          # slower and lower together
    t = np.arange(len(x)) / RATE
    x = x * (0.6 + 0.4 * np.sin(2 * np.pi * 38 * t))                           # a mechanical warble
    x = np.tanh(x * 4) / np.tanh(4)                                             # crunch
    x = np.round(x * 48) / 48                                                   # a little bit-crush
    spectrum = np.fft.rfft(x)                                                   # a radio's band: 180 Hz to 3.2 kHz
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    spectrum *= (f > 180) * (f < 3200) * (1 + 0.8 * np.exp(-((f - 900) / 300) ** 2))
    x = np.fft.irfft(spectrum, len(x))
    out = np.zeros(len(x) + int(0.3 * RATE))                                    # a metal-room echo
    for delay, gain in ((0, 1.0), (0.045, 0.35), (0.11, 0.2), (0.21, 0.1)):
        k = int(delay * RATE)
        out[k:k + len(x)] += gain * x
    return out / max(1e-6, np.abs(out).max()) * 0.85


def save(path, x):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def main():
    roster = json.loads(pathlib.Path(arg("--roster", "examples/bots.json")).read_text(encoding="utf-8"))
    out = pathlib.Path(arg("--out", "dist/bot")) / "sounds"
    voice, pitch = arg("--voice", "Microsoft Hazel Desktop"), float(arg("--pitch", "0.72"))
    with tempfile.TemporaryDirectory() as tmp:
        for bot in roster["bots"]:
            for name, text in bot.get("voice", {}).items():
                raw = pathlib.Path(tmp) / (name + ".wav")
                speak(text, raw, bot.get("voiceName", voice))
                save(out / (name + ".wav"), machine(read(raw), bot.get("voicePitch", pitch)))
                print("%-8s %-12s %s" % (bot["id"], name, text))


if __name__ == "__main__":
    main()
