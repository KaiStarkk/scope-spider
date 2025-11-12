from __future__ import annotations

import logging

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import dashboard, verification

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Scope Spider API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router)
app.include_router(verification.router)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("BACKEND_PORT", "8050"))
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=port, reload=True)
