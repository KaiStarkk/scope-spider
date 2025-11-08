#!/usr/bin/env python3
from __future__ import annotations
import sys, os, json, time
from pathlib import Path
from openai import OpenAI
from pydantic import BaseModel

def q(company: str, ticker: str) -> str:
    return (
        f"Find the most recent OFFICIAL report that includes Scope 1, Scope 2, and Scope 3 emissions for {company} ({ticker}). "
        f"Prefer Climate/Sustainability/TCFD reports; otherwise use the Annual Report. "
        f"Prioritize the company's official domains (investor relations, sustainability) and direct PDF links. "
        f"Return only: url, title, filetype, filename. url must be an https direct .pdf link. "
        f"If no suitable document is found, return empty strings for all fields."
    )

class Report(BaseModel):
    url: str
    title: str
    filetype: str
    filename: str

def search(client: OpenAI, model: str, company: str, ticker: str) -> dict:
    r = client.responses.parse(
        model=model,
        reasoning={"effort": "low"},
        tools=[{"type": "web_search", "user_location": {"type": "approximate", "country": "AU"}}],
        include=["web_search_call.action.sources"],
        max_output_tokens=1024,
        # temperature=0,
        input=q(company, ticker),
        text_format=Report,
    )
    print(json.dumps(getattr(r, "model_dump", lambda: {})(), ensure_ascii=False), flush=True)
    parsed = getattr(r, "output_parsed", None)
    return parsed.model_dump() if parsed else {}

def main() -> None:
    path = Path(sys.argv[1])
    items = json.loads(path.read_text() or "[]")
    client = OpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    for i, it in enumerate(items):
        c = (it.get("company_name") or "").strip()
        t = (it.get("ticker") or "").strip()
        if not c or not t or (it.get("url") or "").strip():
            continue
        print(f"QUERY: {c} ({t})", flush=True)
        d = search(client, model, c, t)
        url = (d.get("url") or "").strip() if isinstance(d, dict) else ""
        if url:
            items[i] = {**it, **d}
            path.write_text(json.dumps(items, ensure_ascii=False))
        time.sleep(1.0)

if __name__ == "__main__":
    main()
