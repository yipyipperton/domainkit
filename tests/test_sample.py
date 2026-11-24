import json
from pathlib import Path

from domainkit.enricher import enrich
from tests.support import NORTHWIND_INPUT, northwind_client

SAMPLE = Path(__file__).parent / "fixtures" / "sample_output.json"


def test_checked_in_sample_matches_a_fresh_fixture_run():
    client, _transport = northwind_client()
    result = enrich(NORTHWIND_INPUT, client=client)
    expected = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert result == expected


def test_readme_embeds_that_sample_verbatim():
    readme = Path(__file__).resolve().parents[1].joinpath("README.md").read_text(encoding="utf-8")
    sample = SAMPLE.read_text(encoding="utf-8").strip()
    assert sample in readme
