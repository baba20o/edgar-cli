"""Registration history without treating request form codes as legal outcomes.

The caller supplies filing rows already filtered to its point-in-time cutoff.
Metadata connects filings by SEC file number; it does not prove the scope or
effect of a withdrawal. Body evidence can identify requests and confirmations.
"""

from __future__ import annotations

import re


SHELF_FORMS = {"F-3", "S-3", "F-3ASR", "S-3ASR"}
REGISTRATION_FORMS = SHELF_FORMS | {"F-1", "S-1", "F-1MEF", "S-1MEF", "F-3MEF", "S-3MEF"}
WITHDRAWAL_FORMS = {"RW", "RW WD", "AW", "AW WD"}


def _value(row: dict, raw: str, alias: str) -> str:
    return row.get(raw) or row.get(alias) or ""


def _ref(row: dict) -> dict:
    return {
        "form": row.get("form", ""),
        "accession": _value(row, "accessionNumber", "accession"),
        "filed": _value(row, "filingDate", "filed"),
        "acceptance_datetime": _value(row, "acceptanceDateTime", "acceptance_datetime"),
        "source_url": row.get("primary_doc_url") or row.get("source_url") or row.get("filing_url", ""),
        "filing_url": row.get("filing_url", ""),
    }


def _sort_key(row: dict) -> tuple:
    return (_value(row, "acceptanceDateTime", "acceptance_datetime")
            or _value(row, "filingDate", "filed"), _value(row, "accessionNumber", "accession"))


def withdrawal_statements(sentences: list[str], form: str = "") -> list[dict]:
    """Conservative sentence-level withdrawal evidence, without legal inference.

    Pass the actual form code. A confirmation requires explicit SEC/Commission
    grant language and an entire-registration scope. Post-effective amendments
    and deregistration of unsold securities are not assumed to withdraw a shelf.
    """
    out = []
    for sentence in sentences:
        if not re.search(r"withdraw|rescind|deregister", sentence, re.I):
            continue
        amendment = bool(re.search(r"withdraw(?:al|ing)?[^.;]{0,80}(?:only )?(?:the )?amendment|"
                                   r"amendment[^.;]{0,80}withdraw", sentence, re.I))
        entire = bool(re.search(r"entire registration statement|registration statement[^.;]{0,100}in its entirety", sentence, re.I))
        scope = "amendment" if amendment or form.upper() in {"AW", "AW WD"} else "registration" if entire else "unknown"
        rescission = bool(re.search(r"withdraw(?:s|al|ing)?[^.;]{0,55}(?:withdrawal request|request to withdraw)|"
                                    r"rescind[^.;]{0,70}(?:request|withdrawal)", sentence, re.I))
        granted = re.search(r"(?:SEC|Commission)[^.;]{0,60}(?:has |had )?(?:granted|approved|consented to)[^.;]{0,70}withdraw", sentence, re.I)
        confirmation = bool(granted and not re.search(r"\bnot\b|\bnever\b|denied", granted.group(0), re.I)
                            and not re.search(r"\b(?:if|when|once|unless)\b[^.;]{0,15}$",
                                              sentence[:granted.start()], re.I))
        action = ("rescission" if rescission else "confirmed_withdrawal" if confirmation and scope == "registration"
                  else "deregistration" if re.search(r"deregister", sentence, re.I) else "request")
        row = {"topic": "registration_status", "sentence": sentence, "figures": [],
               "scope": scope, "action": action}
        targets = re.findall(r"\b\d{10}-\d{2}-\d{6}\b", sentence)
        if targets:
            row["target_accessions"] = targets
        out.append(row)
    return out


def aggregate(rows: list[dict], statements: list[dict]) -> list[dict]:
    """Group registration filings, preserving legacy keys and evidence history."""
    shelves: dict[str, dict] = {}
    ordered = sorted(rows, key=_sort_key)
    for row in ordered:
        form = row.get("form", "").upper()
        number = _value(row, "fileNumber", "file_number")
        base = form.split("/")[0]
        if not number or base not in REGISTRATION_FORMS:
            continue
        ref = _ref(row)
        shelf = shelves.setdefault(number, {
            "file_number": number, "form": base, "shelf": base in SHELF_FORMS,
            "filed": ref["filed"], "accession": ref["accession"],
            "source_url": ref["source_url"], "filing_url": ref["filing_url"],
            "acceptance_datetime": ref["acceptance_datetime"],
            "amendments": [], "effective": None, "supplements": [], "capacity": [],
            "withdrawal_requests": [], "withdrawal_rescissions": [], "withdrawn": None,
            "status": "effectiveness_unknown", "status_evidence": [], "caveats": [],
        })
        if ref["accession"] != shelf["accession"]:
            shelf["amendments"].append(ref)
        if base.endswith("ASR"):
            shelf["effective"] = {"date": ref["filed"], "accession": ref["accession"],
                                  "basis": "automatic_effectiveness_form", "source_url": ref["source_url"]}
            shelf["status"] = "effective"
            shelf["status_evidence"].append({**ref, "basis": "automatic_effectiveness_form"})

    by_accession: dict[str, list[dict]] = {}
    for statement in statements:
        by_accession.setdefault(statement.get("accession", ""), []).append(statement)
        shelf = shelves.get(statement.get("file_number", ""))
        if shelf and statement.get("topic") == "shelf_capacity":
            shelf["capacity"].append(statement)

    for row in ordered:
        form = row.get("form", "").upper()
        shelf = shelves.get(_value(row, "fileNumber", "file_number"))
        if not shelf:
            continue
        ref = _ref(row)
        if form == "POS AM":
            shelf["amendments"].append(ref)
        elif form == "EFFECT":
            shelf["effective"] = {"date": ref["filed"], "accession": ref["accession"],
                                  "source_url": ref["source_url"], "basis": "EFFECT_filing"}
            shelf["status"] = "effective"
            shelf["status_evidence"].append({**ref, "basis": "EFFECT_filing"})
        elif form.startswith("424B"):
            shelf["supplements"].append(ref)
        elif form in WITHDRAWAL_FORMS:
            evidence = [s for s in by_accession.get(ref["accession"], [])
                        if s.get("topic") == "registration_status"]
            scopes = {s.get("scope", "unknown") for s in evidence}
            scope = "amendment" if form.startswith("AW") else next(iter(scopes)) if len(scopes) == 1 else "unknown"
            record = {**ref, "scope": scope, "evidence": evidence}
            if form.endswith(" WD"):
                targets = {target for s in evidence if s.get("action") == "rescission"
                           for target in s.get("target_accessions", [])}
                matched = [r for r in shelf["withdrawal_requests"] if r["accession"] in targets]
                record["resolved_request_accessions"] = [r["accession"] for r in matched]
                for request in matched:
                    request["rescinded_by"] = ref["accession"]
                shelf["withdrawal_rescissions"].append(record)
                if not matched and not form.startswith("AW"):
                    shelf["caveats"].append("A withdrawal rescission was filed, but its target request was not established from the supplied body evidence.")
            else:
                shelf["withdrawal_requests"].append(record)

    for shelf in shelves.values():
        shelf["amendments"].sort(key=_sort_key)
        active = [r for r in shelf["withdrawal_requests"]
                  if not r.get("rescinded_by") and r["scope"] != "amendment"]
        confirmations = [s for s in statements if s.get("file_number") == shelf["file_number"]
                         and s.get("topic") == "registration_status"
                         and s.get("action") == "confirmed_withdrawal" and s.get("scope") == "registration"]
        if confirmations:
            latest = max(confirmations, key=_sort_key)
            if not any(r.get("rescinded_by") and r["accession"] == latest.get("accession")
                       for r in shelf["withdrawal_requests"]):
                shelf["withdrawn"] = {"date": latest.get("filed", ""),
                                      "accession": latest.get("accession", ""),
                                      "source_url": latest.get("source_url", ""),
                                      "basis": "explicit_commission_confirmation",
                                      "sentence": latest["sentence"]}
                shelf["status"] = "withdrawn"
                shelf["status_evidence"].append(latest)
        if not shelf["withdrawn"] and active:
            shelf["status"] = ("withdrawal_requested" if all(r["scope"] == "registration" for r in active)
                               else "withdrawal_status_unknown")
            shelf["status_evidence"].extend(active)
            shelf["caveats"].append("Withdrawal requests are not treated as proof of withdrawal; scope and applicable effectiveness require body evidence.")
        if shelf["effective"]:
            shelf["effectiveness_scope"] = "historical_filing_evidence"
            shelf["current_offering_availability"] = "not_assessed"
            shelf["caveats"].append(
                "Historical effectiveness evidence only: expiration, any applicable shelf time limit, "
                "registration eligibility and unused capacity are not assessed. An EFFECT or ASR "
                "filing does not establish current offering availability.")
        else:
            shelf["caveats"].append("No effectiveness evidence was found in the supplied filing history; this does not prove the registration is ineffective.")
        if any(r["scope"] == "amendment" for r in shelf["withdrawal_requests"]):
            shelf["caveats"].append("Amendment-only withdrawal records do not establish withdrawal of the parent registration.")
        offering_types = {s.get("offering_type") for s in shelf["capacity"] if s.get("offering_type")}
        if offering_types:
            shelf["offering_type"] = next(iter(offering_types)) if len(offering_types) == 1 else "mixed"
    return sorted(shelves.values(), key=lambda shelf: (shelf["filed"], shelf["accession"]), reverse=True)
