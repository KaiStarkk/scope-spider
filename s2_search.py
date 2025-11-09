import sys, json, time
from pathlib import Path
from openai import OpenAI
from s0_models import search_and_parse


def main():
    # Setup
    path = Path(sys.argv[1])
    items = json.loads(path.read_text() or "[]")
    openAI = OpenAI()

    # Process
    for item in items:
        # Checking
        name = item["company_name"].strip()
        ticker = item["ticker"].strip()
        report = item.get("report")
        if report:
            print(
                f"SKIPPING: {name} ({ticker}) already has a report: {report.title}",
                flush=True,
            )
            continue

        # Querying
        print(f"QUERY: {name} ({ticker})", flush=True)
        response = search_and_parse(openAI, name, ticker)
        if parsed := response.output_parsed:
            item["report"] = parsed.model_dump()
            path.write_text(json.dumps(items, ensure_ascii=False))
        else:
            print(f"ERROR: Failed to parse response: {response.model_dump()}", flush=True)
        time.sleep(1.0)


if __name__ == "__main__":
    main()
