"""Beneficial-owner cover evidence; holdings are not automatic float deductions."""
from __future__ import annotations

import re
from datetime import datetime
import xml.etree.ElementTree as ET

from edgar.filing_text import document_to_text


def _numeric(text):
    match = re.fullmatch(r'\s*([\d,]+(?:\.\d+)?)\s*%?\s*(?:\([^)]+\))?\s*', text)
    if not match:
        return None
    number = float(match.group(1).replace(',', ''))
    return int(number) if number.is_integer() else number


def _reported_date(value):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return value


def _xml_value(node, tag):
    return next(((el.text or '').strip() for el in node.iter() if el.tag.split('}')[-1] == tag), '')


def _legacy_value(block, label, percentage=False):
    """Accept a complete labeled cover value, not the next field's row number."""
    match = re.search(label, block, re.I)
    if not match:
        return None
    tail = block[match.end():]
    lines = [(m.group().strip(), m.end()) for m in re.finditer(r'[^\n]+', tail)
             if m.group().strip(' \t:-')]
    if not lines:
        return None
    value, end = lines[0]
    value = value.lstrip(' :-')
    # A lost amount can put the next numbered cover heading immediately after
    # the label. Same-line headings already fail the full numeric parse below;
    # this detects the common separately rendered row-number cell as well.
    if (re.fullmatch(r'\d{1,2}', value) and len(lines) > 1
            and re.match(r'check\s+(?:the\s+)?(?:appropriate\s+)?box|type of reporting person|percent(?:age)? of class',
                         lines[1][0], re.I)):
        return None
    if percentage and '%' not in value:
        return None
    number = _numeric(value)
    if number is None:
        return None
    return number, block[match.start():match.end() + end].strip()


def parse_beneficial_ownership(content):
    """Read structured Schedules 13D/G, with conservative legacy cover fallback.

    Schedule 13D and 13G use different SEC cover record and amount field names.
    Text fallback reads labeled cover fields, never narrative ownership totals.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        root = None
    rows = []
    if root is not None:
        for person in root.iter():
            kind = person.tag.split('}')[-1]
            if kind == 'reportingPersonInfo':
                amount_tag, percent_tag = 'aggregateAmountOwned', 'percentOfClass'
                date_tag = 'dateOfEvent'
            elif kind == 'coverPageHeaderReportingPersonDetails':
                amount_tag = 'reportingPersonBeneficiallyOwnedAggregateNumberOfShares'
                percent_tag, date_tag = 'classPercent', 'eventDateRequiresFilingThisStatement'
            else:
                continue
            name = _xml_value(person, 'reportingPersonName')
            amount = _xml_value(person, amount_tag)
            percent = _xml_value(person, percent_tag)
            if name:
                rows.append({'owner_name': name, 'owner_cik': _xml_value(person, 'reportingPersonCIK'),
                             'shares': _numeric(amount), 'percent_of_class': _numeric(percent),
                             'security_title': _xml_value(root, 'securitiesClassTitle'),
                             'reported_date': _reported_date(_xml_value(root, date_tag)),
                             'reported_date_text': _xml_value(root, date_tag),
                             'shared_voting_power': _numeric(_xml_value(person, 'sharedVotingPower')),
                             'evidence': {'name': name, amount_tag: amount, percent_tag: percent},
                             'method': 'SEC Schedule cover XML'})
        if rows:
            return rows
    text = document_to_text(content)
    blocks = re.split(r'(?i)names? of reporting persons?\s*(?:\([^)]*\))?', text)[1:]
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        name = re.split(r'\b(?:I\.?R\.?S\.?|Check the Appropriate|2\s*\.)', lines[0], flags=re.I)[0].strip()
        amount = _legacy_value(block, r'aggregate amount beneficially owned(?: by each reporting person)?')
        percent = _legacy_value(block, r'percent(?:age)? of class(?: represented by amount in row\s*\(?\d+\)?)?', percentage=True)
        if name and amount and not re.fullmatch(r'\d+\.?', name):
            rows.append({'owner_name':name,'owner_cik':'','shares':amount[0],
                         'percent_of_class':percent[0] if percent else None,
                         'security_title':'','reported_date':'',
                         'evidence':{'name':name, 'aggregate_amount':amount[1],
                                     'percent_of_class':percent[1] if percent else ''},
                         'method':'labeled legacy cover text'})
    return rows


def deduplicate_owners(rows):
    """Latest filing per person and class, without aggregating overlapping groups."""
    latest = {}
    for row in sorted(rows, key=lambda r:(r.get('acceptance_datetime') or r.get('filed',''),r.get('accession',''))):
        key = (row.get('owner_cik') or re.sub(r'\s+',' ',row.get('owner_name','')).casefold(), row.get('security_title','').casefold())
        latest[key] = row
    return list(latest.values())


def insider_holdings(data):
    rows = []
    for initial in data.get('initial_statements',[]):
        if not (initial.get('is_officer') or initial.get('is_director')):
            continue
        for holding in initial.get('holdings',[]):
            rows.append({**initial, **holding, 'shares_after':holding.get('shares'),
                         'holdings':None, 'holding_basis':'initial_statement'})
    for tx in data.get('transactions',[]):
        if (tx.get('is_officer') or tx.get('is_director')) and tx.get('shares_after') is not None:
            rows.append({**tx, 'holding_basis':'post_transaction'})
    latest = {}
    for row in sorted(rows,key=lambda r:(r.get('acceptance_datetime') or r.get('filed',''),r.get('transaction_date') or '')):
        key=(row.get('owner_cik') or row.get('owner_name'),row.get('security_title'),
             row.get('is_derivative'),row.get('ownership'),row.get('nature_of_ownership'))
        # Multiple rows at one time can describe distinct holdings; keep them
        # rather than summing or silently discarding one as an owner total.
        when=(row.get('acceptance_datetime') or row.get('filed',''),row.get('transaction_date') or '')
        old=latest.get(key)
        if old is None or when > old[0]: latest[key]=(when,[row])
        elif when == old[0] and row not in old[1]: old[1].append(row)
    return [row for _,group in latest.values() for row in group]
