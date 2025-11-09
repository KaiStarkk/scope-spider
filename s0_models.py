from typing import Optional
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
    filetype: str = Field(
        description="The file type/format of the report, e.g. pdf, csv, xlsx, txt, html, htm.",
        pattern=r"^(?i)(pdf|csv|xlsx|txt|html|htm)$",
    )
    filename: str = Field(
        description="The file name of the report.",
        pattern=r"^(?i).*\.(pdf|csv|xlsx|txt|html|htm)$",
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
    instructions = f"""
## Instructions
The company given has released an official 2025 report which includes its scope 1 and scope 2 emissions data. Search the web to locate this document. The document must be from 2025, and it must be a PDF. Typically it will be called "sustainability report", "climate report" or some variation on this. In some rare cases it will be the annual report, but only return that if you cannot find any superior alternative.

## Inputs:
{company} is the company's name.
{ticker} is the company's stock ticker symbol.
"""

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
