import sys, json, time, os
from pathlib import Path
from openai import OpenAI
from s0_models import query


def main():
    # Setup
    mode = (
        sys.argv[2]
        if len(sys.argv) > 2 and sys.argv[2] in ("auto", "review")
        else "review"
    )
    debug = ("--debug" in sys.argv[2:]) or ("-d" in sys.argv[2:])
    if mode not in ("auto", "review"):
        sys.exit("Usage: s2_search.py <json_file> [auto|review]")

    path = Path(sys.argv[1])
    items = json.loads(path.read_text() or "[]")
    openAI = OpenAI()
    auto_mode = mode == "auto"

    # Process
    for item in items:
        info = item.get("info") or {}
        name = (info.get("name") or "").strip()
        ticker = (info.get("ticker") or "").strip()
        report = item.get("report") or {}
        data = report.get("data") or {}
        s1 = data.get("scope_1")
        s2 = data.get("scope_2")
        has_good_data = (
            isinstance(s1, (int, float))
            and isinstance(s2, (int, float))
            and (s1 or 0) > 0
            and (s2 or 0) > 0
        )
        has_file = bool((report.get("file") or {}).get("url"))
        if has_file or has_good_data:
            if debug:
                print(
                    f"SKIPPING: {name} ({ticker}) already has file={has_file} data_ok={has_good_data}",
                    flush=True,
                )
            continue

        print(f"QUERY: {name} ({ticker})", flush=True)
        # Propagate debug to the search layer
        if debug:
            os.environ["S0_DEBUG"] = "1"
        response, parsed = query(openAI, name, ticker)
        if parsed:
            if not auto_mode:
                report_data = parsed.model_dump()
                print(
                    f"\n{name} ({ticker}) - Full Report Data:\n{json.dumps(report_data, ensure_ascii=False, indent=2)}\n",
                    flush=True,
                )
                action = input("approve/skip/continue/quit [A/s/c/q]: ").strip().lower()
                if action in ("skip", "s"):
                    continue
                elif action in ("quit", "q"):
                    return
                elif action in ("continue", "c"):
                    auto_mode = True
                    print("Switching to automatic mode...", flush=True)
            # Map parsed Report into nested structure
            existing_data = (item.get("report") or {}).get("data") or {}
            # Build search metadata
            src = "fallback"
            selected = parsed.url
            query_text = None
            candidates = None
            try:
                if isinstance(response, dict):
                    src = response.get("source") or src
                diag = parsed.diagnostics
                if hasattr(diag, "model_dump"):
                    diag = diag.model_dump()
                if not isinstance(diag, dict):
                    diag = {}
                web = diag.get("web_search") or {}
                if not isinstance(web, dict):
                    try:
                        web = dict(web)
                    except Exception:
                        web = {}
                if isinstance(web, dict):
                    query_text = web.get("search_query")
                    raw_candidates = web.get("candidates") or []
                    # Normalize candidate entries to plain dicts
                    norm_candidates = []
                    for c in raw_candidates:
                        if isinstance(c, dict):
                            norm_candidates.append(c)
                        else:
                            norm_candidates.append(
                                {
                                    "url": getattr(c, "url", None),
                                    "title": getattr(c, "title", None),
                                    "why": getattr(c, "why", None),
                                    "snippet": getattr(c, "snippet", None),
                                }
                            )
                    candidates = norm_candidates or None
            except Exception:
                pass
            item["report"] = {
                "file": {
                    "url": parsed.url,
                    "filetype": parsed.filetype,
                    "filename": parsed.filename,
                    "year": parsed.year,
                },
                "search": {
                    "query": query_text,
                    "source": src,
                    "candidates": candidates,
                    "selected": selected,
                },
                "data": {
                    "scope_1": existing_data.get("scope_1"),
                    "scope_2": existing_data.get("scope_2"),
                },
            }
            path.write_text(json.dumps(items, ensure_ascii=False, indent=2))
        else:
            print(
                f"ERROR: Failed to parse response: {response.model_dump()}",
                flush=True,
            )

        if auto_mode:
            time.sleep(5.0)


if __name__ == "__main__":
    main()
