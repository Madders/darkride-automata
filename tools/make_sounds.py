"""The ducks' voices: robot-duck chirps and beeps, synthesised here so they're ours to use (2026-10-24).

A small nasal "quack" timbre (a pulse wave through two formant-like resonances) with pitch glides, vibrato and short
envelopes, in a few moods:
- chirp_1..8: short replies, each a different contour;
- question: a rising "hm?", for listening;
- trill: a happy warble, for greetings and after a roll;
- ooh: a falling, wobbly "oooh", for peering over an edge;
- sad: a low falling note, for being ignored;
- alert: two quick beeps, for something new;
- rocket: the rocket feet's whoosh, for a hop;
- servo, clank, countdown: a big bot's hydraulics, heavy feet and countdown beeps.
Written as 16-bit mono WAV at 24 kHz to bot/sounds/. Usage: python make_sounds.py
"""
import math
import pathlib
import struct
import wave

import numpy as np

OUT = pathlib.Path(__file__).resolve().parents[1] / "bot" / "sounds"
RATE = 24000


def tone(pitch, seconds, vibrato=0.0, rate=0.0, brightness=0.6, seed=0):
    """A nasal robot-duck voice following pitch(t) (Hz), with vibrato depth (fraction) at rate (Hz)."""
    t = np.arange(int(seconds * RATE)) / RATE
    f = pitch(t) * (1 + vibrato * np.sin(2 * math.pi * rate * t))
    phase = 2 * math.pi * np.cumsum(f) / RATE
    # a pulse wave (narrow duty) is buzzy and nasal; mix in a sine to soften it
    pulse = np.where((phase / (2 * math.pi)) % 1.0 < 0.18, 1.0, -0.22)
    voice = brightness * pulse + (1 - brightness) * np.sin(phase)
    return formants(voice)


def formants(x):
    """Two resonances (about 900 Hz and 2.4 kHz) give the quacky nasal colour."""
    out = np.zeros_like(x)
    for centre, width, gain in ((900, 300, 1.0), (2400, 500, 0.6)):
        r = math.exp(-math.pi * width / RATE)
        c = 2 * r * math.cos(2 * math.pi * centre / RATE)
        y1 = y2 = 0.0
        y = np.empty_like(x)
        for i, v in enumerate(x):
            y0 = v + c * y1 - r * r * y2
            y[i] = y0
            y2, y1 = y1, y0
        out += gain * y / np.abs(y).max()
    return out


def envelope(x, attack=0.01, release=0.06):
    n = len(x)
    e = np.ones(n)
    a, r = int(attack * RATE), int(release * RATE)
    e[:a] = np.linspace(0, 1, a)
    e[n - r:] = np.linspace(1, 0, r) ** 1.5
    return x * e


def join(*parts, gap=0.05):
    silence = np.zeros(int(gap * RATE))
    out = []
    for p in parts:
        out += [p, silence]
    return np.concatenate(out[:-1])


def save(name, x):
    x = x / max(1e-6, np.abs(x).max()) * 0.8
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / (name + ".wav")), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(v * 32767)) for v in x))


def rocket(rng, seconds=1.1):
    """The rocket feet: a rushing noise that swells, roars and fades, with a low rumble and a rising whine."""
    t = np.arange(int(seconds * RATE)) / RATE
    noise = rng.normal(0, 1, len(t))
    # a moving low-pass (one pole), opening up as the thrust builds then closing as it fades
    cutoff = 400 + 3200 * np.sin(np.pi * np.clip(t / seconds, 0, 1)) ** 0.7
    a = np.exp(-2 * np.pi * cutoff / RATE)
    y, out = 0.0, np.empty_like(noise)
    for i, v in enumerate(noise):
        y = (1 - a[i]) * v + a[i] * y
        out[i] = y
    rumble = np.sin(2 * np.pi * np.cumsum(55 + 20 * t / seconds) / RATE) * 0.6
    whine = np.sin(2 * np.pi * np.cumsum(500 + 900 * t / seconds) / RATE) * 0.12
    shape = np.minimum(1, t / 0.08) * np.clip((seconds - t) / 0.5, 0, 1)
    return (out / np.abs(out).max() + rumble + whine) * shape


def servo(rng, seconds=1.0):
    """A big hydraulic actuator: a low motor rumble swelling and fading, and the hiss of fluid moving, no tone to it
    (a geared whine read as beeping on a big bot)."""
    t = np.arange(int(seconds * RATE)) / RATE
    shape = np.sin(np.pi * np.clip(t / seconds, 0, 1)) ** 0.8
    rumble = np.sin(2 * np.pi * np.cumsum(38 + 10 * shape) / RATE) + 0.5 * np.sin(2 * np.pi * np.cumsum(76 + 20 * shape) / RATE)
    rumble *= 1 + 0.3 * rng.normal(0, 1, len(t))                                # rough, not pure
    noise = rng.normal(0, 1, len(t))
    a, y, hiss = np.exp(-2 * np.pi * 900 / RATE), 0.0, np.empty_like(noise)   # a soft, dark hiss
    for i, v in enumerate(noise):
        y = (1 - a) * v + a * y
        hiss[i] = y
    hiss /= np.abs(hiss).max()
    return (0.5 * rumble + 0.6 * hiss) * shape


def clank(rng, seconds=0.6):
    """A heavy metal foot coming down: a thud, a ring of steel, a rattle."""
    t = np.arange(int(seconds * RATE)) / RATE
    thud = np.sin(2 * np.pi * 55 * t) * np.exp(-t / 0.06)
    ring = sum(np.sin(2 * np.pi * f * t) * np.exp(-t / d) * a
               for f, d, a in ((310, 0.18, 0.3), (517, 0.12, 0.2), (1130, 0.07, 0.12)))
    rattle = rng.normal(0, 1, len(t)) * np.exp(-t / 0.03) * 0.4
    return thud + ring + rattle


def glide(a, b, curve=1.0):
    return lambda t: a + (b - a) * (t / t[-1]) ** curve if len(t) else a


def main():
    rng = np.random.default_rng(4)
    contours = [(520, 780), (760, 540), (600, 900), (880, 620), (540, 560), (700, 980), (950, 700), (640, 720)]
    for i, (a, b) in enumerate(contours, 1):
        seconds = rng.uniform(0.12, 0.28)
        save("chirp_%d" % i, envelope(tone(glide(a, b, rng.uniform(0.6, 1.8)), seconds, 0.03, 30)))
    save("question", envelope(join(tone(glide(520, 560), 0.08), tone(glide(560, 1020, 1.6), 0.24, 0.02, 25), gap=0.03)))
    save("trill", envelope(join(*[tone(glide(880 + 120 * (k % 2), 980 + 140 * (k % 2)), 0.07, 0.05, 40)
                                  for k in range(6)], gap=0.015), release=0.1))
    save("ooh", envelope(tone(glide(720, 430, 0.7), 0.75, 0.06, 7, brightness=0.35), attack=0.05, release=0.2))
    save("sad", envelope(tone(glide(560, 300, 1.4), 0.5, 0.02, 5, brightness=0.3), release=0.2))
    save("alert", envelope(join(tone(lambda t: 1250 + 0 * t, 0.07, brightness=0.8),
                                tone(lambda t: 1250 + 0 * t, 0.07, brightness=0.8), gap=0.06)))
    save("rocket", rocket(rng))
    save("servo", servo(rng))
    save("clank", clank(rng))
    save("countdown", envelope(join(*[tone(lambda t: 880 + 0 * t, 0.09, brightness=0.9) for _ in range(5)], gap=0.4)))
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
