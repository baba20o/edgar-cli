"""Click command surface for edgar-cli."""

from __future__ import annotations

import json
import logging
import webbrowser
from pathlib import Path

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
        suggestions = result.get("suggestions") or []
        if suggestions:
            click.echo("Suggestions:", err=True)
            for suggestion in suggestions[:8]:
                click.echo(
                    f"  {suggestion.get('taxonomy', '')} {suggestion.get('tag', '')}"
                    f"  ({suggestion.get('label', '')})",
                    err=True,
                )
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
    fn = click.option("--markdown", "-m", is_flag=True, help="Output markdown; best for agents and reports")(fn)
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
@click.option("--all", "all_history", is_flag=True, help="Search historical filing chunks too")
@click.option("--show-urls", is_flag=True, help="Show filing URLs in table output")
@output_options
@click.pass_context
def company(ctx, identifier, limit, form_type, start_date, end_date, all_history, show_urls, json_output, markdown):
    """Show company profile and recent filings for a ticker or CIK."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).submissions(
        identifier, limit=limit, form=form_type, start_date=start_date,
        end_date=end_date, all_history=all_history,
    )
    _output_company(result, json_output, markdown, show_urls=show_urls)


@main.command()
@click.argument("identifier")
@click.option("--limit", "-n", default=20, show_default=True, help="Recent filings to show")
@click.option("--form", "form_type", default=None, help="Only show a form type, e.g. 8-K")
@click.option("--start-date", default=None, help="Only filings on/after YYYY-MM-DD")
@click.option("--end-date", default=None, help="Only filings on/before YYYY-MM-DD")
@click.option("--all", "all_history", is_flag=True, help="Search historical filing chunks too")
@click.option("--show-urls", is_flag=True, help="Show filing URLs in table output")
@output_options
@click.pass_context
def filings(ctx, identifier, limit, form_type, start_date, end_date, all_history, show_urls, json_output, markdown):
    """Show recent filings for a ticker or CIK."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).submissions(
        identifier, limit=limit, form=form_type, start_date=start_date,
        end_date=end_date, all_history=all_history,
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
@click.argument("terms", nargs=-1, required=True)
@click.option("--unit", "-u", default=None, help="Restrict to one unit, e.g. USD")
@click.option("--limit", "-n", default=20, show_default=True, help="Facts to show")
@click.option("--deltas", is_flag=True, help="Show change vs previous comparable displayed period")
@output_options
@click.pass_context
def concept(ctx, identifier, terms, unit, limit, deltas, json_output, markdown):
    """Show facts for one company XBRL concept or alias."""
    client = get_client(use_cache=not ctx.obj["no_cache"])
    if len(terms) == 1:
        result = client.company_concept_alias(identifier, terms[0], unit=unit, limit=limit)
    elif len(terms) == 2:
        taxonomy, tag = terms
        result = client.company_concept(identifier, taxonomy, tag, unit=unit, limit=limit)
    else:
        raise click.UsageError("Use either: concept IDENTIFIER ALIAS or concept IDENTIFIER TAXONOMY TAG")
    _output_concept_facts(result, json_output, markdown, deltas=deltas)


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


@main.command()
@click.argument("identifier")
@click.option("--form", "form_type", default="10-K", show_default=True, help="Latest form type to open")
@click.option("--all", "all_history", is_flag=True, help="Search historical filing chunks too")
@click.option("--print-only", is_flag=True, help="Print URL without launching a browser")
@click.pass_context
def open(ctx, identifier, form_type, all_history, print_only):
    """Open the latest filing index for a ticker or CIK."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).latest_filing(
        identifier, form=form_type, all_history=all_history,
    )
    _error_exit(result)
    url = result.get("filing_url", "")
    click.echo(url)
    if not print_only and url:
        webbrowser.open(url)


@main.command()
@click.argument("accession_or_url")
@click.option("--cik", default=None, help="CIK required when passing only an accession number")
@click.option("--download", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Download listed documents/exhibits to this directory")
@click.option("--type-filter", default=None, help="Only include document types containing this text")
@output_options
@click.pass_context
def exhibits(ctx, accession_or_url, cik, download, type_filter, json_output, markdown):
    """List or download documents/exhibits from a filing."""
    client = get_client(use_cache=not ctx.obj["no_cache"])
    result = client.filing_documents_for_accession(accession_or_url, cik=cik)
    _error_exit(result)
    if type_filter:
        needle = type_filter.upper()
        result["documents"] = [
            doc for doc in result.get("documents", [])
            if needle in doc.get("type", "").upper()
        ]
    if download:
        result = _download_documents(client, result, download)
        _error_exit(result)
    _output_documents(result, json_output, markdown)


@main.command()
@click.argument("identifier")
@output_options
@click.pass_context
def earnings(ctx, identifier, json_output, markdown):
    """Summarize the latest Item 2.02 earnings 8-K and exhibit."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).latest_earnings(identifier)
    _output_earnings(result, json_output, markdown)


@main.command()
@click.argument("identifier")
@click.option("--limit", "-n", default=20, show_default=True, help="Recent 8-Ks to inspect")
@output_options
@click.pass_context
def events(ctx, identifier, limit, json_output, markdown):
    """Detect notable recent 8-K events."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).events(identifier, limit=limit)
    _output_events(result, json_output, markdown)


@main.command()
@click.argument("identifiers", nargs=-1, required=True)
@click.option("--concept", "-c", required=True, help="Concept alias or tag, e.g. revenue or Assets")
@click.option("--taxonomy", "-t", default=None, help="Taxonomy override, e.g. us-gaap")
@click.option("--unit", "-u", default=None, help="Unit override, e.g. USD")
@click.option("--periods", "-n", default=4, show_default=True, help="Periods per company")
@output_options
@click.pass_context
def compare(ctx, identifiers, concept, taxonomy, unit, periods, json_output, markdown):
    """Compare one concept across companies."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).compare_concept(
        list(identifiers), concept, taxonomy=taxonomy, unit=unit, periods=periods,
    )
    _output_compare(result, json_output, markdown)


@main.command()
@click.argument("identifier")
@output_options
@click.pass_context
def brief(ctx, identifier, json_output, markdown):
    """Build a compact company brief."""
    result = get_client(use_cache=not ctx.obj["no_cache"]).brief(identifier)
    _output_brief(result, json_output, markdown)


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
    table.add_column("Tag", style="magenta", overflow="fold")
    table.add_column("Units", no_wrap=True)
    table.add_column("Facts", justify="right", no_wrap=True)
    table.add_column("Filed", style="green", no_wrap=True)
    for row in rows:
        table.add_row(str(row[1]), str(row[3]), str(row[4]), str(row[5]))
    table.caption = f"Showing {len(rows)} of {result.get('total', len(rows))} concepts"
    console.print(table)


def _output_concept_facts(result: dict, json_output: bool, markdown: bool,
                          deltas: bool = False) -> None:
    _error_exit(result)
    if json_output:
        _json(result)
        return

    facts = _add_deltas(result.get("facts", [])) if deltas else result.get("facts", [])
    rows = []
    for fact in facts:
        row = [
            fact.get("filed", ""),
            _format_period(fact),
            fact.get("frame", ""),
            fact.get("form", ""),
            _format_value(fact.get("val", ""), fact.get("unit", "")),
        ]
        if deltas:
            row.append(_format_delta(fact.get("_delta_pct")))
        row.append(fact.get("accn", ""))
        rows.append(row)

    headers = ["Filed", "Period", "Frame", "Form", "Value"]
    if deltas:
        headers.append("Change")
    headers.append("Accession")

    title = f"{result.get('taxonomy', '')}/{result.get('tag', '')}: {result.get('name', '')}"
    delta_note = deltas and any(fact.get("_delta_note") for fact in facts)
    if markdown:
        click.echo(f"## {title}\n")
        if delta_note:
            click.echo("> Deltas skip adjacent rows with mismatched period lengths.\n")
        click.echo(_markdown_table(headers, rows))
        return

    table = Table(title=title)
    table.add_column("Filed", style="green", no_wrap=True)
    table.add_column("Period", no_wrap=True)
    table.add_column("Frame", no_wrap=True)
    table.add_column("Form", style="cyan", no_wrap=True)
    table.add_column("Value", justify="right", no_wrap=True)
    if deltas:
        table.add_column("Change", justify="right", no_wrap=True)
    table.add_column("Accession", style="magenta", no_wrap=True)
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    caption = result.get("label", "")
    if delta_note:
        note = "Deltas skip adjacent rows with mismatched period lengths."
        caption = f"{caption} {note}".strip()
    table.caption = caption
    console.print(table)


def _output_documents(result: dict, json_output: bool, markdown: bool) -> None:
    _error_exit(result)
    if json_output:
        _json(result)
        return

    rows = [
        [
            doc.get("sequence", ""),
            doc.get("type", ""),
            doc.get("document", ""),
            _truncate(doc.get("description", ""), 72),
            doc.get("url", ""),
        ]
        for doc in result.get("documents", [])
    ]
    if markdown:
        click.echo(_markdown_table(["Seq", "Type", "Document", "Description", "URL"], rows))
        return

    table = Table(title=f"Documents: {result.get('accessionNumber', '')}")
    table.add_column("Seq", no_wrap=True)
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Document", style="magenta")
    table.add_column("Description")
    for row in rows:
        table.add_row(*[str(cell) for cell in row[:-1]])
    console.print(table)


def _output_earnings(result: dict, json_output: bool, markdown: bool) -> None:
    _error_exit(result)
    if json_output:
        _json(result)
        return

    filing = result.get("filing", {})
    exhibit = result.get("exhibit") or {}
    highlights = [[h.get("text", "")] for h in result.get("highlights", [])]
    if markdown:
        click.echo(f"## Earnings: {result.get('name', '')}\n")
        click.echo(_markdown_table(["Filed", "Accession", "Filing URL"], [[
            filing.get("filingDate", ""), filing.get("accessionNumber", ""), filing.get("filing_url", ""),
        ]]))
        if exhibit:
            click.echo("\n### Exhibit\n")
            click.echo(_markdown_table(["Type", "Document", "URL"], [[
                exhibit.get("type", ""), exhibit.get("document", ""), exhibit.get("url", ""),
            ]]))
        if highlights:
            click.echo("\n### Highlights\n")
            click.echo(_markdown_table(["Text"], highlights))
        return

    console.print(Panel(
        "\n".join([
            f"Filed: {filing.get('filingDate', '')}",
            f"Accession: {filing.get('accessionNumber', '')}",
            f"Exhibit: {exhibit.get('type', '')} {exhibit.get('document', '')}".strip(),
            filing.get("filing_url", ""),
        ]),
        title=f"Earnings: {result.get('name', '')}",
        expand=False,
    ))
    for item in result.get("highlights", [])[:8]:
        console.print(f"- {item.get('text', '')}")


def _output_events(result: dict, json_output: bool, markdown: bool) -> None:
    _error_exit(result)
    if json_output:
        _json(result)
        return

    rows = [
        [
            event.get("filingDate", ""),
            ", ".join(event.get("event_types", [])),
            event.get("items", ""),
            event.get("accessionNumber", ""),
            next(iter(event.get("snippets", {}).values()), ""),
        ]
        for event in result.get("events", [])
    ]
    if markdown:
        click.echo(_markdown_table(["Filed", "Events", "Items", "Accession", "Snippet"], rows))
        return

    table = Table(title=f"Events: {result.get('name', '')}")
    table.add_column("Filed", style="green", no_wrap=True)
    table.add_column("Events")
    table.add_column("Items", no_wrap=True)
    table.add_column("Accession", style="magenta", no_wrap=True)
    table.add_column("Snippet", overflow="fold")
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    console.print(table)


def _output_compare(result: dict, json_output: bool, markdown: bool) -> None:
    _error_exit(result)
    if json_output:
        _json(result)
        return

    rows = []
    for company in result.get("companies", []):
        if company.get("error"):
            rows.append([company.get("identifier", ""), "ERROR", "", company["error"], "", ""])
            continue
        for fact in company.get("facts", []):
            rows.append([
                company.get("identifier", ""),
                company.get("name", ""),
                fact.get("_tag", company.get("tag", "")),
                _format_period(fact),
                fact.get("frame", ""),
                _format_value(fact.get("val", ""), company.get("unit", "")),
            ])
    if markdown:
        for warning in result.get("warnings", []):
            click.echo(f"> {warning}\n")
        click.echo(_markdown_table(["Identifier", "Company", "Tag", "Period", "Frame", "Value"], rows))
        return

    table = Table(title=f"Compare: {result.get('taxonomy')}/{result.get('tag')}")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Company")
    table.add_column("Tag", style="magenta", overflow="fold")
    table.add_column("Period", no_wrap=True)
    table.add_column("Frame", no_wrap=True)
    table.add_column("Value", justify="right", no_wrap=True)
    if result.get("warnings"):
        table.caption = " ".join(result["warnings"])
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    console.print(table)


def _output_brief(result: dict, json_output: bool, markdown: bool) -> None:
    _error_exit(result)
    if json_output:
        _json(result)
        return

    profile = result.get("profile", {})
    metric_rows = [
        [
            metric.get("metric", ""),
            metric.get("tag", ""),
            _format_period(metric.get("fact", {})),
            _format_value(metric.get("fact", {}).get("val", ""), metric.get("unit", "")),
            _format_freshness(metric),
        ]
        for metric in result.get("metrics", [])
    ]
    event_rows = [
        [
            event.get("filingDate", ""),
            ", ".join(event.get("event_types", [])),
            event.get("accessionNumber", ""),
        ]
        for event in result.get("events", [])
    ]
    if markdown:
        click.echo(f"## {profile.get('name', '')}\n")
        click.echo(_markdown_table(["CIK", "Tickers", "Exchange", "SIC"], [[
            profile.get("cik", ""), ", ".join(profile.get("tickers", [])),
            ", ".join(profile.get("exchanges", [])), profile.get("sicDescription", ""),
        ]]))
        click.echo("\n### Latest Filings\n")
        _output_filings(profile, "Latest Filings", False, True)
        earnings = result.get("earnings") or {}
        if earnings:
            filing = earnings.get("filing", {})
            click.echo("\n### Latest Earnings\n")
            click.echo(_markdown_table(["Filed", "Accession", "URL"], [[
                filing.get("filingDate", ""),
                filing.get("accessionNumber", ""),
                filing.get("filing_url", ""),
            ]]))
        click.echo("\n### Key Metrics\n")
        click.echo(_markdown_table(["Metric", "Tag", "Period", "Value", "Freshness"], metric_rows))
        click.echo("\n### Recent Events\n")
        click.echo(_markdown_table(["Filed", "Events", "Accession"], event_rows))
        return

    _output_company(profile, False, False)
    earnings = result.get("earnings") or {}
    if earnings:
        filing = earnings.get("filing", {})
        console.print(Panel(
            "\n".join([
                f"Filed: {filing.get('filingDate', '')}",
                f"Accession: {filing.get('accessionNumber', '')}",
                filing.get("filing_url", ""),
            ]),
            title="Latest Earnings",
            expand=False,
        ))
    console.print(_simple_table("Key Metrics", ["Metric", "Tag", "Period", "Value", "Freshness"], metric_rows))
    if event_rows:
        console.print(_simple_table("Recent Events", ["Filed", "Events", "Accession"], event_rows))


def _simple_table(title: str, headers: list[str], rows: list[list]) -> Table:
    table = Table(title=title)
    for header in headers:
        table.add_column(header)
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    return table


def _download_documents(client, result: dict, directory: Path) -> dict:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"error": f"Could not create download directory {directory}: {exc}", "documents": result.get("documents", [])}

    for doc in result.get("documents", []):
        url = doc.get("url", "")
        if not url:
            continue
        target = directory / Path(doc.get("document") or "document").name
        try:
            target.write_bytes(client._get_bytes(url))
        except OSError as exc:
            return {"error": f"Could not write {target}: {exc}", "documents": result.get("documents", [])}
        except Exception as exc:
            return {"error": f"Could not download {url}: {exc}", "documents": result.get("documents", [])}
        doc["downloaded_to"] = str(target)
    return result


def _add_deltas(facts: list[dict]) -> list[dict]:
    rows = [dict(fact) for fact in facts]
    for index, row in enumerate(rows):
        if index + 1 >= len(rows):
            continue
        current = _number_or_none(row.get("val"))
        previous = _number_or_none(rows[index + 1].get("val"))
        if current is None or previous in (None, 0):
            continue
        if not _same_period_length(row, rows[index + 1]):
            row["_delta_note"] = "period mismatch"
            continue
        row["_delta_pct"] = (current - previous) / abs(previous) * 100
    return rows


def _number_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_delta(value) -> str:
    if value is None:
        return ""
    return f"{value:+.1f}%"


def _format_freshness(metric: dict) -> str:
    age_days = metric.get("age_days")
    if age_days is None:
        return ""
    if metric.get("stale"):
        return f"stale {_format_age(age_days)}"
    return "current"


def _format_age(days: int) -> str:
    if days < 45:
        return f"{days}d"
    if days < 730:
        return f"{days / 30:.1f}mo"
    return f"{days / 365:.1f}y"


def _same_period_length(left: dict, right: dict) -> bool:
    left_days = _period_days(left)
    right_days = _period_days(right)
    if left_days is None or right_days is None:
        return False
    return abs(left_days - right_days) <= 7


def _period_days(fact: dict) -> int | None:
    start = _parse_date(fact.get("start", ""))
    end = _parse_date(fact.get("end", ""))
    if end and not start:
        return 0
    if start and end:
        return (end - start).days
    return None


def _parse_date(value: str):
    from datetime import datetime

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


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


if __name__ == "__main__":
    main()
