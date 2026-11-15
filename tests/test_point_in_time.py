"""Intraday filing and XBRL availability must not leak later acceptances."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from edgar.api import EdgarClient
from edgar.cli import main
from edgar.point_in_time import available, parse_cutoff, rows_columns

CIK = '0000000001'
MORNING = '2026-07-01T11:30:00Z'
ROWS = [
    {'accessionNumber': '0000000001-26-000003', 'form': '8-K', 'filingDate': '2026-07-01', 'acceptanceDateTime': '2026-07-01T20:05:00Z', 'primaryDocument':'late.htm'},
    {'accessionNumber': '0000000001-26-000002', 'form': '8-K', 'filingDate': '2026-07-01', 'acceptanceDateTime': '2026-07-01T11:00:00Z', 'primaryDocument':'early.htm'},
    {'accessionNumber': '0000000001-26-000001', 'form': '10-K', 'filingDate': '2026-06-01', 'acceptanceDateTime': '', 'primaryDocument':'old.htm'},
]


def client():
    c=EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    c.resolve_company=lambda _: {'cik':CIK, 'name':'Fixture', 'ticker':'FIX'}
    data={'cik':CIK, 'name':'Fixture', 'filings':{'recent':rows_columns(ROWS), 'files':[]}}
    facts=[{'val':i, 'start':'2025-01-01', 'end':'2025-12-31', 'accn':r['accessionNumber'],
            'filed':r['filingDate'], 'form':'10-K'} for i,r in enumerate(ROWS)]
    def raw(path, **kwargs):
        if '/submissions/' in path: return data
        if '/Revenues.json' in path: return {'units':{'USD':facts}}
        return {'error':'404 no fixture data'}
    c._get_uncut=raw
    c._get_text=lambda url:'<p>The company entered into a securities purchase agreement for a private placement of 1,000,000 shares of common stock.</p>'
    return c


def test_date_inclusive_and_offset_equivalence():
    assert parse_cutoff('2026-07-01T07:30:00-04:00').normalized == MORNING
    assert available(ROWS[0], parse_cutoff('2026-07-01'))
    assert not available(ROWS[0], parse_cutoff(MORNING))
    assert not available({'filed':'2026-07-01'}, parse_cutoff(MORNING))
    assert not available({'filed':'2026-06-30'}, parse_cutoff(MORNING))
    assert available({'filed':'2026-06-29'}, parse_cutoff(MORNING))
    for invalid in ['2026-07-01T07:30:00', '2026-02-30', 'not-a-date']:
        with pytest.raises(ValueError): parse_cutoff(invalid)


def test_submissions_and_facts_share_cutoff_without_poisoning_raw_cache():
    c=client()
    before=c.submissions('FIX', as_of=MORNING)
    assert len(before['filings']) == 2 and before['as_of']==MORNING
    concept=c.company_concept('FIX','us-gaap','Revenues',as_of=MORNING)
    assert {r['val'] for r in concept['facts']} == {1,2}
    assert len(c.submissions('FIX')['filings']) == 3
    assert len(c.company_concept('FIX','us-gaap','Revenues')['facts']) == 3


def test_explicit_accession_cannot_bypass_cutoff():
    result=client().filing_text('FIX', accession_or_url=ROWS[0]['accessionNumber'], as_of=MORNING)
    assert 'error' in result and result['documents']==[]


def test_dilution_events_metrics_all_use_cutoff():
    c=client()
    assert len(c.events('FIX',as_of=MORNING)['events'])==1
    result=c.dilution('FIX',as_of=MORNING)
    assert all(r['accession'] != ROWS[0]['accessionNumber'] for r in result['filings_read'])
    assert c.metrics('FIX',['revenue'],as_of=MORNING)['metrics'][0]['fact']['val']==1


def test_historical_chunks_are_searched_for_replay():
    c=client(); raw=c._get_uncut
    def with_history(path, **kwargs):
        if path.endswith('-submissions-001.json'):
            return rows_columns([dict(ROWS[2], filingDate='2024-02-01')])
        result=raw(path,**kwargs)
        if 'filings' in result:
            result={**result,'filings':{'recent':rows_columns(ROWS[:2]), 'files':[
                {'name':'CIK0000000001-submissions-001.json','filingFrom':'2024-01-01','filingTo':'2024-12-31'}]}}
        return result
    c._get_uncut=with_history
    result=c.submissions('FIX',as_of='2024-03-01')
    assert len(result['filings'])==1 and result['filings'][0]['filed']=='2024-02-01'


@pytest.mark.parametrize('command',['company','filings','events','metrics','brief','dashboard','insiders','dilution','text'])
def test_cli_exposes_as_of(command):
    result=CliRunner().invoke(main,[command,'--help'])
    assert result.exit_code==0 and '--as-of' in result.output


def test_cli_intraday_and_invalid_naive_timestamp(monkeypatch):
    monkeypatch.setattr('edgar.cli.get_client',lambda **kwargs:client())
    runner=CliRunner()
    result=runner.invoke(main,['filings','FIX','--as-of',MORNING,'-j'])
    assert result.exit_code==0, result.output
    assert json.loads(result.output)['as_of']==MORNING
    result=runner.invoke(main,['filings','FIX','--as-of','2026-07-01T11:30:00'])
    assert result.exit_code==5
