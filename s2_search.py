import sys, os, json, time
from pathlib import Path
from openai import OpenAI
from s0_models import Report, create_response


def search(client, company, ticker):
    response = create_response(client, company, ticker)

    print(
        json.dumps(getattr(response, "model_dump", lambda: {})(), ensure_ascii=False),
        flush=True,
    )

    # Parse the response - OpenAI follows the schema exactly
    result = Report.model_validate_json(response.output[0].content[0].text)
    return result.model_dump()


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
        if report and (report.get("url") or "").strip():
            print(
                f"SKIPPING: {name} ({ticker}) already has a URL: {report['url']}",
                flush=True,
            )
            continue

        # Querying
        print(f"QUERY: {name} ({ticker})", flush=True)
        data = search(openAI, name, ticker)
        url = (data.get("url") or "").strip() if isinstance(data, dict) else ""
        if url:
            item["report"] = data
            path.write_text(json.dumps(items, ensure_ascii=False))
        time.sleep(1.0)


if __name__ == "__main__":
    main()
