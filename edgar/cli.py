"""Click command surface for edgar-cli."""

from __future__ import annotations

import json
import logging

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from edgar.api import BULK_ARCHIVES, get_client

console = Console()


def _json(data) -> None:
    click.echo(json.dumps(data, indent=2, default=str))


def _error_exit(result: dict) -> None:
    if "error" in result:
        click.echo(f"Error: {result['error']}", err=True)
        raise click.Abort()


def _truncate(text: str, width: int = 72) -> str:
    if not text:
        return ""
    text = str(text).replace("\n", " ").strip()
    return text[: width - 3] + "..." if len(text) > width else text


def _escape_md(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def _markdown_table(headers: list[str], rows: list[list]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_escape_md(cell) for cell in row) + " |")
    return "\n".join(lines)


def output_options(fn):
    fn = click.option("--json-output", "-j", is_flag=True, help="Output raw JSON")(fn)
    fn = click.option("--markdown", "-m", is_flag=True, help="Output markdown")(fn)
    return fn


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.option("--no-cache", is_flag=True, help="Skip local response cache")
@click.pass_context
def main(ctx, debug: bool, no_cache: bool):
    """SEC EDGAR public data CLI."""
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    ctx.ensure_object(dict)
    ctx.obj["no_cache"] = no_cache


@main.command("search-companies")
@click.argument("query")
@click.option("--limit", "-n", default=20, show_default=True, help="Maximum matches")
@output_options
@click.pass_context
def search_companies(ctx, query, limit, json_output, markdown):
    """Search SEC ticker, CIK, company, and exchange mappings."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).search_companies(query, limit=limit)
    _output_companies(result, json_output, markdown)


@main.command()
@click.argument("identifier")
@click.option("--limit", "-n", default=10, show_default=True, help="Recent filings to show")
@click.option("--form", "form_type", default=None, help="Only show a form type, e.g. 10-K")
@click.option("--start-date", default=None, help="Only filings on/after YYYY-MM-DD")
@click.option("--end-date", default=None, help="Only filings on/before YYYY-MM-DD")
@click.option("--show-urls", is_flag=True, help="Show filing URLs in table output")
@output_options
@click.pass_context
def company(ctx, identifier, limit, form_type, start_date, end_date, show_urls, json_output, markdown):
    """Show company profile and recent filings for a ticker or CIK."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).submissions(
        identifier, limit=limit, form=form_type, start_date=start_date, end_date=end_date,
    )
    _output_company(result, json_output, markdown, show_urls=show_urls)


@main.command()
@click.argument("identifier")
@click.option("--limit", "-n", default=20, show_default=True, help="Recent filings to show")
@click.option("--form", "form_type", default=None, help="Only show a form type, e.g. 8-K")
@click.option("--start-date", default=None, help="Only filings on/after YYYY-MM-DD")
@click.option("--end-date", default=None, help="Only filings on/before YYYY-MM-DD")
@click.option("--show-urls", is_flag=True, help="Show filing URLs in table output")
@output_options
@click.pass_context
def filings(ctx, identifier, limit, form_type, start_date, end_date, show_urls, json_output, markdown):
    """Show recent filings for a ticker or CIK."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).submissions(
        identifier, limit=limit, form=form_type, start_date=start_date, end_date=end_date,
    )
    _output_filings(result, f"Recent Filings: {identifier}", json_output, markdown, show_urls=show_urls)


@main.command()
@click.argument("identifier")
@click.option("--limit", "-n", default=50, show_default=True, help="Concepts to show")
@click.option("--taxonomy", "-t", default=None, help="Filter taxonomy, e.g. us-gaap")
@click.option("--tag-filter", "-q", default=None, help="Filter tag, label, or description")
@output_options
@click.pass_context
def facts(ctx, identifier, limit, taxonomy, tag_filter, json_output, markdown):
    """List XBRL concepts available for one company."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).company_facts(
        identifier, taxonomy=taxonomy, tag_filter=tag_filter, limit=limit,
    )
    _output_concepts(result, json_output, markdown)


@main.command()
@click.argument("identifier")
@click.argument("taxonomy")
@click.argument("tag")
@click.option("--unit", "-u", default=None, help="Restrict to one unit, e.g. USD")
@click.option("--limit", "-n", default=20, show_default=True, help="Facts to show")
@output_options
@click.pass_context
def concept(ctx, identifier, taxonomy, tag, unit, limit, json_output, markdown):
    """Show facts for one company XBRL concept."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).company_concept(
        identifier, taxonomy, tag, unit=unit, limit=limit,
    )
    _output_concept_facts(result, json_output, markdown)


@main.command()
@click.argument("taxonomy")
@click.argument("tag")
@click.argument("unit")
@click.argument("frame")
@click.option("--limit", "-n", default=25, show_default=True, help="Facts to show")
@click.option("--sort", "sort_by", type=click.Choice(["value", "name", "none"]),
              default="value", show_default=True)
@output_options
@click.pass_context
def frame(ctx, taxonomy, tag, unit, frame, limit, sort_by, json_output, markdown):
    """Show a cross-company XBRL frame."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).frame(
        taxonomy, tag, unit, frame, limit=limit, sort_by=sort_by,
    )
    _output_frame(result, json_output, markdown)


@main.command("bulk-urls")
@output_options
def bulk_urls(json_output, markdown):
    """List official SEC nightly bulk archive URLs."""
    result = {"archives": BULK_ARCHIVES}
    if json_output:
        _json(result)
        return
    rows = [[a["name"], a["description"], a["url"]] for a in BULK_ARCHIVES]
    if markdown:
        click.echo(_markdown_table(["Name", "Description", "URL"], rows))
        return
    table = Table(title="SEC EDGAR Bulk Archives")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Description")
    table.add_column("URL", style="green")
    for row in rows:
        table.add_row(*row)
    console.print(table)


@main.command("clear-cache")
def clear_cache():
    """Clear the local EDGAR cache."""
    from research_cli_base import FileCache

    count = FileCache(cache_dir="~/.edgar_cache").clear()
    click.echo(f"Cleared {count} cached entries.")


def _output_companies(result: dict, json_output: bool, markdown: bool) -> None:
    _error_exit(result)
    if json_output:
        _json(result)
        return

    rows = [
        [c.get("ticker", ""), c.get("cik", ""), c.get("name", ""), c.get("exchange", "")]
        for c in result.get("companies", [])
    ]
    if markdown:
        click.echo(_markdown_table(["Ticker", "CIK", "Name", "Exchange"], rows))
        return

    table = Table(title=f"Company Search: {result.get('query', '')}")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("CIK", style="magenta", no_wrap=True)
    table.add_column("Name")
    table.add_column("Exchange", style="green")
    for row in rows:
        table.add_row(*row)
    console.print(table)


def _output_company(result: dict, json_output: bool, markdown: bool, show_urls: bool = False) -> None:
    _error_exit(result)
    if json_output:
        _json(result)
        return

    if markdown:
        click.echo(f"## {result.get('name', '')}\n")
        click.echo(_markdown_table(
            ["CIK", "Tickers", "Exchanges", "SIC", "Fiscal Year End"],
            [[
                result.get("cik", ""),
                ", ".join(result.get("tickers", [])),
                ", ".join(result.get("exchanges", [])),
                f"{result.get('sic', '')} {result.get('sicDescription', '')}".strip(),
                result.get("fiscalYearEnd", ""),
            ]],
        ))
        click.echo()
        _output_filings(result, "Recent Filings", False, True, show_urls=show_urls)
        return

    body = "\n".join([
        f"CIK: {result.get('cik', '')}",
        f"Tickers: {', '.join(result.get('tickers', []))}",
        f"Exchanges: {', '.join(result.get('exchanges', []))}",
        f"SIC: {result.get('sic', '')} {result.get('sicDescription', '')}".strip(),
        f"Fiscal year end: {result.get('fiscalYearEnd', '')}",
    ])
    console.print(Panel(body, title=result.get("name", ""), expand=False))
    _output_filings(result, "Recent Filings", False, False, show_urls=show_urls)


def _output_filings(result: dict, title: str, json_output: bool, markdown: bool,
                    show_urls: bool = False) -> None:
    _error_exit(result)
    if json_output:
        _json({"cik": result.get("cik"), "name": result.get("name"), "filings": result.get("filings", [])})
        return

    rows = []
    for filing in result.get("filings", []):
        row = [
            filing.get("filingDate", ""),
            filing.get("form", ""),
            filing.get("accessionNumber", ""),
            _truncate(filing.get("primaryDocDescription", "") or filing.get("primaryDocument", ""), 52),
        ]
        if show_urls:
            row.append(filing.get("filing_url", ""))
        rows.append(row)

    headers = ["Filed", "Form", "Accession", "Description"]
    if show_urls:
        headers.append("URL")

    if markdown:
        if result.get("warning"):
            click.echo(f"> {result['warning']}\n")
        if not rows:
            click.echo("_No matching filings found._")
            return
        click.echo(_markdown_table(headers, rows))
        return

    table = Table(title=title)
    table.add_column("Filed", style="green", no_wrap=True)
    table.add_column("Form", style="cyan", no_wrap=True)
    table.add_column("Accession", style="magenta", no_wrap=True)
    table.add_column("Description")
    if show_urls:
        table.add_column("URL")
    for row in rows:
        table.add_row(*row)
    if result.get("warning"):
        table.caption = result["warning"]
    if not rows:
        table.add_row(*(["", "", "No matching filings found", ""] + ([""] if show_urls else [])))
    console.print(table)


def _output_concepts(result: dict, json_output: bool, markdown: bool) -> None:
    _error_exit(result)
    if json_output:
        _json(result)
        return

    rows = []
    for concept in result.get("concepts", []):
        rows.append([
            concept.get("taxonomy", ""),
            concept.get("tag", ""),
            _truncate(concept.get("label", ""), 48),
            concept.get("units", ""),
            concept.get("fact_count", ""),
            concept.get("latest_filed", ""),
        ])

    if markdown:
        click.echo(f"## XBRL Concepts: {result.get('name', '')}\n")
        click.echo(_markdown_table(["Taxonomy", "Tag", "Label", "Units", "Facts", "Latest Filed"], rows))
        return

    table = Table(title=f"XBRL Concepts: {result.get('name', '')}")
    table.add_column("Tag", style="magenta", no_wrap=False, overflow="fold")
    table.add_column("Latest Filed", style="green", no_wrap=True)
    for row in rows:
        table.add_row(str(row[1]), str(row[5]))
    table.caption = f"Showing {len(rows)} of {result.get('total', len(rows))} concepts"
    console.print(table)


def _output_concept_facts(result: dict, json_output: bool, markdown: bool) -> None:
    _error_exit(result)
    if json_output:
        _json(result)
        return

    rows = []
    for fact in result.get("facts", []):
        rows.append([
            fact.get("filed", ""),
            _format_period(fact),
            fact.get("frame", ""),
            fact.get("form", ""),
            _format_value(fact.get("val", ""), fact.get("unit", "")),
            fact.get("accn", ""),
        ])

    title = f"{result.get('taxonomy', '')}/{result.get('tag', '')}: {result.get('name', '')}"
    if markdown:
        click.echo(f"## {title}\n")
        click.echo(_markdown_table(["Filed", "Period", "Frame", "Form", "Value", "Accession"], rows))
        return

    table = Table(title=title)
    table.add_column("Filed", style="green", no_wrap=True)
    table.add_column("Period", no_wrap=True)
    table.add_column("Frame", no_wrap=True)
    table.add_column("Form", style="cyan", no_wrap=True)
    table.add_column("Value", justify="right", no_wrap=True)
    for row in rows:
        table.add_row(*[str(cell) for cell in row[:-1]])
    table.caption = result.get("label", "")
    console.print(table)


def _output_frame(result: dict, json_output: bool, markdown: bool) -> None:
    _error_exit(result)
    if json_output:
        _json(result)
        return

    rows = []
    for fact in result.get("facts", []):
        rows.append([
            fact.get("cik", ""),
            _truncate(fact.get("entityName", ""), 44),
            fact.get("loc", ""),
            fact.get("end", ""),
            _format_value(fact.get("val", ""), result.get("unit", "")),
            fact.get("accn", ""),
        ])

    title = f"{result.get('taxonomy', '')}/{result.get('tag', '')}/{result.get('unit', '')}/{result.get('frame', '')}"
    if markdown:
        click.echo(f"## {title}\n")
        click.echo(_markdown_table(["CIK", "Entity", "Location", "End", "Value", "Accession"], rows))
        return

    table = Table(title=title)
    table.add_column("CIK", style="magenta", no_wrap=True)
    table.add_column("Entity")
    table.add_column("Location", no_wrap=True)
    table.add_column("End", style="green", no_wrap=True)
    table.add_column("Value", justify="right")
    table.add_column("Accession", style="cyan", no_wrap=True)
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    table.caption = f"Showing {len(rows)} of {result.get('total', len(rows))} facts"
    console.print(table)


def _format_value(value, unit: str = "") -> str:
    if value in ("", None):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    suffix = ""
    display = number
    abs_number = abs(number)
    for threshold, label in [
        (1_000_000_000_000, "T"),
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "K"),
    ]:
        if abs_number >= threshold:
            display = number / threshold
            suffix = label
            break

    text = f"{display:.2f}".rstrip("0").rstrip(".") + suffix
    if unit == "USD":
        return f"${text}"
    if unit in {"USD/shares", "USD/share"}:
        return f"${number:.2f}/share"
    if unit == "shares":
        return text
    return text


def _format_period(fact: dict) -> str:
    start = fact.get("start", "")
    end = fact.get("end", "")
    if start and end:
        return f"{start}..{end}"
    return end or start


if __name__ == "__main__":
    main()
