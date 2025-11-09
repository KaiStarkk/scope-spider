from textwrap import dedent
from typing import Literal, Optional
from pydantic import BaseModel, Field


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
    data: Data = Field(
        description="A dictionary containing emissions data and optional qualifying information."
    )


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
    instructions = dedent(
        f"""
        ## Task
        Find the official 2025 sustainability report for the given company that contains Scope 1 and Scope 2 emissions data.

        ## Requirements
        - The report must be from 2025
        - The report must be a PDF file
        - Preferred document types (in order):
          1. Sustainability report
          2. Climate report or climate action report
          3. ESG report
          4. Annual report (only if no superior alternative exists)

        ## Company Information
        - Company name: {company}
        - Stock ticker: {ticker}

        ## Output
        Extract the following information from the report:
        - Direct URL to the PDF file
        - Report title
        - File type (must be pdf)
        - Filename
        - Year (2025)
        - Scope 1 emissions (in kgCO2e, rounded to nearest whole number)
        - Scope 2 emissions (in kgCO2e, rounded to nearest whole number)
        - Scope 3 emissions (in kgCO2e, rounded to nearest whole number, if available)
        - Any qualifiers or notes about the emissions data
        """
    ).strip()

    response = client.responses.parse(
        instructions=instructions,
        input=[],
        text_format=Report,
        text={"verbosity": "low"},
        reasoning={"effort": "low", "summary": "auto"},
        tools=[
            {
                "type": "web_search",
                "user_location": {"type": "approximate", "country": "AU"},
                "search_context_size": "low",
            }
        ],
        model="gpt-5-mini",
        store=True,
    )
    return response, response.output_parsed
