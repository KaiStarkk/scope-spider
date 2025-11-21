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
        packages = with pkgs; [python312 virtualenv nodejs_20];
        buildInputs = with pkgs; [
          stdenv.cc.cc.lib
        ];
        shellHook = ''
          export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH"
          if [ ! -d ".venv" ]; then
            virtualenv -p python .venv
          fi
          . .venv/bin/activate
          python -m pip install --no-input --upgrade pip >/dev/null 2>&1
          if [ -f backend/requirements.txt ]; then
            python -m pip install --no-input -r backend/requirements.txt >/dev/null 2>&1
          fi
          python -m pip install --no-input "openai==2.7.1" openpyxl pandas pandas-stubs plotly dash requests tqdm PyPDF2 rapidfuzz camelot-py[cv] tiktoken pycryptodome llama-cpp-python pdf2image pillow >/dev/null 2>&1
          ln -sf ${pkgs.nodejs_20}/bin/node .venv/bin/node
          ln -sf ${pkgs.nodejs_20}/bin/npm .venv/bin/npm
          ln -sf ${pkgs.nodejs_20}/bin/npx .venv/bin/npx
        '';
      };
    });
  };
}
