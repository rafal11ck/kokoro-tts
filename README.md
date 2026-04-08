# kokoro-tts

Tiny espeak-style CLI around [Kokoro TTS](https://github.com/hexgrad/kokoro). Pass text, hear speech.

## Usage

```sh
kokoro-tts "Hello from Kokoro"
kokoro-tts -f speech.txt --voice af_bella --speed 1.1
kokoro-tts --lang en-gb "save this" --output out.wav
echo "piped input" | kokoro-tts -
```

Flags: `-v/--voice` (default `af_heart`), `-s/--speed` (default `1.0`), `-l/--lang` (default `en-us`; choices: `en-us`, `en-gb`, `es`, `fr`, `hi`, `it`, `ja`, `pt-br`, `zh`), `-o/--output PATH`, `-f/--file PATH`.

On first run the Kokoro-82M model is downloaded via `huggingface_hub` into `~/.cache/huggingface/`. Playback uses `aplay` (alsa-utils) with a `paplay` (PulseAudio) fallback.

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
