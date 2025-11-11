{
  description = "Dev shell for scope-spider (Python + OpenAI SDK)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = {
    self,
    nixpkgs,
  }: let
    systems = ["x86_64-linux" "aarch64-linux"];
    forAllSystems = f:
      nixpkgs.lib.genAttrs systems (system: f (import nixpkgs {inherit system;}));
  in {
    devShells = forAllSystems (pkgs: {
      default = pkgs.mkShell {
        packages = with pkgs; [python312 virtualenv];
        shellHook = ''
          if [ ! -d ".venv" ]; then
            virtualenv -p python .venv
          fi
          . .venv/bin/activate
          python -m pip install --no-input --upgrade pip >/dev/null 2>&1
          python -m pip install --no-input "openai==2.7.1" openpyxl requests tqdm PyPDF2 rapidfuzz camelot-py[cv] tiktoken pycryptodome >/dev/null 2>&1
        '';
      };
    });
  };
}
