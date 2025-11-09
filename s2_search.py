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

    # Process
    for item in items:
        name = item["company_name"].strip()
        ticker = item["ticker"].strip()
        report = item.get("report")
        if report:
            print(
                f"SKIPPING: {name} ({ticker}) already has a report: {report.title}",
                flush=True,
            )
            continue

        print(f"QUERY: {name} ({ticker})", flush=True)
        response, parsed = query(openAI, name, ticker)
        if parsed:
            if mode == "review":
                action = input("approve/skip: ").strip().lower()
                if action in ("skip", "s"):
                    continue
            item["report"] = parsed.model_dump()
            path.write_text(json.dumps(items, ensure_ascii=False))
        else:
            print(
                f"ERROR: Failed to parse response: {response.model_dump()}",
                flush=True,
            )

        if mode == "auto":
            time.sleep(1.0)


if __name__ == "__main__":
    main()
