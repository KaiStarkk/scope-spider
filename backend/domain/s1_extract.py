import sys
from pathlib import Path

from pydantic import ValidationError

from backend.domain.utils.companies import dump_companies, load_companies
from backend.domain.utils.excel import extract_companies_from_workbook

HEADER_ROW_INDEX = 3
TICKER_COL_INDEX = 0
COMPANY_COL_INDEX = 1


def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: s1_extract.py <xlsx_file> <output_companies.json>")

    xlsx = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if output_path.exists():
        try:
            load_companies(output_path)
        except (ValidationError, ValueError) as exc:
            sys.exit(f"Schema validation failed: {exc}")
        print(f"{output_path} already conforms to the schema.")
        return

    try:
        companies = extract_companies_from_workbook(
            xlsx,
            header_row_index=HEADER_ROW_INDEX,
            ticker_col_index=TICKER_COL_INDEX,
            company_col_index=COMPANY_COL_INDEX,
        )
    except ValueError as exc:
        sys.exit(str(exc))

    dump_companies(output_path, {"companies": []}, companies)
    print(f"Wrote {len(companies)} companies to {output_path}")


if __name__ == "__main__":
    main()
