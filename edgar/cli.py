"""Click command surface for edgar-cli."""

from __future__ import annotations

import json
import logging
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from edgar.api import BULK_ARCHIVES, get_client
from edgar.state import StateStore

console = Console()
click.UsageError.exit_code = 5


def _json(data) -> None:
    click.echo(json.dumps(data, indent=2, default=str))
    _maybe_webhook(data)
    _maybe_export_csv(data)


def _ndjson(rows) -> None:
    for row in rows:
        click.echo(json.dumps(row, default=str, separators=(",", ":")))
    _maybe_webhook(rows)
    _maybe_export_csv(rows)


def _maybe_export_csv(payload) -> None:
    """If --export-csv was set, write the primary tabular slice to that path."""
    try:
        ctx = click.get_current_context(silent=True)
    except RuntimeError:
        return
    path = ctx.obj.get("export_csv") if ctx and ctx.obj else None
    if not path:
        return
    rows = payload if isinstance(payload, list) else _rows_for_export(payload)
    if not rows:
        return
    written = _export_csv(rows, path)
    click.echo(f"# wrote {written} rows to {path}", err=True)


def _export_csv(rows: list[dict], path: Path) -> int:
    """Write a list of dicts to CSV. Returns rows written. Headers are the
    union of keys across all rows, with first-seen order preserved."""
    import csv
    if not rows:
        path.write_text("", encoding="utf-8")
        return 0
    headers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            if key not in seen:
                headers.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            if isinstance(row, dict):
                # Flatten non-scalar values to JSON strings so CSV stays clean.
                flat = {k: (json.dumps(v, default=str)
                            if isinstance(v, (list, dict)) else v)
                        for k, v in row.items()}
                writer.writerow(flat)
    return len(rows)


def _rows_for_export(result: dict) -> list[dict]:
    """Pick the most useful list-of-dicts payload from a result envelope."""
    if not isinstance(result, dict):
        return []
    for key in ("facts", "matches", "filings", "concepts", "documents",
                "events", "rows", "ratios", "metrics", "transactions",
                "results", "lines", "flags", "checks", "peers", "frames",
                "companies", "candidates", "highlights", "proposals",
                "chains", "new_filings", "growth", "archives"):
        rows = result.get(key)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows
    return []


def _maybe_webhook(payload) -> None:
    """POST payload to the global --webhook URL if configured. Best-effort."""
    try:
        ctx = click.get_current_context(silent=True)
    except RuntimeError:
        return
    url = ctx.obj.get("webhook") if ctx and ctx.obj else None
    if not url:
        return
    try:
        import requests
        requests.post(url, json=payload, timeout=5)
    except Exception:
        pass


def _wants_json(json_output: bool, markdown: bool, ndjson: bool) -> bool:
    return json_output or (not markdown and not ndjson and not sys.stdout.isatty())


def _validate_date_option(ctx, param, value):
    """Click callback: reject anything that is not a YYYY-MM-DD calendar date."""
    if value in (None, ""):
        return value
    from datetime import datetime
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise click.BadParameter(f"{value!r} is not a valid YYYY-MM-DD date", param=param)
    return value


PositiveInt = click.IntRange(min=1)
PositiveOrZeroInt = click.IntRange(min=0)


def _format_citation(row: dict, ticker: str = "", name: str = "") -> str:
    """Build an agent-quotable citation string for a fact-like or filing-like row."""
    accession = row.get("accessionNumber") or row.get("accn") or ""
    filed = row.get("filed") or row.get("filingDate") or ""
    form = row.get("form") or ""
    period = row.get("fiscal_period") or row.get("calendar_period") or row.get("frame") or ""
    head_parts = [p for p in [ticker or name, period, form] if p]
    out = " ".join(head_parts)
    if accession:
        out = f"{out} · {accession}" if out else accession
    if filed:
        out = f"{out} · filed {filed}" if out else f"filed {filed}"
    return out


def _add_citations(result: dict) -> dict:
    """Walk a result dict and add a `citation` to each row-like child."""
    if not isinstance(result, dict):
        return result
    tickers = result.get("tickers") or []
    ticker = result.get("ticker") or (tickers[0] if tickers else "")
    name = result.get("name") or ""

    for key in ("facts", "filings", "events", "concepts", "documents"):
        rows = result.get(key)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    row["citation"] = _format_citation(row, ticker=ticker, name=name)

    if isinstance(result.get("companies"), list):
        for company in result["companies"]:
            if not isinstance(company, dict):
                continue
            cticker = company.get("ticker") or company.get("identifier") or ""
            cname = company.get("name", "")
            for fact in company.get("facts", []) or []:
                if isinstance(fact, dict):
                    fact["citation"] = _format_citation(fact, ticker=cticker, name=cname)

    if isinstance(result.get("metrics"), list):
        for metric in result["metrics"]:
            if isinstance(metric, dict) and isinstance(metric.get("fact"), dict):
                metric["fact"]["citation"] = _format_citation(metric["fact"], ticker=ticker, name=name)

    profile = result.get("profile")
    if isinstance(profile, dict):
        _add_citations(profile)
    earnings = result.get("earnings")
    if isinstance(earnings, dict) and isinstance(earnings.get("filing"), dict):
        earnings["filing"]["citation"] = _format_citation(
            earnings["filing"], ticker=ticker, name=name,
        )
    return result


def _finalize(client, result: dict, cite: bool = False) -> dict:
    """Add citations (optional) and the schema/cli/cache envelope to a result."""
    if not isinstance(result, dict):
        return result
    if cite:
        result = _add_citations(result)
    envelope = getattr(client, "_envelope", None)
    return envelope(result) if envelope else result


def _finalize_batch(client, results: list, key: str = "results", cite: bool = False) -> dict:
    """Wrap a list of per-identifier results in a single enveloped dict."""
    if cite:
        results = [_add_citations(r) if isinstance(r, dict) else r for r in results]
    payload = {key: results}
    envelope = getattr(client, "_envelope", None)
    return envelope(payload) if envelope else payload


def cite_option(fn):
    return click.option(
        "--cite", is_flag=True,
        help="Attach an agent-quotable citation string to each row",
    )(fn)


def since_last_fetch_option(fn):
    return click.option(
        "--since-last-fetch", "since_last_fetch", is_flag=True,
        help="Only return entries newer than the last fetch (state at ~/.edgar/state.json)",
    )(fn)


def _error_exit(result: dict) -> None:
    if "error" in result:
        message = str(result["error"])
        suggestions = result.get("suggestions") or []
        if suggestions:
            lines = [message, "Suggestions:"]
            for suggestion in suggestions[:8]:
                lines.append(
                    f"  {suggestion.get('taxonomy', '')} {suggestion.get('tag', '')}"
                    f"  ({suggestion.get('label', '')})"
                )
            message = "\n".join(lines)
        exc = click.ClickException(message)
        exc.exit_code = _exit_code_for_error(str(result["error"]))
        raise exc


def _exit_code_for_error(error: str) -> int:
    text = error.lower()
    if "429" in text or "rate limit" in text or "rate-limited" in text:
        return 3
    if (
        "no matching" in text
        or "no company found" in text
        or "no recent" in text
        or "no facts" in text
        or "404" in text
        or "not found" in text
    ):
        return 2
    if (
        "not a cik" in text
        or "required" in text
        or "blank" in text
        or "ambiguous" in text
        or "could not create" in text
        or "could not write" in text
    ):
        return 5
    if "403" in text or "failed after" in text or "timeout" in text or "unavailable" in text:
        return 4
    return 1


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


def _export_csv_option_callback(ctx, param, value):
    # Stored on ctx.obj (not passed to the command function) so the same
    # flag works both before and after the subcommand.
    if value is not None:
        ctx.ensure_object(dict)
        ctx.obj["export_csv"] = value
    return value


def output_options(fn):
    fn = click.option("--export-csv", "export_csv",
                      type=click.Path(dir_okay=False, path_type=Path), default=None,
                      expose_value=False, callback=_export_csv_option_callback,
                      help="Also write the primary tabular result to a CSV file")(fn)
    fn = click.option("--ndjson", is_flag=True, help="Stream primary rows as newline-delimited JSON")(fn)
    fn = click.option("--json-output", "-j", is_flag=True, help="Output raw JSON")(fn)
    fn = click.option("--markdown", "-m", is_flag=True, help="Output markdown; best for agents and reports")(fn)
    return fn


def _period_type_from_flags(annual: bool, quarterly: bool, ytd: bool, instant: bool) -> str | None:
    selected = [
        label for label, enabled in [
            ("annual", annual),
            ("quarterly", quarterly),
            ("ytd", ytd),
            ("instant", instant),
        ] if enabled
    ]
    if len(selected) > 1:
        raise click.UsageError("Choose only one period filter: --annual, --quarterly, --ytd, or --instant")
    return selected[0] if selected else None


def batch_options(fn):
    fn = click.option("--batch", is_flag=True, help="Read identifiers from stdin, one per line or comma-separated")(fn)
    fn = click.option("--input", "input_file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
                      default=None, help="Read identifiers from a file")(fn)
    fn = click.option("--tickers", default=None, help="Comma-separated identifiers to query in one invocation")(fn)
    return fn


def _split_input_values(text: str) -> list[str]:
    values = []
    for line in text.replace(",", "\n").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            values.append(value)
    return values


def _expand_groups(values: list[str], client=None) -> list[str]:
    """Expand any `@group` expressions in a list of identifiers."""
    expanded = []
    for value in values:
        if value.startswith("@"):
            if client is None:
                client = get_client()
            result = client.expand_group(value)
            if "error" in result:
                error = click.ClickException(f"Group expansion failed: {result['error']}")
                error.exit_code = 5
                raise error
            expanded.extend(result.get("identifiers", []))
        else:
            expanded.append(value)
    return expanded


def _collect_identifiers(identifier: str | None, tickers: str | None, input_file: Path | None,
                         batch: bool) -> list[str]:
    identifiers = [identifier] if identifier else []
    if tickers:
        identifiers.extend(_split_input_values(tickers))
    if input_file:
        try:
            identifiers.extend(_split_input_values(input_file.read_text()))
        except OSError as exc:
            error = click.ClickException(f"Could not read input file: {exc}")
            error.exit_code = 5
            raise error from exc
    if batch:
        identifiers.extend(_split_input_values(sys.stdin.read()))

    if any(value.startswith("@") for value in identifiers):
        identifiers = _expand_groups(identifiers)

    deduped = []
    seen = set()
    for value in identifiers:
        if value not in seen:
            deduped.append(value)
            seen.add(value)
    if not deduped:
        raise click.UsageError("Provide an identifier, --tickers, --input, or --batch stdin")
    return deduped


def _batch_failed(results: list[dict]) -> bool:
    return bool(results) and all("error" in result for result in results)


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.option("--no-cache", is_flag=True, help="Skip local response cache")
@click.option("--cache-max-mb", default=None, type=PositiveInt,
              help="Bound the local cache to this size in MB (LRU eviction).")
@click.option("--webhook", default=None,
              help="POST result JSON to this URL after the command completes (fire-and-forget)")
@click.option("--export-csv", "export_csv",
              type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Also write the primary tabular result to a CSV file")
@click.pass_context
def main(ctx, debug: bool, no_cache: bool, cache_max_mb, webhook: str | None,
         export_csv: Optional[Path]):
    """SEC EDGAR public data CLI."""
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    ctx.ensure_object(dict)
    ctx.obj["no_cache"] = no_cache
    ctx.obj["cache_max_mb"] = cache_max_mb
    ctx.obj["webhook"] = webhook
    ctx.obj["export_csv"] = export_csv


@main.command("search-companies")
@click.argument("query")
@click.option("--limit", "-n", default=20, show_default=True, type=PositiveInt, help="Maximum matches")
@output_options
@click.pass_context
def search_companies(ctx, query, limit, markdown, json_output, ndjson):
    """Search SEC ticker, CIK, company, and exchange mappings."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.search_companies(query, limit=limit)
    result = _finalize(client, result)
    _output_companies(result, json_output, markdown, ndjson)


@main.command()
@click.argument("identifier")
@click.option("--limit", "-n", default=10, show_default=True, type=PositiveInt, help="Recent filings to show")
@click.option("--form", "form_type", default=None, help="Only show a form type, e.g. 10-K")
@click.option("--start-date", default=None, callback=_validate_date_option, help="Only filings on/after YYYY-MM-DD")
@click.option("--end-date", default=None, callback=_validate_date_option, help="Only filings on/before YYYY-MM-DD")
@click.option("--all", "all_history", is_flag=True, help="Search historical filing chunks too")
@click.option("--show-urls", is_flag=True, help="Show filing URLs in table output")
@click.option("--major", "form_class_flag", flag_value="major",
              help="Only major forms (10-K/10-Q/8-K/S-1/proxy/20-F)")
@click.option("--insider", "form_class_flag", flag_value="insider",
              help="Only insider forms (3, 4, 5, 144)")
@click.option("--institutional", "form_class_flag", flag_value="institutional",
              help="Only institutional forms (SC 13D/G, 13F)")
@cite_option
@since_last_fetch_option
@output_options
@click.pass_context
def company(ctx, identifier, limit, form_type, start_date, end_date, all_history, show_urls,
            form_class_flag, cite, since_last_fetch, markdown, json_output, ndjson):
    """Show company profile and recent filings for a ticker or CIK."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    state_store = StateStore() if since_last_fetch else None
    result = client.submissions(
        identifier, limit=limit, form=form_type, start_date=start_date,
        end_date=end_date, all_history=all_history,
        since_last_fetch=since_last_fetch, state_store=state_store,
        form_class=form_class_flag,
    )
    result = _finalize(client, result, cite=cite)
    _output_company(result, json_output, markdown, ndjson, show_urls=show_urls)


@main.command()
@click.argument("identifier")
@click.option("--limit", "-n", default=20, show_default=True, type=PositiveInt, help="Recent filings to show")
@click.option("--form", "form_type", default=None, help="Only show a form type, e.g. 8-K")
@click.option("--start-date", default=None, callback=_validate_date_option, help="Only filings on/after YYYY-MM-DD")
@click.option("--end-date", default=None, callback=_validate_date_option, help="Only filings on/before YYYY-MM-DD")
@click.option("--all", "all_history", is_flag=True, help="Search historical filing chunks too")
@click.option("--show-urls", is_flag=True, help="Show filing URLs in table output")
@click.option("--major", "form_class_flag", flag_value="major",
              help="Only major forms (10-K/10-Q/8-K/S-1/proxy/20-F)")
@click.option("--insider", "form_class_flag", flag_value="insider",
              help="Only insider forms (3, 4, 5, 144)")
@click.option("--institutional", "form_class_flag", flag_value="institutional",
              help="Only institutional forms (SC 13D/G, 13F)")
@cite_option
@since_last_fetch_option
@output_options
@click.pass_context
def filings(ctx, identifier, limit, form_type, start_date, end_date, all_history, show_urls,
            form_class_flag, cite, since_last_fetch, markdown, json_output, ndjson):
    """Show recent filings for a ticker or CIK."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    state_store = StateStore() if since_last_fetch else None
    result = client.submissions(
        identifier, limit=limit, form=form_type, start_date=start_date,
        end_date=end_date, all_history=all_history,
        since_last_fetch=since_last_fetch, state_store=state_store,
        form_class=form_class_flag,
    )
    result = _finalize(client, result, cite=cite)
    _output_filings(result, f"Recent Filings: {identifier}", json_output, markdown, ndjson, show_urls=show_urls)


@main.command()
@click.argument("identifier")
@click.option("--limit", "-n", default=50, show_default=True, type=PositiveInt, help="Concepts to show")
@click.option("--taxonomy", "-t", default=None, help="Filter taxonomy, e.g. us-gaap")
@click.option("--tag-filter", "-q", default=None, help="Filter tag, label, or description")
@output_options
@click.pass_context
def facts(ctx, identifier, limit, taxonomy, tag_filter, markdown, json_output, ndjson):
    """List XBRL concepts available for one company."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.company_facts(
        identifier, taxonomy=taxonomy, tag_filter=tag_filter, limit=limit,
    )
    result = _finalize(client, result)
    _output_concepts(result, json_output, markdown, ndjson)


@main.command()
@click.argument("args", nargs=-1, required=True)
@click.option("--unit", "-u", default=None, help="Restrict to one unit, e.g. USD")
@click.option("--limit", "-n", default=20, show_default=True, type=PositiveInt, help="Facts to show")
@click.option("--deltas", is_flag=True, help="Show change vs previous comparable displayed period")
@click.option("--annual", is_flag=True, help="Only annual-duration facts")
@click.option("--quarterly", is_flag=True, help="Only quarterly-duration facts")
@click.option("--ytd", is_flag=True, help="Only year-to-date facts")
@click.option("--instant", is_flag=True, help="Only instant facts")
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option,
              help="Only return facts filed on/before YYYY-MM-DD (eliminates look-ahead bias)")
@click.option("--since", "since", default=None, callback=_validate_date_option,
              help="Only return facts filed on/after YYYY-MM-DD (concept-keyed incremental)")
@click.option("--canonical", is_flag=True,
              help="Union facts across all candidate tags for the alias (deduped on start/end/accn)")
@click.option("--explain", "explain", is_flag=True,
              help="Add a resolution trace: candidates tried, fact counts, winner")
@batch_options
@cite_option
@output_options
@click.pass_context
def concept(ctx, args, unit, limit, deltas, annual, quarterly, ytd, instant,
            as_of, since, canonical, explain,
            tickers, input_file, batch, cite, markdown, json_output, ndjson):
    """Show facts for one company XBRL concept or alias."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    period_type = _period_type_from_flags(annual, quarterly, ytd, instant)
    batch_mode = bool(tickers or input_file or batch)
    if batch_mode:
        identifiers = _collect_identifiers(None, tickers, input_file, batch)
        terms = args
    else:
        if len(args) < 2:
            raise click.UsageError("Use either: concept IDENTIFIER ALIAS or concept IDENTIFIER TAXONOMY TAG")
        identifiers = [args[0]]
        terms = args[1:]

    if len(terms) == 1:
        fetch = lambda identifier: client.company_concept_alias(
            identifier, terms[0], unit=unit, limit=limit, period_type=period_type,
            as_of=as_of, since=since, canonical_union=canonical,
        )
    elif len(terms) == 2:
        taxonomy, tag = terms
        fetch = lambda identifier: client.company_concept(
            identifier, taxonomy, tag, unit=unit, limit=limit, period_type=period_type,
            as_of=as_of, since=since,
        )
    else:
        raise click.UsageError("Use either: concept IDENTIFIER ALIAS or concept IDENTIFIER TAXONOMY TAG")

    results = []
    for identifier in identifiers:
        result = fetch(identifier)
        result.setdefault("identifier", identifier)
        if explain and len(terms) == 1:
            result["_explain"] = client.explain_concept(
                identifier, terms[0], unit=unit, period_type=period_type,
            )
        results.append(result)
    if len(results) == 1 and not batch_mode:
        single = _finalize(client, results[0], cite=cite)
        _output_concept_facts(single, json_output, markdown, ndjson, deltas=deltas)
    else:
        wrapped = _finalize_batch(client, results, key="results", cite=cite)
        _output_concept_batch(wrapped["results"], json_output, markdown, ndjson, deltas=deltas, envelope=wrapped)


@main.command()
@click.argument("taxonomy")
@click.argument("tag")
@click.argument("unit")
@click.argument("frame")
@click.option("--limit", "-n", default=25, show_default=True, type=PositiveInt, help="Facts to show")
@click.option("--sort", "sort_by", type=click.Choice(["value", "name", "none"]),
              default="value", show_default=True)
@cite_option
@output_options
@click.pass_context
def frame(ctx, taxonomy, tag, unit, frame, limit, sort_by, cite, markdown, json_output, ndjson):
    """Show a cross-company XBRL frame."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.frame(taxonomy, tag, unit, frame, limit=limit, sort_by=sort_by)
    result = _finalize(client, result, cite=cite)
    _output_frame(result, json_output, markdown, ndjson)


@main.command()
@click.argument("identifier")
@click.option("--form", "form_type", default="10-K", show_default=True, help="Latest form type to open")
@click.option("--all", "all_history", is_flag=True, help="Search historical filing chunks too")
@click.option("--print-only", is_flag=True, help="Print URL without launching a browser")
@click.pass_context
def open(ctx, identifier, form_type, all_history, print_only):
    """Open the latest filing index for a ticker or CIK."""
    result = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb")).latest_filing(
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
def exhibits(ctx, accession_or_url, cik, download, type_filter, markdown, json_output, ndjson):
    """List or download documents/exhibits from a filing."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
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
    _output_documents(result, json_output, markdown, ndjson)


@main.command()
@click.argument("identifier")
@cite_option
@output_options
@click.pass_context
def earnings(ctx, identifier, cite, markdown, json_output, ndjson):
    """Summarize the latest Item 2.02 earnings 8-K and exhibit."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.latest_earnings(identifier)
    result = _finalize(client, result, cite=cite)
    _output_earnings(result, json_output, markdown, ndjson)


@main.command()
@click.argument("identifier")
@click.option("--limit", "-n", default=20, show_default=True, type=PositiveInt, help="Recent 8-Ks to inspect")
@cite_option
@since_last_fetch_option
@output_options
@click.pass_context
def events(ctx, identifier, limit, cite, since_last_fetch, markdown, json_output, ndjson):
    """Detect notable recent 8-K events."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    state_store = StateStore() if since_last_fetch else None
    result = client.events(
        identifier, limit=limit,
        since_last_fetch=since_last_fetch, state_store=state_store,
    )
    result = _finalize(client, result, cite=cite)
    _output_events(result, json_output, markdown, ndjson)


@main.command()
@click.argument("identifiers", nargs=-1, required=True)
@click.option("--concept", "-c", required=True, help="Concept alias or tag, e.g. revenue or Assets")
@click.option("--taxonomy", "-t", default=None, help="Taxonomy override, e.g. us-gaap")
@click.option("--unit", "-u", default=None, help="Unit override, e.g. USD")
@click.option("--periods", "-n", default=4, show_default=True, type=PositiveInt, help="Periods per company")
@click.option("--annual", is_flag=True, help="Only annual-duration facts")
@click.option("--quarterly", is_flag=True, help="Only quarterly-duration facts")
@click.option("--ytd", is_flag=True, help="Only year-to-date facts")
@click.option("--instant", is_flag=True, help="Only instant facts")
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option,
              help="Only return facts filed on/before YYYY-MM-DD")
@cite_option
@output_options
@click.pass_context
def compare(ctx, identifiers, concept, taxonomy, unit, periods, annual, quarterly, ytd, instant,
            as_of, cite, markdown, json_output, ndjson):
    """Compare one concept across companies."""
    period_type = _period_type_from_flags(annual, quarterly, ytd, instant)
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.compare_concept(
        list(identifiers), concept, taxonomy=taxonomy, unit=unit, periods=periods,
        period_type=period_type, as_of=as_of,
    )
    result = _finalize(client, result, cite=cite)
    _output_compare(result, json_output, markdown, ndjson)


@main.command()
@click.argument("identifier", required=False)
@click.option("--bundle", default="revenue,net_income,operating_income,operating_cash_flow,cash,debt,shares",
              show_default=True, help="Comma-separated metric aliases")
@batch_options
@cite_option
@output_options
@click.pass_context
def metrics(ctx, identifier, bundle, tickers, input_file, batch, cite, markdown, json_output, ndjson):
    """Return a bundled canonical metric set for one company.

    `--bundle` accepts comma-separated alias names AND named bundle groups:
    `income-statement`, `balance-sheet`, `cash-flow`, `liquidity`,
    `capital-structure`, `quality`. Group names expand into their members.
    """
    from edgar.api import METRIC_BUNDLE_GROUPS
    labels = []
    for item in bundle.split(","):
        item = item.strip()
        if not item:
            continue
        if item in METRIC_BUNDLE_GROUPS:
            labels.extend(METRIC_BUNDLE_GROUPS[item])
        else:
            labels.append(item)
    identifiers = _collect_identifiers(identifier, tickers, input_file, batch)
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    results = []
    for value in identifiers:
        result = client.metrics(value, labels)
        result.setdefault("identifier", value)
        results.append(result)
    if len(results) == 1 and not (tickers or input_file or batch):
        single = _finalize(client, results[0], cite=cite)
        _output_metrics(single, json_output, markdown, ndjson)
    else:
        wrapped = _finalize_batch(client, results, key="results", cite=cite)
        _output_metrics_batch(wrapped["results"], json_output, markdown, ndjson, envelope=wrapped)


@main.command()
@click.argument("identifier", required=False)
@batch_options
@cite_option
@output_options
@click.pass_context
def brief(ctx, identifier, tickers, input_file, batch, cite, markdown, json_output, ndjson):
    """Build a compact company brief."""
    identifiers = _collect_identifiers(identifier, tickers, input_file, batch)
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    results = []
    for value in identifiers:
        result = client.brief(value)
        result.setdefault("identifier", value)
        results.append(result)
    if len(results) == 1 and not (tickers or input_file or batch):
        single = _finalize(client, results[0], cite=cite)
        _output_brief(single, json_output, markdown, ndjson)
    else:
        wrapped = _finalize_batch(client, results, key="results", cite=cite)
        _output_brief_batch(wrapped["results"], json_output, markdown, ndjson, envelope=wrapped)


@main.command()
@click.argument("identifier")
@output_options
@click.pass_context
def dei(ctx, identifier, markdown, json_output, ndjson):
    """Show entity-level (DEI) metadata for a filer."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.dei(identifier)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson([result])
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    body = "\n".join([
        f"CIK: {result.get('cik', '')}",
        f"Tickers: {', '.join(result.get('tickers', []))}",
        f"Exchanges: {', '.join(result.get('exchanges', []))}",
        f"SIC: {result.get('sic', '')} {result.get('sicDescription', '')}".strip(),
        f"Entity type: {result.get('entityType', '')}",
        f"Category: {result.get('category', '')}",
        f"Fiscal year end: {result.get('fiscalYearEnd', '')}",
        f"State of incorporation: {result.get('stateOfIncorporationDescription') or result.get('stateOfIncorporation', '')}",
        f"EIN: {result.get('ein', '')}",
        f"Phone: {result.get('phone', '')}",
        f"Former names: {len(result.get('formerNames', []))}",
    ])
    console.print(Panel(body, title=f"DEI: {result.get('name', '')}", expand=False))


@main.command()
@click.argument("identifier")
@click.option("--by", default="sic", show_default=True, type=click.Choice(["sic"]),
              help="Peer ranking method")
@click.option("--candidates", default=None,
              help="Comma-separated tickers or @group expression to search (default @dow30)")
@click.option("--limit", "-n", default=10, show_default=True, type=PositiveInt)
@output_options
@click.pass_context
def peers(ctx, identifier, by, candidates, limit, markdown, json_output, ndjson):
    """Find peer filers from a candidate set, matched on SIC code."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    candidate_list = None
    if candidates:
        candidate_list = _expand_groups(_split_input_values(candidates), client=client)
    result = client.peers(identifier, candidates=candidate_list, by=by, limit=limit)
    result = _finalize(client, result)
    _error_exit(result)
    peers_list = result.get("peers", [])
    if ndjson:
        _ndjson(peers_list)
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    target = result.get("target", {})
    rows = [[p["cik"], p["ticker"], p["name"], p["exchange"]] for p in peers_list]
    headers = ["CIK", "Ticker", "Name", "Exchange"]
    title = f"Peers of {target.get('ticker') or target.get('name', '')} (SIC {target.get('sic')})"
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command("concept-info")
@click.argument("alias_or_tag")
@click.option("--taxonomy", default="us-gaap", show_default=True)
@click.option("--filer", default="AAPL", show_default=True,
              help="Reference filer used to surface label/description/freshness")
@output_options
@click.pass_context
def concept_info_cmd(ctx, alias_or_tag, taxonomy, filer, markdown, json_output, ndjson):
    """Show metadata about a concept alias or XBRL tag (candidates, units, label)."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.concept_info(alias_or_tag, taxonomy=taxonomy, filer=filer)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson([result])
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    if result.get("is_alias"):
        rows = [[c["taxonomy"], c["tag"], c.get("label", ""), c.get("default_unit", ""),
                 c.get("fact_count_in_filer", 0), c.get("latest_filed_in_filer", "")]
                for c in result.get("candidates", [])]
        headers = ["Taxonomy", "Tag", "Label", "Default Unit", "Facts", "Latest"]
        title = f"Alias: {result['alias']} (reference filer: {result['reference_filer']})"
    else:
        rows = [[result.get("taxonomy", ""), result.get("tag", ""),
                 result.get("label", ""), result.get("units", ""),
                 result.get("fact_count_in_filer", 0), result.get("latest_filed_in_filer", "")]]
        headers = ["Taxonomy", "Tag", "Label", "Units", "Facts", "Latest"]
        title = f"Tag: {result.get('tag')} (reference filer: {result['reference_filer']})"
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command()
@click.argument("query")
@click.option("--filer", default="AAPL", show_default=True,
              help="Reference filer whose reported tags are searched")
@click.option("--taxonomy", default="us-gaap", show_default=True)
@click.option("--limit", "-n", default=25, show_default=True, type=PositiveInt)
@output_options
@click.pass_context
def tags(ctx, query, filer, taxonomy, limit, markdown, json_output, ndjson):
    """Search XBRL tag labels/descriptions for a phrase."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.tag_search(query, filer=filer, taxonomy=taxonomy, limit=limit)
    result = _finalize(client, result)
    _output_tag_search(result, json_output, markdown, ndjson)


@main.command()
@click.option("--taxonomy", default="us-gaap", show_default=True)
@click.option("--since", "since_year", type=int, default=None,
              help="Earliest calendar year (default 2009)")
@click.option("--until", "until_year", type=int, default=None,
              help="Latest calendar year (default current year)")
@click.option("--annual/--no-annual", default=True)
@click.option("--quarterly/--no-quarterly", default=True)
@click.option("--instant/--no-instant", default=True)
@output_options
@click.pass_context
def frames(ctx, taxonomy, since_year, until_year, annual, quarterly, instant,
           markdown, json_output, ndjson):
    """Enumerate plausible XBRL frame strings deterministically."""
    from edgar.api import EdgarClient

    kinds = set()
    if annual:
        kinds.add("annual")
    if quarterly:
        kinds.add("quarterly")
    if instant:
        kinds.add("instant")
    result = EdgarClient.list_frames(taxonomy=taxonomy, since_year=since_year,
                                     until_year=until_year, kinds=kinds)
    if ndjson:
        _ndjson(result.get("frames", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = [[f["frame"], f["kind"], f["year"], f["quarter"] or ""] for f in result["frames"]]
    headers = ["Frame", "Kind", "Year", "Quarter"]
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(f"Frames {result['since_year']}-{result['until_year']}", headers, rows))


def _output_tag_search(result: dict, json_output: bool, markdown: bool, ndjson: bool = False) -> None:
    _error_exit(result)
    matches = result.get("matches", [])
    if ndjson:
        _ndjson(matches)
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = [[m["tag"], _truncate(m.get("label", ""), 48), m.get("units", ""),
             m.get("fact_count", ""), m.get("latest_filed", "")] for m in matches]
    headers = ["Tag", "Label", "Units", "Facts", "Latest Filed"]
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(f"Tags matching {result.get('query')!r}", headers, rows))


@main.command()
@click.argument("identifier")
@click.option("--bundle", default="revenue,net_income,operating_income,operating_cash_flow",
              show_default=True)
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option,
              help="Trailing twelve months as of YYYY-MM-DD (look-ahead-safe)")
@output_options
@click.pass_context
def ttm(ctx, identifier, bundle, as_of, markdown, json_output, ndjson):
    """Trailing-twelve-months reconstruction for a metric bundle."""
    labels = [item.strip() for item in bundle.split(",") if item.strip()]
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.ttm(identifier, bundle=labels, as_of=as_of)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("metrics", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = [[m.get("metric"), _format_value(m.get("value", "")),
             m.get("formula", "")[:80], ", ".join(m.get("missing_inputs", []))]
            for m in result.get("metrics", [])]
    headers = ["Metric", "TTM", "Formula", "Missing"]
    title = f"TTM: {result.get('name', identifier)}"
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command()
@click.argument("identifier")
@click.option("--period-type", default="annual",
              type=click.Choice(["annual", "quarterly", "ytd"]), show_default=True)
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option)
@output_options
@click.pass_context
def ratios(ctx, identifier, period_type, as_of, markdown, json_output, ndjson):
    """Canonical ratio set with formula + provenance per ratio."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.ratios(identifier, period_type=period_type, as_of=as_of)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("ratios", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = []
    for r in result.get("ratios", []):
        v = r.get("value")
        rows.append([
            r.get("metric", ""),
            f"{v*100:.2f}%" if isinstance(v, (int, float)) and abs(v) < 10 and "margin" in r.get("metric", "") else (
                f"{v:.2f}" if isinstance(v, (int, float)) else ""),
            r.get("formula", "")[:64],
            ", ".join(r.get("missing_inputs", [])),
        ])
    headers = ["Ratio", "Value", "Formula", "Missing"]
    title = f"Ratios: {result.get('name', identifier)} ({period_type})"
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command()
@click.argument("identifier")
@click.option("--metric", "-c", required=True, help="Metric alias (e.g. revenue, net_income)")
@click.option("--periods", "-n", default=8, show_default=True, type=PositiveInt)
@click.option("--period-type", default="quarterly",
              type=click.Choice(["annual", "quarterly", "ytd", "instant"]), show_default=True)
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option)
@output_options
@click.pass_context
def trend(ctx, identifier, metric, periods, period_type, as_of, markdown, json_output, ndjson):
    """Multi-period trend with slope, direction, and categorical label."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.trend(identifier, metric, periods=periods,
                          period_type=period_type, as_of=as_of)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("facts", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    facts = result.get("facts", [])
    summary = result.get("summary", {})
    rows = [[f.get("end", ""), f.get("calendar_period", ""),
             _format_value(f.get("val", ""), f.get("unit", ""))]
            for f in sorted(facts, key=lambda x: x.get("end", ""))]
    headers = ["Period End", "Frame", "Value"]
    title = (f"Trend: {result.get('name', identifier)} {metric} "
             f"({period_type}) — {summary.get('label', '')}, slope={summary.get('slope')}")
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command()
@click.argument("identifier")
@click.option("--metric", "-c", required=True)
@click.option("--basis", default="yoy", show_default=True,
              help="Comma-separated: yoy,qoq,cagr3,cagr5")
@click.option("--periods", "-n", default=8, show_default=True, type=PositiveInt)
@click.option("--period-type", default="annual",
              type=click.Choice(["annual", "quarterly", "ytd"]), show_default=True)
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option)
@output_options
@click.pass_context
def growth(ctx, identifier, metric, basis, periods, period_type, as_of,
           markdown, json_output, ndjson):
    """Multi-basis growth: yoy, qoq, cagr3, cagr5."""
    bases = [b.strip() for b in basis.split(",") if b.strip()]
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.growth(identifier, metric, basis=bases, periods=periods,
                           period_type=period_type, as_of=as_of)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("growth", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = []
    for g in result.get("growth", []):
        if g.get("basis") in {"yoy", "qoq"}:
            latest = g.get("latest")
            rows.append([g["basis"],
                         f"{latest*100:+.1f}%" if isinstance(latest, (int, float)) else "",
                         f"{len(g.get('rates', []))} rates computed"])
        else:
            v = g.get("value")
            rows.append([g["basis"],
                         f"{v*100:+.1f}%" if isinstance(v, (int, float)) else "",
                         f"window={g.get('window_size', 0)}"])
    headers = ["Basis", "Value", "Detail"]
    title = f"Growth: {result.get('name', identifier)} {metric} ({period_type})"
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command()
@click.argument("identifier")
@click.option("--metric", "-c", required=True,
              type=click.Choice(["ebitda", "fcf", "net_debt", "nwc", "working_capital",
                                  "tangible_book", "tangible_book_value"]))
@click.option("--period-type", default="annual",
              type=click.Choice(["annual", "quarterly", "ytd"]), show_default=True)
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option)
@output_options
@click.pass_context
def reconstruct(ctx, identifier, metric, period_type, as_of, markdown, json_output, ndjson):
    """Reconstruct derived line items SEC does not tag directly (EBITDA, FCF, ...)."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.reconstruct(identifier, metric, period_type=period_type, as_of=as_of)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson([result])
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    body = "\n".join([
        f"Metric: {result.get('metric')}",
        f"Value: {_format_value(result.get('value'))}",
        f"Formula: {result.get('formula', '')}",
        f"Inputs: {len(result.get('inputs', []))} sources",
        f"Caveats: {'; '.join(result.get('caveats', [])) or 'none'}",
        f"Missing inputs: {', '.join(result.get('missing_inputs', [])) or 'none'}",
    ])
    console.print(Panel(body, title=f"Reconstruct: {result.get('name', identifier)} {metric}", expand=False))


@main.command()
@click.argument("identifiers", nargs=-1, required=True)
@output_options
@click.pass_context
def resolve(ctx, identifiers, markdown, json_output, ndjson):
    """Batch resolve tickers/CIKs/names with ambiguity metadata.

    Accepts plain identifiers and `@group` expressions (e.g. `@dow30`).
    """
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.resolve(list(identifiers))
    result = _finalize(client, result)
    if ndjson:
        _ndjson(result.get("results", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = [[r.get("identifier", ""), r.get("ticker", ""), r.get("cik", ""),
             r.get("name", ""), r.get("error", "")]
            for r in result.get("results", [])]
    headers = ["Input", "Ticker", "CIK", "Name", "Error"]
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table("Resolve", headers, rows))


@main.command()
@click.argument("identifier_a")
@click.argument("identifier_b")
@click.option("--concept", "-c", required=True)
@click.option("--periods", "-n", default=4, show_default=True, type=PositiveInt)
@click.option("--period-type", default="annual",
              type=click.Choice(["annual", "quarterly", "ytd", "instant"]), show_default=True)
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option)
@output_options
@click.pass_context
def diff(ctx, identifier_a, identifier_b, concept, periods, period_type, as_of,
         markdown, json_output, ndjson):
    """Side-by-side diff of one concept across two filers, period-aligned."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.diff_concept(identifier_a, identifier_b, concept,
                                 period_type=period_type, periods=periods, as_of=as_of)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("rows", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    def _period_str(p):
        if not p or p == ["", ""]:
            return ""
        return f"{p[0]}..{p[1]}" if p[0] else p[1]
    rows = [[r.get("frame", ""),
             _period_str(r.get("a_period")), _period_str(r.get("b_period")),
             _format_value(r.get("a_value")), _format_value(r.get("b_value")),
             _format_value(r.get("delta")),
             f"{r['ratio']:.2f}x" if r.get("ratio") is not None else ""]
            for r in result.get("rows", [])]
    headers = ["Frame",
               f"{result['a']['identifier']} period", f"{result['b']['identifier']} period",
               f"{result['a']['identifier']}", f"{result['b']['identifier']}",
               "Delta (A-B)", "Ratio (A/B)"]
    title = f"Diff: {concept} ({period_type})"
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command()
@click.argument("command_name", required=False)
@output_options
def schema(command_name, markdown, json_output, ndjson):
    """Print a JSON schema describing the output of a command (or list known schemas)."""
    schemas = _output_schemas()
    if not command_name:
        payload = {
            "available": sorted(schemas),
            "primary_row_keys": {c: PRIMARY_ROW_KEYS[c] for c in sorted(PRIMARY_ROW_KEYS)},
        }
        if ndjson:
            _ndjson([payload])
            return
        _json(payload)
        return
    if command_name not in schemas:
        click.echo(f"Unknown command schema: {command_name}", err=True)
        click.echo(f"Available: {', '.join(sorted(schemas))}", err=True)
        raise click.exceptions.Exit(2)
    if ndjson:
        _ndjson([schemas[command_name]])
        return
    _json(schemas[command_name])


# Primary list-of-rows field per data command, so agents can find the payload
# without sampling live output. None = composite/scalar envelope.
PRIMARY_ROW_KEYS: dict = {
    "search-companies": "companies",
    "company": "filings",
    "filings": "filings",
    "facts": "concepts",
    "concept": "facts",
    "frame": "facts",
    "exhibits": "documents",
    "earnings": "highlights",
    "events": "events",
    "compare": "companies",
    "metrics": "metrics",
    "brief": "metrics",
    "dei": None,
    "peers": "peers",
    "concept-info": "candidates",
    "tags": "matches",
    "frames": "frames",
    "ttm": "metrics",
    "ratios": "ratios",
    "trend": "facts",
    "growth": "growth",
    "reconstruct": None,
    "resolve": "results",
    "diff": "rows",
    "search": "matches",
    "dashboard": None,
    "governance": "proposals",
    "item": None,
    "holdings": "rows",
    "holders": "rows",
    "insiders": "transactions",
    "quality": "flags",
    "verify": "checks",
    "statements": "lines",
    "audit-trail": "facts",
    "amendments": "chains",
    "delta": "new_filings",
    "pending": "subscriptions",
    "bulk-urls": "archives",
}


def _output_schemas() -> dict:
    """Return JSON schemas for every data command's output.

    Schemas are intentionally pragmatic: they describe top-level shape and the
    key fields agents will read. They are not exhaustive — agents should treat
    them as a contract over named fields, not a closed-world spec. Commands
    without a hand-written schema get a coarse envelope schema (marked
    `"coarse": true`) so `edgar schema CMD` answers for every command. Each
    schema carries `primary_row_key` naming its main list-of-rows field.
    """
    fact_schema = {
        "type": "object",
        "properties": {
            "val": {"type": ["number", "string"]},
            "start": {"type": "string"}, "end": {"type": "string"},
            "filed": {"type": "string"}, "form": {"type": "string"},
            "accn": {"type": "string"}, "accession": {"type": "string"},
            "unit": {"type": "string"},
            "period_type": {"type": "string", "enum": ["annual", "quarterly", "ytd", "instant"]},
            "period_length_days": {"type": "integer"},
            "fiscal_period": {"type": "string"}, "calendar_period": {"type": "string"},
            "source_url": {"type": "string"}, "as_of": {"type": "string"},
            "is_restated": {"type": "boolean"}, "is_cumulative": {"type": "boolean"},
            "superseded_by": {"type": ["string", "null"]},
        },
    }
    envelope_meta = {
        "schema_version": {"type": "string"},
        "cli_version": {"type": "string"},
        "cache": {
            "type": "object",
            "properties": {
                "calls": {"type": "integer"}, "hits": {"type": "integer"},
                "misses": {"type": "integer"}, "age_max_seconds": {"type": "integer"},
                "ttl_min_remaining": {"type": "integer"},
                "last_key": {"type": "string"}, "last_hit": {"type": "boolean"},
                "last_etag": {"type": ["string", "null"]},
            },
        },
    }
    filing_row_schema = {
        "type": "object",
        "description": ("SEC camelCase passthrough plus snake_case aliases "
                        "(added in schema_version 1.1.0)"),
        "properties": {
            "form": {"type": "string"},
            "accession": {"type": "string"}, "accessionNumber": {"type": "string"},
            "filed": {"type": "string"}, "filingDate": {"type": "string"},
            "report_date": {"type": "string"}, "reportDate": {"type": "string"},
            "primary_document": {"type": "string"}, "primaryDocument": {"type": "string"},
            "items": {"type": "string"},
            "filing_url": {"type": "string"}, "primary_doc_url": {"type": "string"},
        },
    }
    schemas = {
        "concept": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                **envelope_meta,
                "cik": {"type": "string"}, "name": {"type": "string"},
                "taxonomy": {"type": "string"}, "tag": {"type": "string"},
                "label": {"type": "string"}, "description": {"type": "string"},
                "as_of": {"type": ["string", "null"]},
                "facts": {"type": "array", "items": fact_schema},
                "total": {"type": "integer"},
                "alias": {"type": ["string", "null"]},
                "candidate_tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["cik", "facts"],
        },
        "metrics": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                **envelope_meta,
                "cik": {"type": "string"}, "ticker": {"type": "string"},
                "name": {"type": "string"}, "reference_date": {"type": "string"},
                "metrics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "metric": {"type": "string"}, "tag": {"type": "string"},
                            "fact": fact_schema, "age_days": {"type": ["integer", "null"]},
                            "stale": {"type": "boolean"}, "error": {"type": "string"},
                        },
                    },
                },
            },
        },
        "ratios": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                **envelope_meta,
                "cik": {"type": "string"}, "name": {"type": "string"},
                "period_type": {"type": "string"},
                "ratios": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "metric": {"type": "string"},
                            "value": {"type": ["number", "null"]},
                            "formula": {"type": "string"},
                            "inputs": {"type": "array"},
                            "caveats": {"type": "array", "items": {"type": "string"}},
                            "missing_inputs": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "not_applicable": {"type": "array"},
            },
        },
        "filings": {
            "type": "object",
            "properties": {
                **envelope_meta,
                "filings": {"type": "array", "items": filing_row_schema},
                "warning": {"type": "string"},
            },
        },
        "events": {
            "type": "object",
            "properties": {**envelope_meta, "events": {"type": "array"}},
        },
        "delta": {
            "type": "object",
            "properties": {
                **envelope_meta,
                "since": {"type": ["string", "null"]},
                "new_filings": {"type": "array"},
                "restated_facts": {"type": "array"},
                "summary": {"type": "object"},
            },
        },
    }
    for command, row_key in PRIMARY_ROW_KEYS.items():
        if command in schemas:
            schemas[command]["primary_row_key"] = row_key
            continue
        properties = dict(envelope_meta)
        if row_key:
            items = filing_row_schema if row_key == "filings" else {"type": "object"}
            properties[row_key] = {"type": "array", "items": items}
        schemas[command] = {
            "type": "object",
            "properties": properties,
            "primary_row_key": row_key,
            "coarse": True,
        }
    return schemas


@main.command()
@click.argument("identifiers", nargs=-1, required=True)
@click.option("--to", "db_path", type=click.Path(dir_okay=False, path_type=Path), required=True,
              help="Path to the SQLite database (created if missing)")
@click.option("--no-facts", is_flag=True, help="Skip companyfacts ingestion (mirror submissions only)")
@click.option("--documents-for", default=None,
              help="Also ingest filing-index documents for the latest 5 filings of this form (e.g. 10-K)")
@click.option("--with-bodies-for", default=None,
              help="Also fetch and FTS-index plain-text filing bodies for this form (e.g. 10-K)")
@click.option("--bodies-limit", default=20, show_default=True, type=PositiveInt,
              help="Max filings to fetch bodies for per filer (per --with-bodies-for run)")
@output_options
@click.pass_context
def mirror(ctx, identifiers, db_path, no_facts, documents_for,
           with_bodies_for, bodies_limit, markdown, json_output, ndjson):
    """Mirror filer submissions + facts to a local SQLite database (incremental)."""
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    expanded = _expand_groups(list(identifiers), client=client)
    summaries = []
    for ident in expanded:
        result = client.mirror_filer(
            ident, db_path=str(db_path), include_facts=not no_facts,
            include_documents_for_form=documents_for,
            with_bodies_for_form=with_bodies_for,
            bodies_limit=bodies_limit,
        )
        summaries.append(result)
    payload = client._envelope({"results": summaries, "db_path": str(db_path),
                                 "total": len(summaries)})
    if ndjson:
        _ndjson(summaries)
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(payload)
        return
    rows = [[r.get("ticker") or r.get("identifier", ""), r.get("name", ""),
             r.get("filings_inserted", "—"), r.get("facts_inserted", "—"),
             r.get("docs_inserted", "—"), r.get("error", "")]
            for r in summaries]
    headers = ["Ticker", "Name", "Filings+", "Facts+", "Docs+", "Error"]
    title = f"Mirror to {db_path}"
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command()
@click.argument("query")
@click.option("--db", "db_path", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Search the local SQLite mirror at this path (uses FTS5)")
@click.option("--form", default=None, help="Restrict to a specific form")
@click.option("--since", default=None, callback=_validate_date_option,
              help="Only filings on/after YYYY-MM-DD")
@click.option("--until", default=None, callback=_validate_date_option,
              help="Only filings on/before YYYY-MM-DD (live SEC EFTS only)")
@click.option("--tickers", default=None,
              help="Comma-separated identifiers (or @group) to scope the search")
@click.option("--mode", default="auto", show_default=True,
              type=click.Choice(["auto", "bodies", "metadata"]),
              help="Mirror search mode: bodies (filing text), metadata (form/items), or auto")
@click.option("--limit", "-n", default=25, show_default=True, type=PositiveInt)
@output_options
@click.pass_context
def search(ctx, query, db_path, form, since, until, tickers, mode, limit,
           markdown, json_output, ndjson):
    """Full-text search filings.

    With `--db PATH` searches the local SQLite mirror via FTS5. If filing
    bodies have been ingested (`mirror --with-bodies-for FORM`), `--mode bodies`
    or the default `auto` searches filing text. `--mode metadata` searches
    form/items/description only. Without `--db`, queries SEC's live EDGAR
    Full-Text Search.
    """
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    cik_list = None
    if tickers:
        ids = _expand_groups(_split_input_values(tickers), client=client)
        cik_list = []
        for ident in ids:
            company = client.resolve_company(ident)
            if "error" not in company:
                cik_list.append(company["cik"])
    if db_path:
        result = client.search_mirror(str(db_path), query, form=form, since=since,
                                       ciks=cik_list, limit=limit, mode=mode)
    else:
        primary_cik = cik_list[0] if cik_list else None
        result = client.search_efts(query, form=form, since=since, until=until,
                                     cik=primary_cik, limit=limit)
    result = _finalize(client, result)
    _error_exit(result)
    matches = result.get("matches", [])
    if ndjson:
        _ndjson(matches)
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = []
    for m in matches:
        rows.append([
            m.get("filed", ""), m.get("form", ""),
            (m.get("display_names") or [m.get("name", "")])[0] if isinstance(m.get("display_names"), list) else m.get("name", ""),
            m.get("accession", ""),
            (m.get("highlight") or [m.get("description", "")])[0] if m.get("highlight") else (m.get("description") or "")[:80],
        ])
    headers = ["Filed", "Form", "Filer", "Accession", "Match"]
    title = f"Search: {query!r}" + (" (mirror)" if db_path else " (live EFTS)")
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command()
@click.argument("identifier")
@output_options
@click.pass_context
def dashboard(ctx, identifier, markdown, json_output, ndjson):
    """One-call composite snapshot: profile + metrics + ratios + events + quality."""
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.dashboard(identifier)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson([result])
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    profile = result.get("profile", {})
    body = "\n".join([
        f"CIK: {result.get('cik', '')}",
        f"SIC: {profile.get('sic', '')} {profile.get('sicDescription', '')}",
        f"Fiscal year end: {profile.get('fiscalYearEnd', '')}",
        f"Exchanges: {', '.join(profile.get('exchanges', []))}",
        f"Latest filings: {len(profile.get('latest_filings', []))}",
        f"Metrics: {len(result.get('metrics', []))}",
        f"Ratios: {len(result.get('ratios', []))}",
        f"Recent events: {len(result.get('events', []))}",
        f"Quality flags: {result.get('quality', {}).get('flagged_count', 0)} of {len(result.get('quality', {}).get('flags', []))}",
    ])
    console.print(Panel(body, title=f"Dashboard: {result.get('name', '')}", expand=False))


@main.command()
@click.argument("identifier")
@click.option("--year", type=int, default=None,
              help="Pick the DEF 14A from this filing year (defaults to most recent)")
@click.option("--db", "db_path", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Read body from a mirror SQLite if present (otherwise fetch live)")
@output_options
@click.pass_context
def governance(ctx, identifier, year, db_path, markdown, json_output, ndjson):
    """Heuristic DEF 14A extraction: audit fees, board size, shareholder proposals."""
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.governance(
        identifier, year=year,
        db_path=str(db_path) if db_path else None,
    )
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        for fee in result.get("audit_fees", []):
            click.echo(json.dumps({"kind": "audit_fee", **fee}, default=str))
        for prop in result.get("proposals", []):
            click.echo(json.dumps({"kind": "proposal", **prop}, default=str))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    body_lines = [
        f"Filing: {result['filing']['form']} {result['filing']['filed']}",
        f"Body source: {result.get('body_source', '')}  length: {result.get('body_length', 0)}",
        "",
    ]
    if result.get("audit_fees"):
        body_lines.append("Audit fees:")
        for fee in result["audit_fees"][:6]:
            body_lines.append(f"  {fee['label']:30s} ${fee['value_usd']:,}")
    if result.get("board_size"):
        body_lines.append(f"\nBoard size: {result['board_size']['count']} directors")
    if result.get("proposals"):
        body_lines.append(f"\nProposals ({len(result['proposals'])}):")
        for p in result["proposals"][:8]:
            body_lines.append(f"  Proposal {p['number']}: {p['title'][:60]}")
    if result.get("neo_titles_mentioned"):
        body_lines.append(f"\nExecutive titles mentioned: "
                          f"{', '.join(result['neo_titles_mentioned'][:5])}")
    console.print(Panel("\n".join(body_lines),
                        title=f"Governance: {result.get('name', identifier)}",
                        expand=False))


@main.command()
@click.argument("identifier")
@click.option("--form", default="10-K", show_default=True,
              type=click.Choice(["10-K", "10-Q"]))
@click.option("--section", "-s", required=True,
              help="Item code (e.g. 1A) or title (e.g. 'Risk Factors')")
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option,
              help="Use the latest filing on/before YYYY-MM-DD")
@click.option("--db", "db_path", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Read body from a mirror SQLite if present (otherwise fetch live)")
@click.option("--max-chars", default=50000, show_default=True, type=PositiveInt,
              help="Truncate the section text at this many characters")
@output_options
@click.pass_context
def item(ctx, identifier, form, section, as_of, db_path, max_chars,
         markdown, json_output, ndjson):
    """Extract one Item-level section from a 10-K or 10-Q (heuristic)."""
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.filing_section(
        identifier, form=form, section=section, as_of=as_of,
        db_path=str(db_path) if db_path else None, max_chars=max_chars,
    )
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson([result.get("section", {})])
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    s = result.get("section", {})
    body = "\n".join([
        f"Filing: {result['filing']['form']} {result['filing']['filed']}",
        f"Item {s.get('item')}: {s.get('title')}",
        f"Length: {s.get('length')} chars  Confidence: {s.get('confidence')}",
        f"Items found in document: {', '.join(s.get('items_found_in_document', []))}",
        f"Body source: {result.get('body_source', '')}",
        "",
        (s.get("text") or "")[:2000] + ("…" if s.get("truncated_to_max_chars") else ""),
    ])
    console.print(Panel(body,
                        title=f"{result.get('name', identifier)} {form} · {section}",
                        expand=False))


@main.command()
@click.argument("identifier")
@click.option("--quarter", default=None,
              help="Quarter (e.g. 2025Q4 or CY2025Q4); defaults to most recent 13F-HR")
@click.option("--top", "top_n", default=50, show_default=True, type=PositiveInt,
              help="Top-N positions to return (sorted by value)")
@output_options
@click.pass_context
def holdings(ctx, identifier, quarter, top_n, markdown, json_output, ndjson):
    """Show one institutional filer's 13F-HR holdings for a quarter."""
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.holdings(identifier, quarter=quarter, top_n=top_n)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("top_positions", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    body = "\n".join([
        f"Filing: {result['filing']['form']} {result['filing']['filed']} "
        f"({result['filing']['accession']})",
        f"Total value: ${_format_value(result.get('total_value_usd'))} "
        f"({result.get('position_count', 0)} positions)",
        f"Top-{top_n} concentration: {result.get('top_concentration', 0)*100:.1f}%",
    ])
    console.print(Panel(body, title=f"Holdings: {result.get('name', identifier)}", expand=False))
    rows = []
    for p in result.get("top_positions", [])[:25]:
        rows.append([
            p["name_of_issuer"][:40],
            p.get("cusip", ""),
            f"{p['weight']*100:.2f}%",
            _format_value(p.get("value_usd")),
            f"{p['shares']:,}",
            p.get("put_call", ""),
        ])
    headers = ["Issuer", "CUSIP", "Weight", "Value", "Shares", "P/C"]
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table("Top positions", headers, rows))


@main.command()
@click.argument("identifier")
@click.option("--candidates", default="@13f-top",
              help="Tickers/@group of 13F filers to scan (default @13f-top — a curated set of major institutional filers; pass explicit CIKs for broader coverage)")
@click.option("--cusip", default=None,
              help="Match holdings by CUSIP (more precise than name-substring)")
@click.option("--quarter", default=None,
              help="Quarter (e.g. 2025Q4); defaults to each candidate's most recent 13F-HR")
@click.option("--top", "top_n", default=25, show_default=True, type=PositiveInt)
@click.option("--max-filers", default=30, show_default=True, type=PositiveInt,
              help="Hard cap on candidate filers scanned")
@output_options
@click.pass_context
def holders(ctx, identifier, candidates, cusip, quarter, top_n, max_filers,
            markdown, json_output, ndjson):
    """Find which institutional filers (from a candidate set) hold an issuer.

    Matches on `nameOfIssuer` substring by default; pass `--cusip` for exact
    matching. Without a 13F-filer candidate list this is bounded — pass
    `--candidates @group` or a comma-separated list of CIKs.
    """
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    candidate_list = _expand_groups(_split_input_values(candidates), client=client)
    result = client.holders(identifier, candidates=candidate_list,
                             quarter=quarter, top_n=top_n, cusip=cusip,
                             max_filers=max_filers)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("rows", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    body = "\n".join([
        f"Issuer: {result.get('issuer_name', '')} (CIK {result.get('issuer_cik', '')})",
        f"Match: {result.get('match_strategy')} '{result.get('needle')}'",
        f"Candidates scanned: {result.get('candidates_scanned')} of {result.get('candidates_total')}",
        f"Filers holding: {result.get('filer_count', 0)}",
        f"Total shares: {_format_value(result.get('total_shares'))}",
        f"Total value: ${_format_value(result.get('total_value_usd'))}",
    ])
    console.print(Panel(body, title=f"Holders of {identifier}", expand=False))
    rows = []
    for f in result.get("filers", [])[:25]:
        rows.append([
            f["filer_name"][:40],
            f.get("filer_cik", ""),
            f["positions"],
            _format_value(f.get("shares")),
            _format_value(f.get("value_usd")),
        ])
    headers = ["Filer", "CIK", "#Pos", "Shares", "Value"]
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table("Holders", headers, rows))


@main.command()
@click.argument("identifier")
@click.option("--since", default=None, callback=_validate_date_option,
              help="Only Form 4 filings on/after YYYY-MM-DD")
@click.option("--limit", "-n", default=50, show_default=True, type=PositiveInt,
              help="Max transactions to return after aggregation")
@click.option("--max-fetch", default=50, show_default=True, type=PositiveInt,
              help="Max Form 4 filings to download (hard ceiling 200)")
@output_options
@click.pass_context
def insiders(ctx, identifier, since, limit, max_fetch, markdown, json_output, ndjson):
    """Aggregate Form 4 insider transactions for a filer."""
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.insiders(identifier, since=since, limit=limit,
                              max_form4_fetches=max_fetch)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("transactions", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    s = result.get("summary", {})
    body = "\n".join([
        f"Form 4 filings parsed: {result.get('form4s_fetched', 0)}"
        f"  failed: {result.get('form4s_failed', 0)}",
        f"Transactions: {result.get('transactions_total', 0)}",
        f"Acquired: {_format_value(s.get('acquired_shares'))} shares "
        f"(${_format_value(s.get('acquired_value'))})",
        f"Disposed: {_format_value(s.get('disposed_shares'))} shares "
        f"(${_format_value(s.get('disposed_value'))})",
        f"Net: {_format_value(s.get('net_shares'))} shares "
        f"(${_format_value(s.get('net_value'))})",
    ])
    console.print(Panel(body, title=f"Insiders: {result.get('name', identifier)}", expand=False))
    rows = []
    for ins in result.get("insiders", [])[:10]:
        rows.append([
            ins["name"], ins.get("title", ""),
            "D" if ins.get("is_director") else "",
            "O" if ins.get("is_officer") else "",
            ins["transactions"],
            _format_value(ins.get("acquired_shares")),
            _format_value(ins.get("disposed_shares")),
            _format_value(ins.get("acquired_value") - ins.get("disposed_value")),
        ])
    headers = ["Insider", "Title", "Dir", "Off", "#Tx", "Acq Sh", "Disp Sh", "Net $"]
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table("Insider rollup", headers, rows))


@main.command()
@click.argument("identifier")
@click.option("--period-type", default="annual",
              type=click.Choice(["annual", "quarterly", "ytd"]), show_default=True)
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option)
@output_options
@click.pass_context
def quality(ctx, identifier, period_type, as_of, markdown, json_output, ndjson):
    """Earnings-quality flags: accruals, OpCF/NI divergence, AR creep, SBC, restatements."""
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.quality(identifier, period_type=period_type, as_of=as_of)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("flags", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = []
    for f in result.get("flags", []):
        v = f.get("value")
        rows.append([
            f["flag"],
            f"{v:.4f}" if isinstance(v, float) else (str(v) if v is not None else "—"),
            "FLAG" if f.get("flagged") else "",
            f.get("threshold", ""),
            f.get("formula", "")[:60],
        ])
    headers = ["Flag", "Value", "Status", "Threshold", "Formula"]
    title = (f"Quality: {result.get('name', identifier)} "
             f"({result.get('flagged_count', 0)}/{len(result.get('flags', []))} flagged)")
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command()
@click.argument("identifier")
@click.option("--period-type", default="annual",
              type=click.Choice(["annual", "quarterly", "ytd"]), show_default=True)
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option)
@output_options
@click.pass_context
def verify(ctx, identifier, period_type, as_of, markdown, json_output, ndjson):
    """Cross-statement consistency checks (EPS↔NI/shares, GP↔Rev−COGS, ...)."""
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.verify(identifier, period_type=period_type, as_of=as_of)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("checks", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = [[c["check"], "PASS" if c.get("passed") else "FAIL",
             _format_value(c.get("expected")), _format_value(c.get("actual")),
             _format_value(c.get("delta")), c.get("formula", "")[:60]]
            for c in result.get("checks", [])]
    headers = ["Check", "Status", "Expected", "Actual", "Delta", "Formula"]
    title = (f"Verify: {result.get('name', identifier)} "
             f"({result.get('passed', 0)}/{result.get('total', 0)} passed)")
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command()
@click.argument("identifier")
@click.option("--statement", default="income", show_default=True,
              type=click.Choice(["income", "balance", "cash"]))
@click.option("--period-type", default="annual",
              type=click.Choice(["annual", "quarterly", "ytd"]), show_default=True)
@click.option("--as-of", "as_of", default=None, callback=_validate_date_option)
@output_options
@click.pass_context
def statements(ctx, identifier, statement, period_type, as_of, markdown, json_output, ndjson):
    """Compose a normalized financial statement (income/balance/cash flow)."""
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.statement(identifier, statement=statement,
                              period_type=period_type, as_of=as_of)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("lines", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = [[l["line"], _format_value(l.get("value"), l.get("unit", "")),
             l.get("tag", "—") or "—",
             l.get("end", "—") or l.get("start", "—") or "—",
             "yes" if l.get("is_restated") else "",
             l.get("error", "missing" if l.get("missing") else "")]
            for l in result.get("lines", [])]
    headers = ["Line", "Value", "Tag", "Period End", "Restated", "Note"]
    title = (f"{statement.title()} statement: {result.get('name', identifier)} "
             f"({result.get('period_end') or 'latest'}, "
             f"coverage {int(result.get('coverage', 0)*100)}%)")
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command("audit-trail")
@click.argument("identifier")
@click.option("--concept", "-c", required=True, help="Concept alias (e.g. revenue, net_income)")
@click.option("--period", default=None, help="Calendar period frame (e.g. CY2024)")
@click.option("--start", "period_start", default=None, help="Period start YYYY-MM-DD")
@click.option("--end", "period_end", default=None, help="Period end YYYY-MM-DD")
@output_options
@click.pass_context
def audit_trail_cmd(ctx, identifier, concept, period, period_start, period_end,
                    markdown, json_output, ndjson):
    """Show every filing that reported a fact, with restatement detection."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.audit_trail(identifier, concept, period=period,
                                period_start=period_start, period_end=period_end)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("facts", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = [[f.get("filed", ""), f.get("end", ""), f.get("calendar_period", ""),
             _format_value(f.get("val", ""), f.get("unit", "")),
             f.get("source_tag") or f.get("tag", ""), f.get("accn", "")]
            for f in result.get("facts", [])]
    headers = ["Filed", "Period End", "Frame", "Value", "Tag", "Accession"]
    title = f"Audit trail: {result.get('name', '')} {concept}"
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))
    restated = result.get("restated_periods", [])
    if restated:
        console.print(f"\n[bold yellow]Restated periods: {len(restated)}[/bold yellow]")
        for r in restated[:5]:
            console.print(f"  {r['start']}..{r['end']}: values seen = {r['values_seen']}")


@main.command()
@click.argument("identifier")
@click.option("--since", default=None, callback=_validate_date_option,
              help="Only filings on/after YYYY-MM-DD")
@click.option("--limit", "-n", default=50, show_default=True, type=PositiveInt)
@output_options
@click.pass_context
def amendments(ctx, identifier, since, limit, markdown, json_output, ndjson):
    """Pair primary filings with their /A amendments."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    result = client.amendments(identifier, since=since, limit=limit)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("chains", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    rows = []
    for chain in result.get("chains", []):
        amd = chain["amendment"]
        prim = chain.get("primary") or {}
        rows.append([amd.get("filingDate", ""), amd.get("form", ""), amd.get("accessionNumber", ""),
                     prim.get("filingDate", "—"), prim.get("form", "—"), prim.get("accessionNumber", "—")])
    headers = ["Amend Filed", "Amend Form", "Amend Accession", "Primary Filed", "Primary Form", "Primary Accession"]
    title = f"Amendments: {result.get('name', '')}"
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table(title, headers, rows))


@main.command()
@click.argument("identifier")
@click.option("--since", default=None, callback=_validate_date_option,
              help="Diff scope start (YYYY-MM-DD)")
@click.option("--use-state/--no-state", default=False,
              help="Use ~/.edgar/state.json high-water mark (auto-advance on success)")
@output_options
@click.pass_context
def delta(ctx, identifier, since, use_state, markdown, json_output, ndjson):
    """Show new filings and detected restatements since a date."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    state_store = StateStore() if use_state else None
    result = client.delta(identifier, since=since, state_store=state_store)
    result = _finalize(client, result)
    _error_exit(result)
    if ndjson:
        for nf in result.get("new_filings", []):
            click.echo(json.dumps({"kind": "new_filing", **nf}, default=str))
        for rf in result.get("restated_facts", []):
            click.echo(json.dumps({"kind": "restated", **rf}, default=str))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return
    summary = result.get("summary", {})
    console.print(Panel(
        f"New filings: {summary.get('new_filings', 0)}\n"
        f"Restated periods detected: {summary.get('restated_periods', 0)}\n"
        f"Since: {result.get('since') or 'state high-water'}",
        title=f"Delta: {result.get('name', '')}", expand=False,
    ))
    rows = [[f.get("filingDate"), f.get("form"), f.get("accessionNumber")]
            for f in result.get("new_filings", [])[:20]]
    if rows:
        console.print(_simple_table("New filings (top 20)", ["Filed", "Form", "Accession"], rows))


@main.group()
def subscribe():
    """Manage filing subscriptions in ~/.edgar/state.json."""


@subscribe.command("add")
@click.argument("identifier")
@click.option("--form", default=None, help="Restrict to a specific form")
@click.pass_context
def subscribe_add(ctx, identifier, form):
    """Subscribe to a (filer, form) pair."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    company = client.resolve_company(identifier)
    _error_exit(company)
    store = StateStore()
    entry = store.subscribe(company["cik"], form)
    click.echo(json.dumps(entry, default=str))


@subscribe.command("remove")
@click.argument("identifier")
@click.option("--form", default=None)
@click.pass_context
def subscribe_remove(ctx, identifier, form):
    """Unsubscribe from a (filer, form) pair."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    company = client.resolve_company(identifier)
    _error_exit(company)
    removed = StateStore().unsubscribe(company["cik"], form)
    click.echo(f"removed: {removed}")


@subscribe.command("list")
def subscribe_list():
    """List all current subscriptions."""
    subs = StateStore().subscriptions()
    _json({"subscriptions": subs, "total": len(subs)})


@main.command()
@click.option("--ndjson", "ndjson_out", is_flag=True, help="Stream new filings as NDJSON")
@click.pass_context
def pending(ctx, ndjson_out):
    """Drain new filings for every active subscription (advances high-water marks)."""
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    store = StateStore()
    subs = store.subscriptions()
    out_subs = []
    for sub in subs:
        cik = sub.get("cik", "")
        form = None if sub.get("form") in (None, "*") else sub.get("form")
        result = client.submissions(cik, limit=50, form=form,
                                     since_last_fetch=True, state_store=store)
        new = result.get("filings", []) if "error" not in result else []
        if ndjson_out:
            for f in new:
                click.echo(json.dumps({
                    "subscription": sub.get("key"), "cik": cik, "form": form or "*",
                    **f,
                }, default=str))
        else:
            out_subs.append({"subscription": sub.get("key"), "new_filings": new,
                             "count": len(new)})
    if not ndjson_out:
        _json({"subscriptions": out_subs, "total": len(subs)})


@main.command("mark-seen")
@click.argument("identifier")
@click.argument("accession")
@click.option("--form", default=None)
@click.option("--filed", default=None, callback=_validate_date_option,
              help="Filing date YYYY-MM-DD (defaults to today)")
@click.pass_context
def mark_seen(ctx, identifier, accession, form, filed):
    """Manually advance the high-water mark for a (filer, form) subscription."""
    from datetime import date as _date
    client = get_client(use_cache=not ctx.obj["no_cache"], cache_max_mb=ctx.obj.get("cache_max_mb"))
    company = client.resolve_company(identifier)
    _error_exit(company)
    StateStore().mark_seen(
        company["cik"], form, accession, filed or _date.today().isoformat(),
    )
    click.echo("ok")


@main.command("bulk-urls")
@output_options
def bulk_urls(markdown, json_output, ndjson):
    """List official SEC nightly bulk archive URLs."""
    result = {"archives": BULK_ARCHIVES}
    if ndjson:
        _ndjson(result["archives"])
        return
    if _wants_json(json_output, markdown, ndjson):
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


def _cache_for_management(ctx) -> "EdgarCache":  # noqa: F821
    """Build an EdgarCache that honors the global --cache-max-mb flag."""
    from edgar.cache import EdgarCache

    max_mb = ctx.obj.get("cache_max_mb") if ctx.obj else None
    max_bytes = int(max_mb) * 1024 * 1024 if max_mb else None
    return EdgarCache(cache_dir="~/.edgar_cache", max_bytes=max_bytes)


@main.command("clear-cache")
@click.pass_context
def clear_cache(ctx):
    """Clear the local EDGAR cache."""
    count = _cache_for_management(ctx).clear()
    click.echo(f"Cleared {count} cached entries.")


@main.group()
def cache():
    """Inspect and manage the local EDGAR cache."""


@cache.command("stats")
@output_options
@click.pass_context
def cache_stats(ctx, markdown, json_output, ndjson):
    """Show cache size, fresh/expired counts, and configuration."""
    stats = _cache_for_management(ctx).stats()
    if ndjson:
        _ndjson([stats])
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(stats)
        return
    rows = [[k, str(v)] for k, v in stats.items() if k != "endpoint_ttls"]
    for entry in stats.get("endpoint_ttls", []):
        rows.append([f"ttl {entry['pattern']}", f"{entry['ttl_seconds']}s"])
    if markdown:
        click.echo(_markdown_table(["Field", "Value"], rows))
        return
    console.print(_simple_table("Cache Stats", ["Field", "Value"], rows))


@cache.command("invalidate")
@click.argument("pattern")
@click.pass_context
def cache_invalidate(ctx, pattern):
    """Delete cached entries whose URL matches a glob (e.g. '*CIK0000320193*')."""
    removed = _cache_for_management(ctx).invalidate(pattern)
    click.echo(f"Invalidated {removed} cached entries matching {pattern!r}.")


@cache.command("warm")
@click.option("--tickers", default=None, help="Comma-separated identifiers to warm submissions+facts for")
@click.option("--input", "input_file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=None, help="Read identifiers from a file")
@click.pass_context
def cache_warm(ctx, tickers, input_file):
    """Pre-fetch submissions and companyfacts for a list of identifiers."""
    from edgar.api import COMPANY_TICKERS_URL

    identifiers = _collect_identifiers(None, tickers, input_file, batch=False) if (tickers or input_file) else []
    if not identifiers:
        raise click.UsageError("Provide --tickers or --input")
    client = get_client(use_cache=not ctx.obj["no_cache"],
                       cache_max_mb=ctx.obj.get("cache_max_mb"))
    cache_obj = client.edgar_cache
    urls = [COMPANY_TICKERS_URL]
    for ident in identifiers:
        company = client.resolve_company(ident)
        if "error" in company:
            click.echo(f"skip {ident}: {company['error']}", err=True)
            continue
        cik = company["cik"]
        urls.append(f"https://data.sec.gov/submissions/CIK{cik}.json")
        urls.append(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")

    def fetch(url):
        return client._get(url)

    summary = cache_obj.warm(urls, fetch)
    _json(summary)


def _output_companies(result: dict, json_output: bool, markdown: bool, ndjson: bool = False) -> None:
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("companies", []))
        return
    if _wants_json(json_output, markdown, ndjson):
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


def _output_company(result: dict, json_output: bool, markdown: bool, ndjson: bool = False,
                    show_urls: bool = False) -> None:
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("filings", []))
        return
    if _wants_json(json_output, markdown, ndjson):
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
        _output_filings(result, "Recent Filings", False, True, False, show_urls=show_urls)
        return

    body = "\n".join([
        f"CIK: {result.get('cik', '')}",
        f"Tickers: {', '.join(result.get('tickers', []))}",
        f"Exchanges: {', '.join(result.get('exchanges', []))}",
        f"SIC: {result.get('sic', '')} {result.get('sicDescription', '')}".strip(),
        f"Fiscal year end: {result.get('fiscalYearEnd', '')}",
    ])
    console.print(Panel(body, title=result.get("name", ""), expand=False))
    _output_filings(result, "Recent Filings", False, False, False, show_urls=show_urls)


def _output_filings(result: dict, title: str, json_output: bool, markdown: bool,
                    ndjson: bool = False, show_urls: bool = False) -> None:
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("filings", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        # Preserve envelope metadata (schema_version, cli_version, cache, warning, ...)
        # rather than emitting only a stripped-down dict, so agents always get
        # the documented envelope on JSON output.
        payload = {k: v for k, v in result.items() if k not in {"files", "history_files_checked"}}
        _json(payload)
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


def _output_concepts(result: dict, json_output: bool, markdown: bool, ndjson: bool = False) -> None:
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("concepts", []))
        return
    if _wants_json(json_output, markdown, ndjson):
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
                          ndjson: bool = False, deltas: bool = False) -> None:
    _error_exit(result)
    facts = _add_deltas(result.get("facts", [])) if deltas else result.get("facts", [])
    if ndjson:
        _ndjson(facts)
        return
    if _wants_json(json_output, markdown, ndjson):
        if deltas:
            result = dict(result)
            result["facts"] = facts
        _json(result)
        return

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


def _flatten_concept_batch(results: list[dict], deltas: bool = False) -> list[dict]:
    rows = []
    for result in results:
        if "error" in result:
            rows.append({"identifier": result.get("identifier", ""), "error": result.get("error", "")})
            continue
        facts = _add_deltas(result.get("facts", [])) if deltas else result.get("facts", [])
        for fact in facts:
            row = dict(fact)
            row.update({
                "identifier": result.get("identifier", ""),
                "cik": result.get("cik", ""),
                "name": result.get("name", ""),
                "taxonomy": result.get("taxonomy", ""),
                "tag": result.get("tag", ""),
            })
            rows.append(row)
    return rows


def _output_concept_batch(results: list[dict], json_output: bool, markdown: bool,
                          ndjson: bool = False, deltas: bool = False,
                          envelope: Optional[dict] = None) -> None:
    if _batch_failed(results):
        _error_exit(results[0])
    rows = _flatten_concept_batch(results, deltas=deltas)
    if ndjson:
        _ndjson(rows)
        return
    payload = dict(envelope) if envelope else {"results": results}
    payload.setdefault("results", results)
    if deltas:
        payload["facts"] = rows
    if _wants_json(json_output, markdown, ndjson):
        _json(payload)
        return

    table_rows = [
        [
            row.get("identifier", ""),
            row.get("name", ""),
            row.get("tag", ""),
            row.get("filed", ""),
            _format_period(row),
            row.get("frame", ""),
            _format_value(row.get("val", ""), row.get("unit", "")),
            row.get("error", ""),
        ]
        for row in rows
    ]
    headers = ["Identifier", "Company", "Tag", "Filed", "Period", "Frame", "Value", "Error"]
    if markdown:
        click.echo(_markdown_table(headers, table_rows))
        return
    console.print(_simple_table("Concept Batch", headers, table_rows))


def _output_documents(result: dict, json_output: bool, markdown: bool, ndjson: bool = False) -> None:
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("documents", []))
        return
    if _wants_json(json_output, markdown, ndjson):
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


def _output_earnings(result: dict, json_output: bool, markdown: bool, ndjson: bool = False) -> None:
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("highlights", []))
        return
    if _wants_json(json_output, markdown, ndjson):
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


def _output_events(result: dict, json_output: bool, markdown: bool, ndjson: bool = False) -> None:
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("events", []))
        return
    if _wants_json(json_output, markdown, ndjson):
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


def _output_compare(result: dict, json_output: bool, markdown: bool, ndjson: bool = False) -> None:
    _error_exit(result)
    if ndjson:
        rows = []
        for company in result.get("companies", []):
            for fact in company.get("facts", []):
                item = dict(fact)
                item["identifier"] = company.get("identifier", "")
                item["company"] = company.get("name", "")
                rows.append(item)
        _ndjson(rows)
        return
    if _wants_json(json_output, markdown, ndjson):
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


def _metric_rows(result: dict) -> list[list]:
    return [
        [
            metric.get("metric", ""),
            metric.get("tag", ""),
            _format_period(metric.get("fact", {})),
            _format_value(metric.get("fact", {}).get("val", ""), metric.get("unit", "")),
            _format_freshness(metric),
        ]
        for metric in result.get("metrics", [])
    ]


def _output_metrics(result: dict, json_output: bool, markdown: bool, ndjson: bool = False) -> None:
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("metrics", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return

    rows = _metric_rows(result)
    if markdown:
        click.echo(f"## Metrics: {result.get('name', '')}\n")
        click.echo(_markdown_table(["Metric", "Tag", "Period", "Value", "Freshness"], rows))
        return

    console.print(_simple_table(f"Metrics: {result.get('name', '')}", ["Metric", "Tag", "Period", "Value", "Freshness"], rows))


def _flatten_metrics_batch(results: list[dict]) -> list[dict]:
    rows = []
    for result in results:
        if "error" in result:
            rows.append({"identifier": result.get("identifier", ""), "error": result.get("error", "")})
            continue
        for metric in result.get("metrics", []):
            row = dict(metric)
            row.update({
                "identifier": result.get("identifier", ""),
                "cik": result.get("cik", ""),
                "ticker": result.get("ticker", ""),
                "name": result.get("name", ""),
                "reference_date": result.get("reference_date", ""),
            })
            rows.append(row)
    return rows


def _output_metrics_batch(results: list[dict], json_output: bool, markdown: bool,
                          ndjson: bool = False, envelope: Optional[dict] = None) -> None:
    if _batch_failed(results):
        _error_exit(results[0])
    rows = _flatten_metrics_batch(results)
    if ndjson:
        _ndjson(rows)
        return
    if _wants_json(json_output, markdown, ndjson):
        payload = dict(envelope) if envelope else {"results": results}
        payload.setdefault("results", results)
        _json(payload)
        return

    table_rows = [
        [
            row.get("identifier", ""),
            row.get("name", ""),
            row.get("metric", ""),
            row.get("tag", ""),
            _format_period(row.get("fact", {})),
            _format_value(row.get("fact", {}).get("val", ""), row.get("unit", "")),
            _format_freshness(row),
            row.get("error", ""),
        ]
        for row in rows
    ]
    headers = ["Identifier", "Company", "Metric", "Tag", "Period", "Value", "Freshness", "Error"]
    if markdown:
        click.echo(_markdown_table(headers, table_rows))
        return
    console.print(_simple_table("Metrics Batch", headers, table_rows))


def _output_brief(result: dict, json_output: bool, markdown: bool, ndjson: bool = False) -> None:
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("metrics", []))
        return
    if _wants_json(json_output, markdown, ndjson):
        _json(result)
        return

    profile = result.get("profile", {})
    metric_rows = _metric_rows(result)
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
        _output_filings(profile, "Latest Filings", False, True, False)
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

    _output_company(profile, False, False, False)
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


def _brief_summary_rows(results: list[dict]) -> list[list]:
    rows = []
    for result in results:
        if "error" in result:
            rows.append([result.get("identifier", ""), "", "", "", "", result.get("error", "")])
            continue
        profile = result.get("profile", {})
        latest_filing = (profile.get("filings") or [{}])[0]
        revenue = next((metric for metric in result.get("metrics", []) if metric.get("metric") == "revenue"), {})
        rows.append([
            result.get("identifier", ""),
            profile.get("name", ""),
            profile.get("cik", ""),
            latest_filing.get("filingDate", ""),
            _format_value(revenue.get("fact", {}).get("val", ""), revenue.get("unit", "")),
            "",
        ])
    return rows


def _output_brief_batch(results: list[dict], json_output: bool, markdown: bool,
                        ndjson: bool = False, envelope: Optional[dict] = None) -> None:
    if _batch_failed(results):
        _error_exit(results[0])
    if ndjson:
        _ndjson(results)
        return
    if _wants_json(json_output, markdown, ndjson):
        payload = dict(envelope) if envelope else {"results": results}
        payload.setdefault("results", results)
        _json(payload)
        return

    rows = _brief_summary_rows(results)
    headers = ["Identifier", "Company", "CIK", "Latest Filing", "Revenue", "Error"]
    if markdown:
        click.echo(_markdown_table(headers, rows))
        return
    console.print(_simple_table("Brief Batch", headers, rows))


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


def _output_frame(result: dict, json_output: bool, markdown: bool, ndjson: bool = False) -> None:
    _error_exit(result)
    if ndjson:
        _ndjson(result.get("facts", []))
        return
    if _wants_json(json_output, markdown, ndjson):
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
