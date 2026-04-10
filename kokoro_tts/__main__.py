"""espeak-like CLI wrapper around Kokoro TTS (hexgrad/kokoro backend).

Split into two modes sharing one entry point:

* Client mode (default) — what Ren'Py / users invoke. Sends the utterance
  to a long-lived daemon over a unix socket, blocks until playback ends,
  and on SIGTERM/SIGINT tells the daemon to stop immediately. This is what
  lets Ren'Py's ``process.terminate()`` interrupt mid-sentence cleanly.

* Daemon mode (``--daemon``) — long-lived worker. Loads ``KPipeline`` once,
  serves ``SPEAK``/``STOP`` on a per-user, per-language unix socket, and
  streams synthesized float32 PCM into ``aplay``/``paplay`` as each
  sentence is produced (so audio starts on the first sentence instead of
  after the whole paragraph is synthesized).

File output (``-o``) stays on a separate in-process path and never touches
the daemon — it's a batch operation where startup latency is acceptable.
"""
from __future__ import annotations

import argparse
import atexit
import fcntl
import json
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
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

# Kokoro voice filenames start with the single-letter lang code; use that to
# route a forced voice (e.g. KOKORO_TTS_VOICE=am_adam) to the right daemon.
_PREFIX_TO_LANG = {code: lang for lang, code in LANG_CODES.items()}

LANG_DEFAULT_VOICE = {
    "en-us": "af_heart",
    "en-gb": "bf_emma",
    "es":    "ef_dora",
    "fr":    "ff_siwis",
    "hi":    "hf_alpha",
    "it":    "if_sara",
    "ja":    "jf_alpha",
    "pt-br": "pf_dora",
    "zh":    "zf_xiaobei",
}

SAMPLE_RATE = 24000


# ---------------------------------------------------------------------------
# Voice / language resolution
# ---------------------------------------------------------------------------

def _lang_from_kokoro_voice(voice: str) -> str:
    if voice and voice[0] in _PREFIX_TO_LANG:
        return _PREFIX_TO_LANG[voice[0]]
    return "en-us"


def _lang_env_voice(lang: str) -> str | None:
    key = "KOKORO_TTS_VOICE_" + lang.upper().replace("-", "_")
    return os.environ.get(key)


def resolve_voice(cli_voice: str | None, cli_lang: str | None) -> tuple[str, str]:
    """Return (lang, kokoro_voice) from CLI flags + env overrides.

    ``cli_voice`` may be:
      * None
      * an espeak-style language tag (``en``, ``en-us``, ``en+f3``, ``ja``, ...)
      * a Kokoro voice filename (``af_heart``, ``am_adam``, ...)

    ``KOKORO_TTS_VOICE`` forces a Kokoro voice regardless of ``-v``.
    ``KOKORO_TTS_VOICE_EN_US`` / ``..._JA`` / ... override per-language
    defaults when Ren'Py passes only a language tag.
    """
    forced = os.environ.get("KOKORO_TTS_VOICE")
    if forced:
        return _lang_from_kokoro_voice(forced), forced

    if cli_voice:
        base = cli_voice.split("+", 1)[0].lower()
        if base == "en":
            base = "en-us"
        if base in LANG_CODES:
            lang = base
            voice = _lang_env_voice(lang) or LANG_DEFAULT_VOICE[lang]
            return lang, voice
        # Not a known espeak tag — assume it's a Kokoro voice name.
        return _lang_from_kokoro_voice(cli_voice), cli_voice

    lang = cli_lang or "en-us"
    if lang not in LANG_CODES:
        print(f"kokoro-tts: unknown lang {lang!r}, falling back to en-us",
              file=sys.stderr)
        lang = "en-us"
    return lang, _lang_env_voice(lang) or LANG_DEFAULT_VOICE[lang]


# ---------------------------------------------------------------------------
# Daemon plumbing
# ---------------------------------------------------------------------------

def _daemon_paths(lang: str) -> tuple[Path, Path]:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    base = Path(runtime) / f"kokoro-tts-{os.getuid()}-{lang}"
    return Path(str(base) + ".sock"), Path(str(base) + ".pid")


def _spawn_stream_player() -> subprocess.Popen:
    """Start an audio player that reads raw float32 mono @ 24kHz from stdin."""
    candidates = [
        ["aplay", "-q", "-f", "FLOAT_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-"],
        ["paplay", "--raw", "--format=float32le",
         f"--rate={SAMPLE_RATE}", "--channels=1"],
    ]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    raise RuntimeError("no audio player found (need aplay or paplay)")


def _maybe_enable_hf_offline() -> None:
    """If any Kokoro snapshot is already cached, avoid HF's etag check."""
    hf_home = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
    snapshots = hf_home / "hub" / "models--hexgrad--Kokoro-82M" / "snapshots"
    if not snapshots.is_dir():
        return
    for snap in snapshots.iterdir():
        if not (snap / "config.json").exists():
            continue
        voices = snap / "voices"
        if voices.is_dir() and any(voices.iterdir()):
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            return


def _silence_upstream_warnings() -> None:
    # LSTM(dropout=0.2, num_layers=1) — no-op dropout, upstream bug.
    # torch.nn.utils.weight_norm deprecated in favor of parametrizations.weight_norm.
    import warnings
    warnings.filterwarnings("ignore", message=r"dropout option adds dropout.*")
    warnings.filterwarnings(
        "ignore", message=r"`torch\.nn\.utils\.weight_norm` is deprecated.*")


class _DaemonState:
    def __init__(self, lang: str) -> None:
        self.lang = lang
        self.pipeline = None  # lazy — first SPEAK pays the load cost
        self.utterance_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.current_player: subprocess.Popen | None = None
        self.last_activity = time.time()

    def ensure_pipeline(self) -> None:
        if self.pipeline is None:
            print(f"kokoro-tts: loading model ({self.lang})", flush=True)
            from kokoro import KPipeline  # type: ignore
            self.pipeline = KPipeline(
                lang_code=LANG_CODES[self.lang], repo_id="hexgrad/Kokoro-82M")
            print("kokoro-tts: model loaded", flush=True)

    def is_idle(self) -> bool:
        return self.current_player is None and not self.utterance_lock.locked()

    def stop_current(self) -> None:
        self.stop_event.set()
        p = self.current_player
        if p is not None:
            try:
                p.kill()
            except ProcessLookupError:
                pass


def _do_speak(state: _DaemonState, conn: socket.socket, payload: dict) -> None:
    import numpy as np  # type: ignore

    text = str(payload.get("text", ""))
    voice = payload.get("voice") or LANG_DEFAULT_VOICE.get(state.lang, "af_heart")
    amp = float(payload.get("amp", 100.0))
    speed = float(payload.get("speed", 1.0))
    gain = max(0.0, min(1.0, amp / 100.0))

    state.ensure_pipeline()
    state.stop_event.clear()
    player = _spawn_stream_player()
    state.current_player = player

    try:
        conn.sendall(b"OK\n")
    except OSError:
        pass

    def _watch_stop() -> None:
        try:
            while not state.stop_event.is_set():
                try:
                    data = conn.recv(64)
                except OSError:
                    return
                if not data:
                    print("kokoro-tts: client disconnect", flush=True)
                    state.stop_current()
                    return
                if b"STOP" in data:
                    print("kokoro-tts: stop", flush=True)
                    state.stop_current()
                    return
        except Exception:
            pass
    watcher = threading.Thread(target=_watch_stop, daemon=True)
    watcher.start()

    stopped = False
    try:
        for _, _, audio in state.pipeline(text, voice=voice, speed=speed):
            if state.stop_event.is_set():
                stopped = True
                break
            arr = np.asarray(audio, dtype=np.float32)
            if gain != 1.0:
                arr = arr * gain
            try:
                player.stdin.write(arr.tobytes())
            except BrokenPipeError:
                break
    finally:
        if stopped:
            try:
                player.kill()
            except ProcessLookupError:
                pass
        else:
            try:
                if player.stdin:
                    player.stdin.close()
            except OSError:
                pass
            while player.poll() is None:
                if state.stop_event.wait(0.05):
                    stopped = True
                    break
            if stopped:
                try:
                    player.kill()
                except ProcessLookupError:
                    pass
        state.current_player = None
        state.last_activity = time.time()

    try:
        conn.sendall(b"STOPPED\n" if stopped else b"DONE\n")
    except OSError:
        pass


def _handle_client(conn: socket.socket, state: _DaemonState) -> None:
    try:
        state.last_activity = time.time()
        f = conn.makefile("rb")
        line = f.readline()
        if not line:
            return
        cmd, _, rest = line.partition(b" ")
        cmd = cmd.strip()
        if cmd == b"SPEAK":
            try:
                payload = json.loads(rest.decode())
            except json.JSONDecodeError as e:
                try:
                    conn.sendall(f"ERR bad payload: {e}\n".encode())
                except OSError:
                    pass
                return
            text = payload.get("text", "") if isinstance(payload, dict) else ""
            print(f"kokoro-tts: speak {text!r}", flush=True)
            with state.utterance_lock:
                _do_speak(state, conn, payload)
        elif cmd == b"STOP":
            print("kokoro-tts: stop", flush=True)
            state.stop_current()
            try:
                conn.sendall(b"STOPPED\n")
            except OSError:
                pass
        else:
            try:
                conn.sendall(b"ERR unknown command\n")
            except OSError:
                pass
    except Exception as e:  # noqa: BLE001 — last-resort, keep daemon alive
        try:
            conn.sendall(f"ERR {e}\n".encode())
        except OSError:
            pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def daemon_main(lang: str) -> int:
    if lang not in LANG_CODES:
        print(f"kokoro-tts: --daemon requires a known --lang (got {lang!r})",
              file=sys.stderr)
        return 2

    sock_path, pid_path = _daemon_paths(lang)
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    # Single-instance lock. Another live daemon for this (user, lang) → exit.
    pid_fp = open(pid_path, "a+")
    try:
        fcntl.flock(pid_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pid_fp.close()
        return 0
    pid_fp.seek(0)
    pid_fp.truncate()
    pid_fp.write(str(os.getpid()))
    pid_fp.flush()

    # Remove stale socket from a crashed predecessor.
    try:
        sock_path.unlink()
    except FileNotFoundError:
        pass

    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(sock_path))
    srv.listen(8)
    os.chmod(sock_path, 0o600)

    def _cleanup() -> None:
        try:
            sock_path.unlink()
        except OSError:
            pass
        try:
            pid_path.unlink()
        except OSError:
            pass
    atexit.register(_cleanup)

    def _on_term(*_: object) -> None:
        sys.exit(0)
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    _maybe_enable_hf_offline()
    _silence_upstream_warnings()

    state = _DaemonState(lang)
    idle_secs = int(os.environ.get("KOKORO_TTS_IDLE_SECS", "600"))

    def _idle_watch() -> None:
        while True:
            time.sleep(10)
            if state.is_idle() and time.time() - state.last_activity > idle_secs:
                os._exit(0)
    threading.Thread(target=_idle_watch, daemon=True).start()

    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        threading.Thread(
            target=_handle_client, args=(conn, state), daemon=True).start()
    return 0


# ---------------------------------------------------------------------------
# Client (what Ren'Py actually invokes)
# ---------------------------------------------------------------------------

def _spawn_daemon_detached(lang: str) -> None:
    subprocess.Popen(
        [sys.executable, "-m", "kokoro_tts", "--daemon", "--lang", lang],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _connect_with_spawn(lang: str, timeout: float = 10.0) -> socket.socket:
    sock_path, _ = _daemon_paths(lang)
    deadline = time.time() + timeout
    spawned = False
    while time.time() < deadline:
        s = socket.socket(socket.AF_UNIX)
        try:
            s.connect(str(sock_path))
            return s
        except (FileNotFoundError, ConnectionRefusedError):
            s.close()
            if not spawned:
                _spawn_daemon_detached(lang)
                spawned = True
            time.sleep(0.1)
    raise RuntimeError("kokoro-tts daemon did not come up in time")


def client_speak(text: str, lang: str, voice: str, speed: float, amp: float) -> int:
    s = _connect_with_spawn(lang)

    def _on_signal(_sig: int, _frm: object) -> None:
        print(f"kokoro-tts: client got signal {_sig}, sending STOP", file=sys.stderr, flush=True)
        try:
            s.sendall(b"STOP\n")
        except OSError:
            pass
        try:
            s.close()
        except OSError:
            pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    payload = json.dumps(
        {"text": text, "voice": voice, "amp": amp, "speed": speed})
    s.sendall(b"SPEAK " + payload.encode() + b"\n")

    f = s.makefile("rb")
    rc = 0
    while True:
        line = f.readline()
        if not line:
            break
        if line.startswith(b"DONE") or line.startswith(b"STOPPED"):
            break
        if line.startswith(b"ERR"):
            sys.stderr.write(line.decode())
            rc = 1
            break
        # OK is informational; keep reading for DONE/STOPPED.
    try:
        s.close()
    except OSError:
        pass
    return rc


# ---------------------------------------------------------------------------
# Batch (-o) — in-process, bypasses daemon
# ---------------------------------------------------------------------------

def batch_to_file(text: str, lang: str, voice: str, speed: float, out_path: str) -> int:
    _maybe_enable_hf_offline()
    _silence_upstream_warnings()
    from kokoro import KPipeline  # type: ignore
    import numpy as np  # type: ignore
    import soundfile as sf  # type: ignore

    pipeline = KPipeline(lang_code=LANG_CODES[lang], repo_id="hexgrad/Kokoro-82M")
    chunks = [audio for _, _, audio in pipeline(text, voice=voice, speed=speed)]
    if not chunks:
        print("kokoro-tts: no audio produced", file=sys.stderr)
        return 1
    samples = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    sf.write(out_path, samples, SAMPLE_RATE)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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
    p.add_argument("text", nargs="*",
                   help='Text to speak. Use "-" or pipe stdin to read from stdin.')
    p.add_argument("-f", "--file", help="Read text from FILE instead of args.")
    p.add_argument("-v", "--voice", default=None,
                   help="Voice: either an espeak language tag (en, en-gb, ja, ...) "
                        "or a Kokoro voice name (af_heart, am_adam, ...). "
                        "KOKORO_TTS_VOICE env var overrides.")
    p.add_argument("-s", "--speed", type=float, default=1.0,
                   help="Speech speed multiplier (default: 1.0).")
    p.add_argument("-l", "--lang", default=None, choices=sorted(LANG_CODES),
                   help="Language tag (default: en-us, or inferred from -v).")
    p.add_argument("-o", "--output",
                   help="Write WAV to PATH instead of playing (bypasses daemon).")
    p.add_argument("-a", "--amplitude", type=float, default=100.0,
                   help="Volume 0-100 (Ren'Py passes this; default: 100).")
    p.add_argument("--daemon", action="store_true",
                   help="Run as the persistent synthesis daemon.")
    args = p.parse_args(argv)

    if args.daemon:
        return daemon_main(args.lang or "en-us")

    lang, voice = resolve_voice(args.voice, args.lang)

    if args.output:
        text = read_text(args)
        if not text.strip():
            p.error("no text provided (pass as args, via -f FILE, or on stdin)")
        return batch_to_file(text, lang, voice, args.speed, args.output)

    text = read_text(args)
    if not text.strip():
        p.error("no text provided (pass as args, via -f FILE, or on stdin)")

    return client_speak(text, lang, voice, args.speed, args.amplitude)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
