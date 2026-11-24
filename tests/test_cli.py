import json
import subprocess
import sys

import pytest

from domainkit.cli import main
from domainkit.errors import InvalidDomain


def test_module_version():
    completed = subprocess.run(
        [sys.executable, "-m", "domainkit", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip().startswith("domainkit ")


def test_enrich_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(
        "domainkit.cli.enrich",
        lambda domain, **kwargs: {"input": domain, "domain": "example.com", "error": None, "max_pages": kwargs["max_pages"]},
    )
    assert main(["enrich", "https://www.example.com/", "--max-pages", "2"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["domain"] == "example.com"
    assert payload["input"] == "https://www.example.com/"
    assert payload["max_pages"] == 2


def test_enrich_writes_a_file_and_rejects_a_bad_domain(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "domainkit.cli.enrich",
        lambda domain, **kwargs: {"domain": "example.com"},
    )
    output = tmp_path / "out.json"
    assert main(["enrich", "example.com", "-o", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["domain"] == "example.com"
    assert "wrote" in capsys.readouterr().err

    def fail(domain, **kwargs):
        raise InvalidDomain("not a public domain name")

    monkeypatch.setattr("domainkit.cli.enrich", fail)
    assert main(["enrich", "localhost"]) == 1
    assert "not a public domain name" in capsys.readouterr().err


def test_batch_csv_and_jsonl(monkeypatch, tmp_path, capsys):
    def fake(domain, **kwargs):
        if domain.startswith("bad"):
            raise InvalidDomain("not a public domain name")
        return {
            "input": domain,
            "domain": "example.com",
            "final_url": "https://example.com/",
            "error": None,
            "company_name": {
                "value": "=Northwind",
                "source": "title",
                "confidence": "low",
                "inferred": True,
                "page": "https://example.com/",
            },
            "website_title": None,
            "meta_description": None,
            "emails": [{"value": "hello@example.com", "source": "mailto", "confidence": "high", "inferred": False, "page": "https://example.com/"}],
            "phones": [],
            "social_links": {"linkedin": [{"value": "https://www.linkedin.com/company/example", "source": "html-link", "confidence": "medium", "inferred": False, "page": "https://example.com/"}]},
            "address": None,
            "technologies": [{"name": "WordPress"}],
            "contact_forms": [],
            "booking_links": [],
            "live_chat": [],
            "warnings": [],
        }

    monkeypatch.setattr("domainkit.cli.enrich", fake)
    source = tmp_path / "domains.csv"
    source.write_text("domain\nhttps://www.example.com/\nbad.example\n", encoding="utf-8")
    csv_output = tmp_path / "out.csv"
    assert main(["batch", str(source), "-o", str(csv_output)]) == 0
    text = csv_output.read_text(encoding="utf-8")
    assert "company_name" in text.splitlines()[0]
    assert "'=Northwind" in text
    assert "not a public domain name" in text
    assert "hello@example.com" in text
    assert "1 errors" in capsys.readouterr().err

    jsonl_output = tmp_path / "out.jsonl"
    assert main(["batch", str(source), "-o", str(jsonl_output)]) == 0
    rows = [json.loads(line) for line in jsonl_output.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["domain"] == "example.com"
    assert rows[1]["error"] == "not a public domain name"
    assert main(["batch", str(source), "-o", str(tmp_path / "out.txt")]) == 1


def test_batch_without_a_header_uses_the_first_column(monkeypatch, tmp_path, capsys):
    seen = []

    def fake(domain, **kwargs):
        seen.append(domain)
        return {"input": domain, "domain": domain, "error": None}

    monkeypatch.setattr("domainkit.cli.enrich", fake)
    source = tmp_path / "plain.csv"
    source.write_text("example.com\nwww.example.org\n", encoding="utf-8")
    assert main(["batch", str(source), "-o", str(tmp_path / "out.jsonl")]) == 0
    assert seen == ["example.com", "www.example.org"]
    capsys.readouterr()


def test_parser_requires_a_command():
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2
