import sys, json, time
from pathlib import Path
from openai import OpenAI
from s0_models import query


def main():
    # Setup
    mode = sys.argv[2] if len(sys.argv) > 2 else "review"
    if mode not in ("auto", "review"):
        sys.exit("Usage: s2_search.py <json_file> [auto|review]")

    path = Path(sys.argv[1])
    items = json.loads(path.read_text() or "[]")
    openAI = OpenAI()
    auto_mode = mode == "auto"

    # Process
    for item in items:
        name = item.get("name", "").strip()
        ticker = item.get("ticker", "").strip()
        report = item.get("report")
        if report:
            print(
                f"SKIPPING: {name} ({ticker}) already has a report: {report['title']} and data: {report['data']}",
                flush=True,
            )
            continue

        print(f"QUERY: {name} ({ticker})", flush=True)
        response, parsed = query(openAI, name, ticker)
        if parsed:
            if not auto_mode:
                report_data = parsed.model_dump()
                print(
                    f"\n{name} ({ticker}) - Full Report Data:\n{json.dumps(report_data, ensure_ascii=False, indent=2)}\n",
                    flush=True,
                )
                action = input("approve/skip/continue: ").strip().lower()
                if action in ("skip", "s"):
                    continue
                elif action in ("continue", "c"):
                    auto_mode = True
                    print("Switching to automatic mode...", flush=True)
            item["report"] = parsed.model_dump()
            path.write_text(json.dumps(items, ensure_ascii=False, indent=2))
        else:
            print(
                f"ERROR: Failed to parse response: {response.model_dump()}",
                flush=True,
            )

        if auto_mode:
            time.sleep(1.0)


if __name__ == "__main__":
    main()
