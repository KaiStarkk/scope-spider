from typing import Optional
from pydantic import BaseModel, Field


class Report(BaseModel):
    url: str = Field(description="A direct link to the report file.")
    title: str = Field(description="The official title of the sustainability report.")
    filetype: str = Field(
        description="The file type/format of the report, e.g. pdf, docx."
    )
    filename: str = Field(description="The file name of the report.")
    year: str = Field(
        description="The year covered or published by the report, formatted as YYYY.",
        pattern=r"^\d{4}$",
    )


class Company(BaseModel):
    company_name: str = Field(
        description="The full name of the company issuing the report."
    )
    ticker: str = Field(
        description="The company ticker symbol, typically including the exchange code."
    )
    report: Optional[Report] = None


def search_and_parse(client, company, ticker):
    """Create an OpenAI response for the given company and ticker."""
    return client.responses.parse(
        prompt={
            "id": "pmpt_690fdf1b7be48194b628127978bdc9240b0df1327cec0e4c",
            "version": "1",
            "variables": {"name": company, "ticker": ticker},
        },
        input=[],
        text_format=Report,
        verbosity="low",
        reasoning={"effort": "low", "summary": None},
        tools=[
            {
                "type": "web_search",
                "user_location": {"type": "approximate", "country": "AU"},
                "search_context_size": "low",
            }
        ],
        model="gpt-5-nano",
        store=True,
    )
