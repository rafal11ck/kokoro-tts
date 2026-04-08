# kokoro-tts

Tiny espeak-style CLI around [Kokoro TTS](https://github.com/thewh1teagle/kokoro-onnx). Pass text, hear speech.

## Usage

```sh
kokoro-tts "Hello from Kokoro"
kokoro-tts -f speech.txt --voice af_bella --speed 1.1
kokoro-tts "save this" --output out.wav
echo "piped input" | kokoro-tts -
kokoro-tts --list-voices
```

Flags: `-v/--voice` (default `af_heart`), `-s/--speed` (default `1.0`), `-l/--lang` (default `en-us`), `-o/--output PATH`, `-f/--file PATH`, `--list-voices`.

On first run the model (`kokoro-v1.0.onnx`, ~310 MB) and voices (`voices-v1.0.bin`) are downloaded into `$XDG_CACHE_HOME/kokoro-tts/`. Override with `KOKORO_MODEL` / `KOKORO_VOICES` env vars.

Playback uses `aplay` (alsa-utils) with a `paplay` (PulseAudio) fallback.

## NixOS

```sh
nix run github:you/kokoro-tts -- "hello world"
# or from a local checkout:
nix run . -- "hello world"
nix build .
nix profile install .
```

The flake builds `kokoro-onnx` from PyPI and patches the bundled `libespeak-ng` with `autoPatchelfHook`, so it runs on NixOS without `nix-ld`.

### First build: fix the hashes

`flake.nix` uses `lib.fakeHash` for the three PyPI fetches (`kokoro_onnx`, `espeakng_loader`, `language_tags`). On first `nix build`, Nix will print the real `sha256` for each — paste them back in and rebuild. This is standard Nix practice for pinning.

## Dev shell

```sh
nix develop
python -m kokoro_tts "testing"
```
