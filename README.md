# kokoro-tts

Tiny espeak-style CLI around [Kokoro TTS](https://github.com/hexgrad/kokoro). Pass text, hear speech.

## Usage

```sh
kokoro-tts "Hello from Kokoro"
kokoro-tts -f speech.txt --voice af_bella --speed 1.1
kokoro-tts --lang en-gb "save this" --output out.wav
echo "piped input" | kokoro-tts -
```

Flags: `-v/--voice` (espeak language tag like `en-gb` or a Kokoro voice name like `af_heart`), `-s/--speed` (default `1.0`), `-l/--lang` (choices: `en-us`, `en-gb`, `es`, `fr`, `hi`, `it`, `ja`, `pt-br`, `zh`), `-a/--amplitude` (0-100, Ren'Py uses this), `-o/--output PATH`, `-f/--file PATH`.

On first run the Kokoro-82M model is downloaded via `huggingface_hub` into `~/.cache/huggingface/`. Playback uses `aplay` (alsa-utils) with a `paplay` (PulseAudio) fallback.

## Ren'Py self-voicing

```sh
export RENPY_TTS_COMMAND=kokoro-tts
./renpy.sh your-game   # then press `v` in-game to toggle self-voicing
```

Under the hood, `kokoro-tts` runs as a warm daemon: the first invocation loads the Kokoro model and listens on `$XDG_RUNTIME_DIR/kokoro-tts-$UID-<lang>.sock`; subsequent invocations are thin clients that stream synthesized audio through the daemon. When Ren'Py advances dialogue it sends `SIGTERM` to the client, which tells the daemon to kill playback immediately.

Ren'Py's `config.tts_voice` selects the *language* (it passes espeak names like `en`, `en-gb`, `ja`). To pick which Kokoro voice is used within a language, set env vars:

```sh
export KOKORO_TTS_VOICE=am_adam          # force this voice everywhere
export KOKORO_TTS_VOICE_EN_US=af_bella   # per-language override
export KOKORO_TTS_VOICE_JA=jf_alpha
```

The daemon exits after `KOKORO_TTS_IDLE_SECS` seconds of inactivity (default 600).

## NixOS

```sh
nix run github:rafal11ck/kokoro-tts -- "hello world"
nix run . -- "hello world"
nix build .
```

### As a flake input

```nix
{
  inputs.kokoro-tts.url = "github:rafal11ck/kokoro-tts";
  inputs.kokoro-tts.inputs.nixpkgs.follows = "nixpkgs";

  outputs = { nixpkgs, kokoro-tts, ... }: {
    nixosConfigurations.<host> = nixpkgs.lib.nixosSystem {
      modules = [
        kokoro-tts.nixosModules.default
        { programs.kokoro-tts.enable = true; }
        ./configuration.nix
      ];
    };
  };
}
```

Module options:

- `programs.kokoro-tts.enable` — install the CLI into `environment.systemPackages`.
- `programs.kokoro-tts.renpyTts` — set `RENPY_TTS_COMMAND` so Ren'Py's self-voicing routes through kokoro-tts. Defaults to the value of `enable`.

After rebuilding, log out and back in for the session variable to take effect.

## Dev shell

```sh
nix develop
python -m kokoro_tts "testing"
```
