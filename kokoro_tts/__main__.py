"""espeak-like CLI wrapper around Kokoro TTS (hexgrad/kokoro backend)."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LANG_CODES = {
    "en-us": "a",
    "en-gb": "b",
    "es":    "e",
    "fr":    "f",
    "hi":    "h",
    "it":    "i",
    "ja":    "j",
    "pt-br": "p",
    "zh":    "z",
}


def play(wav_path: Path) -> int:
    for player in (["aplay", "-q", str(wav_path)], ["paplay", str(wav_path)]):
        if shutil.which(player[0]):
            return subprocess.run(player).returncode
    print("kokoro-tts: no audio player found (need aplay or paplay)", file=sys.stderr)
    return 1


def read_text(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).read_text()
    if args.text == ["-"] or (not args.text and not sys.stdin.isatty()):
        return sys.stdin.read()
    if args.text:
        return " ".join(args.text)
    return ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="kokoro-tts",
        description="Speak text with Kokoro TTS (espeak-style CLI).",
    )
    p.add_argument("text", nargs="*", help='Text to speak. Use "-" or pipe stdin to read from stdin.')
    p.add_argument("-f", "--file", help="Read text from FILE instead of args.")
    p.add_argument("-v", "--voice", default="af_heart", help="Voice name (default: af_heart).")
    p.add_argument("-s", "--speed", type=float, default=1.0, help="Speech speed multiplier (default: 1.0).")
    p.add_argument("-l", "--lang", default="en-us", choices=sorted(LANG_CODES),
                   help="Language tag (default: en-us).")
    p.add_argument("-o", "--output", help="Write WAV to PATH instead of playing.")
    args = p.parse_args(argv)

    # Import lazily so --help is fast.
    from kokoro import KPipeline  # type: ignore
    import numpy as np  # type: ignore
    import soundfile as sf  # type: ignore

    text = read_text(args)
    if not text.strip():
        p.error("no text provided (pass as args, via -f FILE, or on stdin)")

    pipeline = KPipeline(lang_code=LANG_CODES[args.lang])
    chunks = [audio for _, _, audio in pipeline(text, voice=args.voice, speed=args.speed)]
    if not chunks:
        print("kokoro-tts: no audio produced", file=sys.stderr)
        return 1
    samples = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    sr = 24000

    if args.output:
        sf.write(args.output, samples, sr)
        return 0

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        sf.write(tmp_path, samples, sr)
        return play(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
