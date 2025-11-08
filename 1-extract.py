#!/usr/bin/env python3
"""Output company list (company_name,ticker) from XLSX as JSON to stdout."""

from __future__ import annotations
import sys, json
from pathlib import Path
from openpyxl import load_workbook

HEADER_ROW_INDEX = 3
TICKER_COL_INDEX = 0
COMPANY_COL_INDEX = 1

def main() -> None:
    xlsx = Path(sys.argv[1])
    sheet = sys.argv[2] if len(sys.argv) > 2 else None
    wb = load_workbook(filename=xlsx, data_only=True, read_only=True)
    ws = wb[sheet] if sheet else wb.active
    out = []
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i <= HEADER_ROW_INDEX:
            continue
        t = r[TICKER_COL_INDEX]
        c = r[COMPANY_COL_INDEX]
        if t and c:
            out.append({"company_name": str(c).strip(), "ticker": str(t).strip()})
    wb.close()
    print(json.dumps(out, ensure_ascii=False))

if __name__ == "__main__":
    main()
