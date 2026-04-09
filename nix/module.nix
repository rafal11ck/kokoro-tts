self: { config, lib, pkgs, ... }:
let
  cfg = config.programs.kokoro-tts;
  pkg = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
in {
  options.programs.kokoro-tts = {
    enable = lib.mkEnableOption "kokoro-tts CLI";
    renpyTts = lib.mkOption {
      type = lib.types.bool;
      default = cfg.enable;
      defaultText = lib.literalExpression "config.programs.kokoro-tts.enable";
      description = "Set RENPY_TTS_COMMAND to route Ren'Py self-voicing through kokoro-tts.";
    };
  };

  config = lib.mkMerge [
    (lib.mkIf cfg.enable {
      environment.systemPackages = [ pkg ];
    })
    (lib.mkIf cfg.renpyTts {
      environment.sessionVariables.RENPY_TTS_COMMAND = "${pkg}/bin/kokoro-tts";
    })
  ];
}
