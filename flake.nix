{
  description = "jev-proxy - micro-servidor proxy/roteador inteligente para LLMs via OpenRouter";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          python312
          python312Packages.fastapi
          python312Packages.uvicorn
          python312Packages.httpx
          python312Packages.python-dotenv
        ];

        shellHook = ''
          echo "[jev-proxy] Ambiente ativado. Python: $(python3 --version)"
          echo "[jev-proxy] Inicie o servidor com: python3 server.py"
        '';
      };
    };
}
