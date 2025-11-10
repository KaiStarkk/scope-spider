from textwrap import dedent
from typing import Literal, Optional, List
from pydantic import BaseModel, Field
import os
import re
import time
import requests
from urllib.parse import quote_plus, urlparse, parse_qs, unquote


class Data(BaseModel):
    scope_1: int = Field(
        description="Total greenhouse gas emissions for Scope 1, in kgCO2e (rounded to nearest whole number)."
    )
    scope_2: int = Field(
        description="Total greenhouse gas emissions for Scope 2, in kgCO2e (rounded to nearest whole number)."
    )
    scope_3: Optional[int] = Field(
        default=None,
        description="Total greenhouse gas emissions for Scope 3, in kgCO2e (rounded to nearest whole number).",
    )
    qualifiers: Optional[str] = Field(
        default=None,
        description="Caveats or qualifying information about the data, e.g. 'scope_2 uses market method, not location method.'",
    )


class Report(BaseModel):
    url: str = Field(description="A direct link to the report file.")
    title: str = Field(description="The official title of the sustainability report.")
    filetype: Literal["pdf", "csv", "xlsx", "txt", "html", "htm"] = Field(
        description="The file type/format of the report, e.g. pdf, csv, xlsx, txt, html, htm."
    )
    filename: str = Field(
        description="The file name of the report.",
        pattern=r"^.*\.(pdf|csv|xlsx|txt|html|htm)$",
    )
    year: str = Field(
        description="The year covered or published by the report, formatted as YYYY.",
        pattern=r"^\d{4}$",
    )
    data: Optional[Data] = Field(
        default=None,
        description="Optional: emissions data and qualifiers. Filled by later stages.",
    )
    diagnostics: Optional["Diagnostics"] = Field(
        default=None, description="Pipeline diagnostics."
    )
    download: Optional["DownloadMeta"] = Field(
        default=None, description="Download metadata."
    )
    extraction: Optional["ExtractionMeta"] = Field(
        default=None, description="Extraction metadata."
    )


class Candidate(BaseModel):
    url: str
    title: Optional[str] = None
    why: Optional[str] = None
    snippet: Optional[str] = None


class WebSearchDiagnostics(BaseModel):
    search_query: Optional[str] = None
    candidates: Optional[List[Candidate]] = None


class Diagnostics(BaseModel):
    web_search: Optional[WebSearchDiagnostics] = None


class MinimalReportForParse(BaseModel):
    """Minimal subset used when invoking the LLM fallback.
    Prevents the model from 'inventing' download/extraction fields."""

    url: str
    title: str
    filetype: Literal["pdf"]
    filename: str
    year: str = Field(pattern=r"^\d{4}$")
    diagnostics: Optional[Diagnostics] = None


class DownloadMeta(BaseModel):
    path: Optional[str] = None
    status: Optional[Literal["ok", "error"]] = None
    error: Optional[str] = None


class ExtractionMeta(BaseModel):
    pdf: Optional[str] = None
    pages: Optional[int] = None
    hits: Optional[List[int]] = None
    chosen_pages: Optional[List[int]] = None
    snippet_path: Optional[str] = None
    chars_total: Optional[int] = None
    chars_snippet: Optional[int] = None


class Company(BaseModel):
    name: str = Field(description="The full name of the company issuing the report.")
    ticker: str = Field(
        description="The company ticker symbol, typically including the exchange code."
    )
    report: Optional[Report] = Field(
        default=None, description="The sustainability report information."
    )


def query(client, company, ticker):
    """Create an OpenAI response for the given company and ticker.

    Returns a tuple of (raw_response, parsed_report) where parsed_report
    may be None if parsing failed.
    """
    model = os.getenv("S0_MODEL", "gpt-4o-mini")
    use_ddg = os.getenv("S0_USE_DDG", "1").lower() not in ("0", "false", "no")
    debug = os.getenv("S0_DEBUG", "0").lower() in ("1", "true", "yes")

    def _dbg(msg: str) -> None:
        if debug:
            print(msg, flush=True)

    try:
        ddg_timeout = float(os.getenv("S0_DDG_TIMEOUT_SEC", "8"))
    except Exception:
        ddg_timeout = 8.0

    def _ddg_candidates(q: str, max_items: int = 5) -> List[Candidate]:
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(q)
        _dbg(f"[s0] DDG query: {q}")
        _dbg(f"[s0] DDG URL: {url}")
        t0 = time.monotonic()
        try:
            html = requests.get(
                url, timeout=(3.0, ddg_timeout), headers={"User-Agent": "Mozilla/5.0"}
            ).text
        except Exception as e:
            _dbg(f"[s0] DDG request failed: {e}; falling back")
            return []
        dt = time.monotonic() - t0
        _dbg(f"[s0] DDG HTTP OK in {dt:.2f}s, parsing...")
        # Detect CAPTCHA/blocks and back off (return no candidates to trigger fallback)
        lower_html = html.lower()
        triggers = [
            "captcha",
            "detected unusual",
            "enable javascript",
            "unfortunately, bots use duckduckgo too",
            "error-lite@duckduckgo.com",
            "select all squares containing a duck",
        ]
        matched_contexts = []
        for trig in triggers:
            idx = lower_html.find(trig)
            if idx != -1:
                start = max(0, idx - 120)
                end = min(len(html), idx + len(trig) + 120)
                context = html[start:end].replace("\n", " ").strip()
                matched_contexts.append((trig, context))
        if matched_contexts:
            for trig, ctx in matched_contexts:
                _dbg(f"[s0] DDG block suspected: trigger='{trig}' context='{ctx}'")
            _dbg("[s0] DDG indicates captcha/JS/block; falling back")
            return []
        links = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.IGNORECASE,
        )
        if not links:
            links = re.findall(
                r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.IGNORECASE
            )
        _dbg(f"[s0] DDG raw links found: {len(links)}")
        # Try to capture result snippets in order (best-effort; structure can vary)
        snippets_a = re.findall(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippets_div = re.findall(
            r'<div[^>]+class="result__snippet"[^>]*>(.*?)</div>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        raw_snippets = [*snippets_a, *snippets_div]
        cleaned_snippets = [re.sub("<.*?>", "", s or "").strip() for s in raw_snippets]
        out: List[Candidate] = []
        for idx, (href, title_html) in enumerate(links):
            title = re.sub("<.*?>", "", title_html or "").strip()
            url_l = href.strip()
            # Decode DuckDuckGo redirector to get real URL
            if (
                url_l.startswith("//duckduckgo.com/l/?")
                or "duckduckgo.com/l/?" in url_l
            ):
                norm = url_l if url_l.startswith("http") else "https:" + url_l
                parsed = urlparse(norm)
                uddg = parse_qs(parsed.query or "").get("uddg", [None])[0]
                if uddg:
                    try:
                        url_l = unquote(uddg)
                    except Exception:
                        pass
            if url_l.startswith("//"):
                url_l = "https:" + url_l
            snippet = cleaned_snippets[idx] if idx < len(cleaned_snippets) else ""
            low = (title + " " + url_l + " " + snippet).lower()
            domain = ""
            try:
                domain = urlparse(url_l).netloc.lower()
            except Exception:
                domain = ""
            # Must be a direct PDF
            if not url_l.lower().endswith(".pdf"):
                continue
            # Allow sustainability/ESG/climate/TCFD or Annual Report
            if not any(
                k in low
                for k in ["sustainability", "esg", "climate", "tcfd", "annual report"]
            ):
                continue
            # Require company name or ticker match to reduce cross-company false positives
            base_ticker = (ticker.split("-")[0] if ticker else "").lower()
            company_clean = re.sub(r"[^a-z0-9]+", " ", (company or "").lower()).strip()
            tokens = [
                t
                for t in company_clean.split()
                if t and t not in {"ltd", "limited", "plc", "pty"}
            ]
            has_company_or_ticker = any(t in low for t in tokens) or (
                base_ticker and base_ticker in low
            )
            # Allow certain official announcement domains even if ticker not present in title/url
            whitelist_no_ticker = {"announcements.asx.com.au"}
            if not has_company_or_ticker and domain not in whitelist_no_ticker:
                continue
            # Enforce domain policy: prefer only .com or .com.au, with a small whitelist exception set
            domain_whitelist = {
                "announcements.asx.com.au",
                "wcsecure.weblink.com.au",
                "weblink.com.au",
                "sharepoint.com",
                "azureedge.net",
                "akamai",
            }
            allowed_tld = domain.endswith(".com") or domain.endswith(".com.au")
            is_whitelisted = any(w in domain for w in domain_whitelist)
            if not (allowed_tld or is_whitelisted):
                # Skip non .com/.com.au domains unless explicitly whitelisted
                continue
            why_bits = []
            if "2025" in low or "fy25" in low:
                why_bits.append("2025 match")
            if any(
                k in low
                for k in ["sustainability", "esg", "climate", "tcfd", "annual report"]
            ):
                why_bits.append("keyword match")
            out.append(
                Candidate(
                    url=url_l,
                    title=title,
                    why=", ".join(why_bits) or "pdf match",
                    snippet=snippet or None,
                )
            )
            if len(out) >= max_items:
                break

        def _has_fy25ish(text: str) -> bool:
            s = text.lower()
            if "fy25" in s or "fy-25" in s or "fy 25" in s:
                return True
            return bool(re.search(r"\b25\b", s))

        def _year_points(text: str) -> int:
            """Map the best year match to points: 2025=5, 2024=4, 2023=3, 2022=2, 2021=1."""
            s = text.lower()
            # FY shorthands
            for fy, pts in (
                ("fy25", 5),
                ("fy24", 4),
                ("fy23", 3),
                ("fy22", 2),
                ("fy21", 1),
            ):
                if (
                    fy in s
                    or fy.replace("y", "y ") in s
                    or fy.replace("fy", "fy-") in s
                ):
                    return pts
            # Full year
            years = re.findall(r"\b(20\d{2})\b", text)
            if years:
                try:
                    year_val = max(int(y) for y in years)
                except Exception:
                    year_val = None
                if year_val in (2025, 2024, 2023, 2022, 2021):
                    return {2025: 5, 2024: 4, 2023: 3, 2022: 2, 2021: 1}[year_val]
            return 0

        def rank(c: Candidate) -> tuple:
            ct = (c.title or "").lower()
            cu = (c.url or "").lower()
            cs = (c.snippet or "").lower()
            # Year preferences
            has_2025 = (
                "2025" in (ct + cu)
                or "fy25" in (ct + cu)
                or _has_fy25ish(ct)
                or _has_fy25ish(cu)
            )
            yr_points = _year_points(ct + " " + cu + " " + cs)
            # Sustainability phrase strength
            has_annual_phrase = ("annual report" in ct) or ("annual_report" in cu)
            has_esg_kw = (
                ("esg" in ct) or ("esg" in cu) or ("tcfd" in ct) or ("tcfd" in cu)
            )
            has_sust_kw = (
                ("sustainability" in ct)
                or ("sustainability" in cu)
                or ("climate" in ct)
                or ("climate" in cu)
            )
            scope1_in_snippet = ("scope 1" in cs) or (
                "scope i" in cs
            )  # be generous on OCR weirdness
            # Domain signals
            try:
                dom = urlparse(c.url).netloc.lower()
            except Exception:
                dom = ""
            official_dom = any(
                d in dom
                for d in [
                    "announcements.asx.com.au",
                    "asx.com.au",
                    "wcsecure.weblink.com.au",
                    "weblink.com.au",
                    "sharepoint.com",
                    "azureedge.net",
                    "akamai",
                ]
            )
            tld_bonus = (
                1 if dom.endswith(".com.au") else (0 if dom.endswith(".com") else 0)
            )
            # Company/ticker presence
            base_ticker = (ticker.split("-")[0] if ticker else "").lower()
            company_clean = re.sub(r"[^a-z0-9]+", " ", (company or "").lower()).strip()
            tokens = [
                t
                for t in company_clean.split()
                if t and t not in {"ltd", "limited", "plc", "pty"}
            ]
            company_hit = any(t in (ct + " " + cu) for t in tokens) or (
                base_ticker and base_ticker in (ct + " " + cu)
            )
            # Scoring per requirements:
            # - Year points (max weight)
            # - +1 if "annual report"
            # - +1 if ESG/sustainability/climate/TCFD present
            # - +1 if "scope 1" in description/snippet
            score = 0
            score += yr_points
            if has_annual_phrase:
                score += 1
            if has_esg_kw or has_sust_kw:
                score += 1
            if scope1_in_snippet:
                score += 1
            # Small bonuses
            score += tld_bonus
            if official_dom:
                score += 1
            if company_hit:
                score += 1
            # Return tuple for stable sort and debugging
            return (
                score,
                yr_points,
                1 if has_annual_phrase else 0,
                1 if (has_esg_kw or has_sust_kw) else 0,
                1 if scope1_in_snippet else 0,
                tld_bonus,
                1 if official_dom else 0,
                1 if company_hit else 0,
            )

        # Log scored candidates for diagnosis
        scored = []
        for c in out:
            r = rank(c)
            scored.append((r, c))
            try:
                dom = urlparse(c.url).netloc
            except Exception:
                dom = ""
            _dbg(
                f"[s0] DDG candidate score={r} domain={dom} url={c.url} title={c.title} snippet_present={bool(c.snippet)}"
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        out = [c for r, c in scored]
        _dbg(f"[s0] DDG filtered candidates: {len(out)}")
        return out[:max_items]

    ddg_query = f"{company} 2025 (sustainability OR ESG OR climate OR TCFD OR 'annual report') filetype:pdf"
    ddg_top = _ddg_candidates(ddg_query, max_items=3) if use_ddg else []

    if ddg_top:
        _dbg(f"[s0] Using DDG candidates (n={len(ddg_top)})")
        # Select best candidate locally (no model round-trip)
        best = ddg_top[0]
        # Build a minimal Report object
        try:
            p = urlparse(best.url)
            fname = (
                p.path.rsplit("/", 1)[-1] if p and p.path else "report.pdf"
            ) or "report.pdf"
        except Exception:
            fname = "report.pdf"
        diag = Diagnostics(
            web_search=WebSearchDiagnostics(
                search_query=ddg_query,
                candidates=ddg_top,
            )
        )
        parsed = Report(
            url=best.url,
            title=best.title or "",
            filetype="pdf",
            filename=fname,
            year="2025",
            data=None,
            diagnostics=diag,
            download=None,
            extraction=None,
        )
        _dbg(f"[s0] Selected DDG candidate: {best.url}")
        # Return a lightweight response object plus the parsed model
        return {"source": "ddg", "selected": best.url}, parsed

    instructions = dedent(
        f"""
        ## Objective
        Return the direct .pdf URL for the company's official 2025 (or FY25) sustainability/ESG/climate report.
        Output only the minimal fields listed below. Do NOT include any other fields
        such as download/extraction/data.

        ## One-shot web search
        - You have ONE web_search call. Form a precise query:
          "{company} (2025 OR FY25) (sustainability OR ESG OR climate OR TCFD) filetype:pdf"
        - Strong preferences:
          1) Year must be 2025 (or FY25) in title/URL/metadata.
          2) Direct .pdf link (ends with .pdf), not a viewer page.
          3) Official domain/CDN over third-party aggregators; avoid product manuals/release notes.
        - Reject 2024/2023 results unless no 2025 exists.

        ## Output (structured, ONLY these fields)
        - url: direct .pdf URL (empty if none that satisfy 2025/FY25)
        - title: report title
        - filetype: "pdf"
        - filename: file name
        - year: "2025"
        - diagnostics.web_search:
          {{"search_query": "<exact query used>",
            "candidates":[{{"url":"...","title":"...","why":"..."}}, ... up to 3]}}
        """
    ).strip()

    input_text = f'{{"name": "{company}", "ticker": "{ticker}"}}'

    _dbg("[s0] DDG yielded 0; using web_search tool fallback")
    response = client.responses.parse(
        instructions=instructions,
        input=input_text,
        text_format=MinimalReportForParse,
        # text={"verbosity": "low"},
        # reasoning={"effort": "low", "summary": "auto"},
        max_tool_calls=1,
        tools=[
            {
                "type": "web_search",
                "user_location": {"type": "approximate", "country": "AU"},
                "search_context_size": "low",
            }
        ],
        include=["web_search_call.results"],
        tool_choice="required",
        temperature=0,
        model=model,
        store=True,
    )
    # Map minimal parsed result into full Report, ensuring no extraneous fields are pre-filled
    mr = response.output_parsed
    try:
        if not mr or not (mr.url or "").strip():
            _dbg("[s0] Fallback parse returned empty URL; skipping")
            return response, None
        url_l = mr.url.strip()
        if not url_l.lower().endswith(".pdf"):
            _dbg(f"[s0] Fallback parse URL not a PDF: {url_l}; skipping")
            return response, None
        try:
            p = urlparse(url_l)
            path_name = (p.path or "").rsplit("/", 1)[-1].strip()
        except Exception:
            path_name = ""
        fname_source = path_name or (mr.filename or "").strip()
        # Ensure a valid filename with allowed extension
        if not re.search(
            r"\.(pdf|csv|xlsx|txt|html|htm)$", (fname_source or "").lower()
        ):
            fname = "report.pdf"
        else:
            fname = fname_source
        parsed = Report(
            url=url_l,
            title=(mr.title or "").strip(),
            filetype="pdf",
            filename=fname or "report.pdf",
            year=(mr.year or "2025"),
            data=None,
            diagnostics=mr.diagnostics,
            download=None,
            extraction=None,
        )
        return response, parsed
    except Exception as e:
        _dbg(f"[s0] Failed to map fallback parse into Report: {e}")
        return response, None
