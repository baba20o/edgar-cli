from edgar.ownership import parse_beneficial_ownership, deduplicate_owners, insider_holdings


def test_structured_cover_preserves_people_classes_and_zero():
    xml='''<edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13D"><securitiesClassTitle>Class A</securitiesClassTitle><dateOfEvent>11/09/2026</dateOfEvent><reportingPersons><reportingPersonInfo><reportingPersonName>Owner A</reportingPersonName><reportingPersonCIK>123</reportingPersonCIK><aggregateAmountOwned>204303570.00</aggregateAmountOwned><percentOfClass>54.0</percentOfClass><sharedVotingPower>204303570.00</sharedVotingPower></reportingPersonInfo><reportingPersonInfo><reportingPersonName>Owner B</reportingPersonName><aggregateAmountOwned>0.00</aggregateAmountOwned><percentOfClass>0</percentOfClass></reportingPersonInfo></reportingPersons></edgarSubmission>'''
    rows=parse_beneficial_ownership(xml)
    assert len(rows)==2 and rows[0]['shares']==204303570
    assert rows[0]['security_title']=='Class A' and rows[1]['shares']==0
    assert rows[0]['evidence']['aggregateAmountOwned']=='204303570.00'


def test_legacy_cover_only_and_latest_person_class():
    text='Names of Reporting Persons\n\nExample Fund LLC\n\n2. Check the Appropriate Box\n\nAggregate Amount Beneficially Owned by Each Reporting Person\n\n1,000,000\n\nPercent of Class Represented by Amount in Row (9)\n\n8.5%'
    row=parse_beneficial_ownership(text)[0]
    assert row['owner_name']=='Example Fund LLC' and row['shares']==1000000 and row['percent_of_class']==8.5
    assert not parse_beneficial_ownership('We own 4,000,000 shares, about 9% of the company.')
    newer={**row,'filed':'2026-11-01','shares':2}
    assert deduplicate_owners([{**row,'filed':'2026-10-01'},newer])==[newer]


def test_insider_does_not_sum_derivatives_and_common_shares():
    base={'owner_name':'Officer','is_officer':True,'filed':'2026-11-01','security_title':'Common'}
    rows=insider_holdings({'transactions':[{**base,'shares_after':20,'is_derivative':False},
                                         {**base,'shares_after':30,'is_derivative':True}]})
    assert len(rows)==2


def test_real_sbet_schedule_13g_cover_uses_actual_sec_field_names():
    import json
    from pathlib import Path
    source = json.loads((Path(__file__).parent / 'fixtures/handoff/sbet-20250530-13g.json').read_text())
    rows = parse_beneficial_ownership(source['xml'])
    assert len(rows) == 1
    row = rows[0]
    assert row['owner_name'] == 'Alpha Capital Anstalt'
    assert row['owner_cik'] == ''  # No owner CIK is stated in the cover record.
    assert row['shares'] == 284550
    # Preserve the source's stated percentage, without repairing its arithmetic.
    assert row['percent_of_class'] == 0.004
    assert row['security_title'] == 'COMMON STOCK, $0.0001 PAR VALUE PER SHARE'
    assert row['reported_date'] == '2025-05-30'
    assert row['reported_date_text'] == '05/30/2025'
    assert row['shared_voting_power'] == 0
    assert row['evidence']['reportingPersonBeneficiallyOwnedAggregateNumberOfShares'] == '284550.00'
    assert row['evidence']['classPercent'] == '0.004'


def test_legacy_missing_amount_does_not_consume_next_cover_row_number():
    # Synthetic incomplete legacy covers, not quotations from the SEC fixture.
    heading = ('Names of Reporting Persons\n\nExample Fund LLC\n\n'
               '9. Aggregate Amount Beneficially Owned by Each Reporting Person\n\n')
    for next_row in ('10. Check box if the aggregate amount excludes certain shares',
                     '10 Check box if the aggregate amount excludes certain shares',
                     '10\n\nCheck box if the aggregate amount excludes certain shares'):
        assert not parse_beneficial_ownership(heading + next_row), next_row


def test_legacy_numeric_field_requires_whole_value_not_partial_number():
    heading = ('Names of Reporting Persons\n\nExample Fund LLC\n\n'
               'Aggregate Amount Beneficially Owned by Each Reporting Person\n\n')
    assert not parse_beneficial_ownership(heading + '1,000,000 may be acquired if the transaction closes.')
    assert not parse_beneficial_ownership(heading + 'N/A\n\n10. Check box if the aggregate amount excludes certain shares')
    row = parse_beneficial_ownership(heading + '10\n\n10. Check box if the aggregate amount excludes certain shares')[0]
    assert row['shares'] == 10
