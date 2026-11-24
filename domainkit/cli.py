"""Command line interface for one domain, a CSV batch, or the local API."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from domainkit import __version__
from domainkit.constants import (
    DEFAULT_MAX_PAGES,
    DEFAULT_PER_HOST_DELAY,
    DEFAULT_TIMEOUT,
    SOCIAL_PLATFORMS,
)
from domainkit.enricher import enrich, error_result
from domainkit.errors import DomainKitError
from domainkit.normalize import normalize_domain


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="domainkit",
        description="Open-source company intelligence for AI agents. One domain in. Structured business data out.",
    )
    parser.add_argument("--version", action="version", version=f"domainkit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    enrich_parser = sub.add_parser("enrich", help="Enrich one domain and print JSON")
    enrich_parser.add_argument("domain", help="example.com, https://example.com/, or www.example.com")
    enrich_parser.add_argument("-o", "--output", help="Write JSON to this file instead of stdout")
    enrich_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    enrich_parser.add_argument("--delay", type=float, default=DEFAULT_PER_HOST_DELAY, help="Seconds to wait between requests to one host")
    enrich_parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)

    batch_parser = sub.add_parser("batch", help="Enrich a CSV of domains")
    batch_parser.add_argument("input", help="CSV with a domain, url, website, or host column, or one domain per row")
    batch_parser.add_argument("-o", "--output", required=True, help="Destination .csv or .jsonl file")
    batch_parser.add_argument("--format", choices=("csv", "jsonl"), help="Output format. Defaults from the file extension")
    batch_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    batch_parser.add_argument("--delay", type=float, default=DEFAULT_PER_HOST_DELAY)
    batch_parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)

    serve_parser = sub.add_parser("serve", help="Serve the local enrichment API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8747)
    return parser


def _dump(result: dict) -> str:
    return json.dumps(result, indent=2, ensure_ascii=False) + "\n"


def _run_enrich(args: argparse.Namespace) -> int:
    try:
        result = enrich(
            args.domain,
            timeout=args.timeout,
            max_pages=args.max_pages,
            per_host_delay=args.delay,
        )
    except DomainKitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    text = _dump(result)
    if args.output:
        try:
            Path(args.output).write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"could not write {args.output}: {exc}", file=sys.stderr)
            return 1
        print(f"wrote {args.output}", file=sys.stderr)
        return 0
    sys.stdout.write(text)
    return 0


def _load_domains(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise DomainKitError(f"could not read {path}: {exc}") from exc
    rows = [row for row in csv.reader(text.splitlines()) if any(cell.strip() for cell in row)]
    if not rows:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    column = None
    data = rows
    for name in ("domain", "website", "url", "host"):
        if name in header:
            column = header.index(name)
            data = rows[1:]
            break
    if column is None:
        column = 0
    domains: list[str] = []
    for row in data:
        if column < len(row) and row[column].strip():
            domains.append(row[column].strip())
    return domains


def _csv_cell(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in "=+-@\t":
        return "'" + text
    return text


def _join(values: list[str]) -> str:
    return " | ".join(value for value in values if value)


def _field_value(field: dict | None) -> str:
    if not field:
        return ""
    value = field.get("value")
    if isinstance(value, dict):
        parts = [value.get(key) for key in ("street", "locality", "region", "postal_code", "country", "raw")]
        return ", ".join(part for part in parts if part)
    return "" if value is None else str(value)


def _csv_row(result: dict) -> dict[str, str]:
    social = result.get("social_links") or {}
    address = _field_value(result.get("address"))
    row = {
        "input": result.get("input") or "",
        "domain": result.get("domain") or "",
        "final_url": result.get("final_url") or "",
        "company_name": _field_value(result.get("company_name")),
        "company_name_source": (result.get("company_name") or {}).get("source") or "",
        "company_name_inferred": (
            "" if result.get("company_name") is None else str(bool(result["company_name"].get("inferred"))).lower()
        ),
        "website_title": _field_value(result.get("website_title")),
        "meta_description": _field_value(result.get("meta_description")),
        "emails": _join(_field_value(item) for item in result.get("emails") or []),
        "phones": _join(_field_value(item) for item in result.get("phones") or []),
        "address": address,
        "technologies": _join(item.get("name", "") for item in result.get("technologies") or []),
        "contact_forms": _join(item.get("action", "") for item in result.get("contact_forms") or []),
        "booking_links": _join(
            f"{item.get('provider', '')} {item.get('url', '')}".strip() for item in result.get("booking_links") or []
        ),
        "live_chat": _join(item.get("provider", "") for item in result.get("live_chat") or []),
        "error": result.get("error") or "",
        "warnings": _join(result.get("warnings") or []),
    }
    for platform in SOCIAL_PLATFORMS:
        row[platform] = _join(_field_value(item) for item in social.get(platform) or [])
    return {key: _csv_cell(value) for key, value in row.items()}


def _csv_columns() -> list[str]:
    return [
        "input",
        "domain",
        "final_url",
        "company_name",
        "company_name_source",
        "company_name_inferred",
        "website_title",
        "meta_description",
        "emails",
        "phones",
        *SOCIAL_PLATFORMS,
        "address",
        "technologies",
        "contact_forms",
        "booking_links",
        "live_chat",
        "error",
        "warnings",
    ]


def _enrich_or_error(domain: str, args: argparse.Namespace) -> dict:
    try:
        return enrich(
            domain,
            timeout=args.timeout,
            max_pages=args.max_pages,
            per_host_delay=args.delay,
        )
    except DomainKitError as exc:
        known = None
        try:
            known = normalize_domain(domain).domain
        except DomainKitError:
            known = None
        return error_result(domain, str(exc), known)


def _run_batch(args: argparse.Namespace) -> int:
    output = Path(args.output)
    fmt = args.format
    if fmt is None:
        suffix = output.suffix.lower()
        if suffix == ".jsonl":
            fmt = "jsonl"
        elif suffix == ".csv":
            fmt = "csv"
        else:
            print("output file must end in .csv or .jsonl, or pass --format", file=sys.stderr)
            return 1
    if not output.parent.exists():
        print(f"output directory does not exist: {output.parent}", file=sys.stderr)
        return 1
    try:
        domains = _load_domains(Path(args.input))
    except DomainKitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    results = [_enrich_or_error(domain, args) for domain in domains]
    errors = sum(1 for result in results if result.get("error"))
    try:
        if fmt == "jsonl":
            lines = [json.dumps(result, ensure_ascii=False) for result in results]
            output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        else:
            with output.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=_csv_columns(), lineterminator="\n")
                writer.writeheader()
                for result in results:
                    writer.writerow(_csv_row(result))
    except OSError as exc:
        print(f"could not write {output}: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(results)} rows to {output} ({errors} errors)", file=sys.stderr)
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from domainkit.api import app

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "enrich":
        return _run_enrich(args)
    if args.command == "batch":
        return _run_batch(args)
    if args.command == "serve":
        return _run_serve(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
