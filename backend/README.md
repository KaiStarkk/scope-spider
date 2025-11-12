# Scope Spider Backend

## Running the API locally

1. Activate the project dev-shell (recommended):
   ```bash
   nix develop
   ```
   or activate your existing virtualenv.

2. Install Python dependencies (if not already installed):
   ```bash
   pip install -r backend/requirements.txt
   ```

3. Start the FastAPI server (defaults to port `8050`):
   ```bash
   uvicorn backend.app.main:app --reload --port 8050
   ```

   The `backend` package ensures the project root is on `PYTHONPATH`, so the command works both with and without `--app-dir backend`. Set `BACKEND_PORT` to override the default port when running `python -m backend.app.main`.

The API will be available at http://127.0.0.1:8050 and serves routes under `/api/...`.
