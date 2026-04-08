{
  description = "kokoro-tts: espeak-like CLI wrapper around Kokoro TTS";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    (flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python3;

        pyEnv = python.withPackages (ps: [
          ps.kokoro
          ps.soundfile
          ps.numpy
          ps.spacy-models.en_core_web_sm
        ]);

        kokoro-tts = pkgs.stdenv.mkDerivation {
          pname = "kokoro-tts";
          version = "0.1.0";
          src = ./.;
          nativeBuildInputs = [ pkgs.makeWrapper ];
          buildInputs = [ pyEnv ];
          installPhase = ''
            runHook preInstall
            mkdir -p $out/share/kokoro-tts $out/bin
            cp -r kokoro_tts $out/share/kokoro-tts/
            makeWrapper ${pyEnv}/bin/python $out/bin/kokoro-tts \
              --add-flags "-m kokoro_tts" \
              --prefix PYTHONPATH : "$out/share/kokoro-tts" \
              --prefix PATH : "${pkgs.lib.makeBinPath [ pkgs.alsa-utils pkgs.pulseaudio ]}"
            runHook postInstall
          '';
        };
      in {
        packages.default = kokoro-tts;
        packages.kokoro-tts = kokoro-tts;

        apps.default = {
          type = "app";
          program = "${kokoro-tts}/bin/kokoro-tts";
        };

        devShells.default = pkgs.mkShell {
          packages = [ pyEnv pkgs.alsa-utils ];
        };
      })) // {
        nixosModules.default = import ./nix/module.nix self;
      };
}
