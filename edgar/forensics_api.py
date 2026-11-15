"""Bounded composite research views built from the existing filing primitives."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import copy
import re
import threading
import time

from edgar.forensics import compute_baby_shelf, compute_cash_runway
from edgar.ownership import parse_beneficial_ownership, deduplicate_owners, insider_holdings
from edgar.point_in_time import point_in_time


class _SharedLimiter:
    """Serialize slot allocation within the composite's worker threads."""
    def __init__(self, limiter):
        self.limiter = limiter
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            return self.limiter.acquire()


def _share_supply(dilution, ownership, public_float, price=None, baby=None):
    if baby is None:
        baby = compute_baby_shelf(dilution, price=price)
    stated = dilution.get('latest_shares_outstanding')
    baseline = (stated or {}).get('as_of') or (stated or {}).get('filed', '')
    offers = list(dilution.get('offerings', []))
    offers += [row for supplement in dilution.get('prospectus_supplements', []) for row in supplement.get('offering', [])]
    seen = set(); unique = []
    for row in offers:
        key=(row.get('accession'),row.get('sentence'))
        if key not in seen:
            seen.add(key); unique.append(row)
    closed = [row for row in unique if row.get('closing_status') == 'closed']
    pending, unresolved = [], []
    for row in unique:
        if row.get('closing_status') != 'announced_pending':
            continue
        later = [other for other in closed
                 if (other.get('acceptance_datetime') or other.get('filed', '')) >= (row.get('acceptance_datetime') or row.get('filed', ''))
                 and (other.get('accession') == row.get('accession') or not other.get('kind')
                      or other.get('kind') == row.get('kind'))]
        if later:
            unresolved.append({**row, 'current_closing_status': 'unresolved',
                'potential_closing_evidence': later,
                'status_caveat': 'Closing evidence may refer to this transaction; sentence-level extraction cannot establish the linkage. Do not count this as currently unclosed.'})
        else:
            pending.append({**row, 'status_caveat': 'Pending as stated in the cited sentence; later closure outside scanned evidence is not excluded.'})
    per_class = [{**row, 'figure':figure} for row in dilution.get('shares_outstanding', [])
                 for figure in row.get('figures', []) if figure.get('type')=='shares']
    from edgar.share_supply import select_latest_shares
    classes = {}
    for row in per_class:
        share_class = row['figure'].get('share_class') or row.get('share_class') or 'unspecified'
        classes.setdefault(share_class, []).append(row)
    latest_classes = [chosen for group in classes.values()
                      if (chosen := select_latest_shares(group)) is not None]
    return {
        'shares_outstanding': {
            'xbrl_cover_shares': dilution.get('xbrl_shares_outstanding'),
            'latest_filing_stated_shares': stated,
            'filing_stated_shares_by_class': latest_classes,
            'filing_stated_share_history': per_class,
            'basis': 'Separate dated evidence; no summed or split-adjusted total is inferred.',
        },
        'stated_non_affiliate_shares': baby['non_affiliate_float_shares'],
        'entity_public_float': public_float,
        'post_baseline_issuances': [r for r in unique if baseline and r.get('closing_status')=='closed'
                                  and r.get('closing_date', '') > baseline],
        'closed_issuances_date_unknown': [r for r in unique if r.get('closing_status')=='closed' and not r.get('closing_date')],
        'pending_unclosed_shares': pending,
        'offering_status_unresolved': unresolved,
        'reverse_splits': dilution.get('reverse_splits', []),
        'beneficial_owners': ownership.get('beneficial_owners', []),
        'insider_holdings': ownership.get('insider_holdings', []),
        'ownership_coverage': ownership.get('coverage', {}),
        'baby_shelf': baby,
        'warnings': dilution.get('warnings', []) + ownership.get('warnings', []) + baby.get('warnings', []),
        'caveats': [
            'Beneficial ownership can overlap and include derivatives; these records are not summed or deducted to infer tradable float.',
            'Closed offerings are not automatically freely tradable. Pending evidence is sentence-scoped and may describe different or superseded transactions.',
            'The post-baseline list uses explicit closing dates; filing dates alone do not establish issuance timing.',
            'Share counts and prices retain their stated dates and share classes; split adjustments require separate evidence.',
        ],
    }


class ForensicsMixin:
    def _ownership_snapshot(self, identifier, max_filings=10):
        profile = self.submissions(identifier, limit=100000)
        if 'error' in profile:
            return {'beneficial_owners':[], 'insider_holdings':[], 'warnings':[{'code':'ownership_unavailable','message':profile['error']}]}
        forms = {'SC 13D','SC 13D/A','SC 13G','SC 13G/A','SCHEDULE 13D','SCHEDULE 13D/A','SCHEDULE 13G','SCHEDULE 13G/A'}
        candidates = [r for r in profile.get('filings', []) if r.get('form','').upper() in forms]
        owners=[]; reads=[]; warnings=[]
        for filing in candidates[:max_filings]:
            url=re.sub(r'/xslSCHEDULE_[^/]+/', '/', filing.get('primary_doc_url',''))
            ref={'accession':filing.get('accessionNumber',''),'form':filing.get('form',''),
                 'filed':filing.get('filingDate',''),'acceptance_datetime':filing.get('acceptanceDateTime',''),
                 'source_url':url}
            try:
                parsed=parse_beneficial_ownership(self._get_text(url))
                owners.extend({**row,**ref} for row in parsed)
                reads.append({**ref,'owners_parsed':len(parsed)})
                if not parsed:
                    warnings.append({'code':'ownership_cover_unparsed','message':'No labeled ownership cover could be parsed.',**ref})
            except Exception as exc:
                reads.append({**ref,'error':str(exc)})
                warnings.append({'code':'ownership_fetch_failed','message':str(exc),**ref})
        insiders=self.insiders(identifier,limit=10000,max_form4_fetches=max_filings) if max_filings else {}
        if 'error' in insiders:
            warnings.append({'code':'insiders_unavailable','message':insiders['error']})
        if insiders.get('form4s_failed', 0):
            warnings.append({'code':'insider_filings_failed', 'message':'Some bounded insider reads failed; holdings coverage is incomplete.',
                             'count':insiders['form4s_failed']})
        return {'beneficial_owners':deduplicate_owners(owners), 'insider_holdings':insider_holdings(insiders),
                'coverage':{'filings_read':reads,'filings_not_read':max(0,len(candidates)-max_filings),
                            'max_ownership_filings':max_filings,'insider_fetch_limit':max_filings,
                            'insider_filings_fetched':insiders.get('form4s_fetched', 0),
                            'insider_filings_failed':insiders.get('form4s_failed', 0),
                            'history_scope':'Available submissions window; historical chunks are loaded for as-of replay.'},
                'warnings':warnings}

    def _public_float(self, identifier):
        from edgar.api import freshest_fact
        result=self.company_concept(identifier,'dei','EntityPublicFloat',unit='USD',limit=100,suggest_on_404=False)
        return freshest_fact(result.get('facts', []))

    @point_in_time
    def float(self, identifier, as_of=None, price=None, max_filings=25,
              max_supplements=3, max_ownership_filings=10, include_periodic=False, include_shelf=False):
        dilution=self.dilution(identifier,max_filings=max_filings,max_supplements=max_supplements,
                               include_periodic=include_periodic,include_shelf=include_shelf)
        if 'error' in dilution:
            return dilution
        ownership=self._ownership_snapshot(identifier,max_ownership_filings)
        return {'cik':dilution['cik'],'ticker':dilution.get('ticker',''),'name':dilution.get('name',''),
                **_share_supply(dilution,ownership,self._public_float(identifier),price)}

    @point_in_time
    def forensics(self, identifier, price=None, high_60d=None, as_of=None, max_filings=10,
                  max_supplements=3, max_ownership_filings=5, include_periodic=False, include_shelf=False):
        started=time.perf_counter()
        cache_start=len(self._cache_calls)
        old_memo, old_facts, old_limiter=self._request_memo,self._facts_snapshot,self.rate_limiter
        self._request_memo={}
        self.rate_limiter=_SharedLimiter(old_limiter) if old_limiter else None
        try:
            profile=self.submissions(identifier,limit=5)
            if 'error' in profile:
                return profile
            cik=profile['cik']
            facts=self._get(f'/api/xbrl/companyfacts/CIK{cik}.json')
            if 'error' not in facts:
                self._facts_snapshot={'cik':cik,'data':facts}
            clients=[]
            def fork():
                child=copy(self)
                child._cache_calls=[]
                child._fiscal_grids={}
                child.session=copy(self.session)
                child.session.headers=copy(self.session.headers)
                child.session.cookies=copy(self.session.cookies)
                clients.append(child)
                return child
            work={
                'dilution':lambda c:c.dilution(cik,max_filings=max_filings,max_supplements=max_supplements,
                                             include_periodic=include_periodic,include_shelf=include_shelf),
                'metrics':lambda c:c.metrics(cik,['cash','operating_cash_flow','assets_current','liabilities_current','debt','short_term_debt']),
                'events':lambda c:c.events(cik,limit=min(10,max_filings)),
                'ownership':lambda c:c._ownership_snapshot(cik,max_ownership_filings),
            }
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures={name:pool.submit(copy_context().run,fn,fork()) for name,fn in work.items()}
                parts={}
                for name,future in futures.items():
                    try: parts[name]=future.result()
                    except Exception as exc: parts[name]={'error':str(exc)}
            for child in clients:
                self._cache_calls.extend(child._cache_calls)
            dilution=parts['dilution']
            public=self._public_float(cik)
            baby=compute_baby_shelf(dilution,price=price,high_60d_close=high_60d)
            supply=_share_supply(dilution,parts['ownership'],public,price,baby=baby)
            distress=compute_cash_runway(parts['metrics'])
            distress['going_concern']=dilution.get('going_concern',[])
            failures={name:result['error'] for name,result in parts.items() if 'error' in result}
            warnings=supply['warnings']+distress.get('warnings',[])
            warnings += parts['events'].get('warnings', []) + distress.get('source_warnings', [])
            document_failures = [row for row in dilution.get('filings_read', []) if row.get('error')]
            event_failures = [row for row in parts['events'].get('events', []) if row.get('fetch_error') or row.get('exhibit_error')]
            if document_failures or event_failures:
                warnings.append({'code':'document_coverage_incomplete', 'message':'Some document or exhibit reads failed; retained evidence is partial.',
                                 'dilution_failures':len(document_failures), 'event_failures':len(event_failures)})
            warnings += [{'code':'section_unavailable','section':name,'message':error} for name,error in failures.items()]
            return {
                'cik':cik,'ticker':profile.get('ticker',''),'name':profile.get('name',''),
                'entity':{key:profile.get(key) for key in ('cik','ticker','name','exchanges','sic','sicDescription','state_of_incorporation','state_of_incorporation_description','filer_category')},
                'share_supply':supply,
                'shelves_and_capacity':{'shelf_registrations':dilution.get('shelf_registrations',[]),
                    'baby_shelf':baby,'equity_lines_and_atm':dilution.get('equity_lines_and_atm',[]),'agents':dilution.get('agents',[])},
                'warrant_overhang':{'statements':dilution.get('warrants',[]),
                    'caveat':'Dated warrant evidence may overlap or be superseded; active outstanding totals are not inferred.'},
                'financial_distress':distress,
                'recent_catalysts_and_risks':parts['events'].get('events',[]),
                'coverage':{'filings_read':dilution.get('filings_read',[]),'filings_not_read':dilution.get('filings_not_read',{}),
                            'section_errors':failures,'document_failures':document_failures,
                            'event_document_failures':event_failures,'max_workers':4},
                'warnings':warnings,
                'performance':{'elapsed_ms':round((time.perf_counter()-started)*1000,3),
                    'cache':self._summarize_cache(cache_start),
                    'caveat':'Measured elapsed time for this call; cold latency depends on document count, SEC rate limiting and network.'},
            }
        finally:
            self._request_memo,self._facts_snapshot,self.rate_limiter=old_memo,old_facts,old_limiter

    def feed(self, forms=None, since_minutes=15, use_state=False, state_store=None, now=None, max_pages=10):
        from edgar.feed import fetch_feed
        return fetch_feed(self,forms=forms,since_minutes=since_minutes,use_state=use_state,
                          state_store=state_store,now=now,max_pages=max_pages)
