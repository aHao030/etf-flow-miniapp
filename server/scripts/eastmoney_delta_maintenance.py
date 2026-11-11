"""Maintain existing Eastmoney histories using checked same-provider overlap."""
from datetime import date, datetime, timezone
import json
import logging
import re
import sqlite3
import time

import collect_eastmoney_stock_prices as em
import collect_stock_prices as p
import probe_eastmoney_delta as probe
import stock_price_batch as common
from history import eastmoney_stock_prices as source

ANCHORS = ('000001', '600519')


class Rejected(Exception):
    def __init__(self, reason, cooldown=0):
        super().__init__(reason)
        self.reason = reason
        self.cooldown = cooldown


def frozen_members(root, target):
    known = {}
    for path in sorted((root.parent / 'price-universes').glob('current-listed-*.json')):
        try:
            manifest, fingerprint = common.read_universe(path)
        except (OSError, KeyError, TypeError, ValueError, AttributeError):
            continue
        if manifest['target_end'] <= target:
            for item in manifest['stocks']:
                # Retain the original membership evidence and its actual observation date.
                known.setdefault(item['stock_code'], (item, manifest['as_of'], manifest['target_end'], fingerprint))
    return known


def existing_attempt(root, code, target, now):
    attempt = p.read_json(em.attempt_location(root, code))
    try:
        day = date.fromisoformat(attempt['target_end']).isoformat()
        if day != attempt['target_end'] or day > target or not isinstance(attempt['reason'], str) or not attempt['reason']:
            return None
        if p.stamp(attempt['attempted_at']) > now:
            return None
        return attempt
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def delayed(attempt, target, now):
    if not attempt or attempt['target_end'] != target:
        return False
    return (now - p.stamp(attempt['attempted_at'])).total_seconds() < (em.cooldown(attempt['reason']) or 7200)


def merge_response(raw, stock, old, start, target, fetched_at):
    if not isinstance(raw, bytes) or len(raw) > source.MAX_BYTES:
        raise Rejected('invalid_response_size')
    try:
        bundle = source.parse_range(json.loads(raw), stock, start, target)
    except (ValueError, TypeError, OverflowError, KeyError):
        raise Rejected('source_identity_dates_or_values_invalid') from None
    old_tail = [row for row in old['candles'] if row[0] >= start]
    rows = bundle['candles']
    if not old_tail or rows[:len(old_tail)] != old_tail:
        raise Rejected('overlap_changed_or_missing')
    # Only append after the last real source candle. Historical calendar disagreements
    # and zero-volume rows survive intact, as do all previous verified close prices.
    candles = old['candles'] + rows[len(old_tail):]
    if bundle['provider_total'] != len(candles):
        raise Rejected('reported_total_does_not_match_merged_history')
    bundle['candles'] = candles
    bundle['series'] = [[row[0], row[2]] for row in candles if row[5] > 0]
    record = source.make_record(stock, target, bundle, fetched_at)
    if record['series'][:len(old['series'])] != old['series']:
        raise Rejected('previous_verified_prices_changed')
    record['update_evidence'] = {'kind': 'same_provider_short_range', 'previous_checked_through': old['checked_through'],
                                 'range_start': start, 'overlap_rows': len(old_tail), 'received_rows': len(rows),
                                 'source_declared_total': bundle['provider_total']}
    return record


def eligible(root, catalog, target, now, frozen):
    files = root / 'prices/eastmoney/v1'
    stocks, valid, queue = {}, set(), []
    initial, waiting = set(), []
    keys = {path.stem for path in files.glob('*.json')}
    keys.update(path.stem for path in (root / 'prices/eastmoney/attempts/v1').glob('*.json'))
    for code in sorted(keys):
        item = catalog.get(code)
        if not re.fullmatch(r'\d{6}', code):
            continue
        attempt = existing_attempt(root, code, target, now)
        path = em.location(root, code)
        if not item or not item.get('stock_listed_date'):
            if attempt and not path.exists():
                waiting.append({'stock_code':code, 'status':'pending', 'reason':'published_listing_identity_unavailable'})
            continue
        stock = {'stock_code': code, 'stock_name': item['stock_name'], 'listed_date': item['stock_listed_date'],
                 'market': p.symbol_for(code)[:2].upper()}
        record = p.read_json(path)
        evidence = frozen.get(code)
        if evidence and (evidence[0]['listed_date'] != stock['listed_date'] or evidence[0]['market'] != stock['market']):
            if attempt and not path.exists():
                waiting.append({'stock_code':code, 'status':'pending', 'reason':'frozen_listing_identity_mismatch'})
            continue
        if source.valid_record(record, stock):
            if p.stamp(record['fetched_at']) > now:
                continue
            valid.add(code)
            stocks[code] = stock
            if record['checked_through'] > target or delayed(attempt, target, now):
                continue
            if record['checked_through'] == target:
                has_price = record['series'] and record['series'][-1][0] == target
                if has_price or (now - p.stamp(record['fetched_at'])).total_seconds() < 7200:
                    continue
            queue.append((record['checked_through'], code))
        elif not path.exists() and attempt:
            if not evidence or evidence[2] > attempt['target_end']:
                waiting.append({'stock_code':code, 'status':'pending', 'reason':'frozen_listing_identity_unavailable'})
            elif not delayed(attempt, target, now):
                initial.add(code)
                stocks[code] = stock
                queue.append(('', code))
    return stocks, valid, [code for _, code in sorted(queue)], initial, waiting


def gate_verified(path, target):
    state = p.read_json(path)
    return bool(isinstance(state, dict) and state.get('schema') == 1 and state.get('source') == source.SOURCE
                and state.get('target_end') == target and state.get('anchors') == list(ANCHORS)
                and state.get('status') == 'verified' and state.get('klt') == 101 and state.get('fqt') == 0)


def maintain(cache_root, catalog, target, budget, *, now=None, fetcher=None, full_fetcher=None,
             interval=.6, sleeper=None, monotonic=None):
    """Called after scheduled_gate. Returns valid EM codes to exclude from Tencent."""
    clock = lambda: now or datetime.now(timezone.utc)
    sleeper, monotonic = sleeper or time.sleep, monotonic or time.monotonic
    interval = max(.6, interval)
    report = {'provider': 'eastmoney', 'scope': 'existing_provider_caches_and_verified_prior_attempts',
              'target_end': target, 'current_listings_reverified': False, 'status': 'no_due', 'network_requests': 0,
              'updated': 0, 'pending': 0, 'initial_recoveries': 0, 'halt_remaining': False, 'results': []}
    valid = set()
    if not (cache_root / 'prices/eastmoney').is_dir():
        return valid, report
    # Request libraries may log a full query on errors. Reports use local reason codes.
    previous_logging = logging.root.manager.disable
    try:
        with common.batch_lock(cache_root / 'locks/eastmoney'):
            frozen = frozen_members(cache_root, target)
            stocks, valid, queue, initial, waiting = eligible(cache_root, catalog, target, clock(), frozen)
            report['results'].extend(waiting)
            report['pending_initial_identity'] = len(waiting)
            report.update(valid_existing=len(valid), eligible_due=len(queue),
                          listing_universe_as_of=sorted({frozen[code][1] for code in stocks if code in frozen}))
            if not queue:
                return valid, report
            until = em.provider_blocked_until(cache_root, clock())
            if until:
                report.update(status='source_backoff', blocked_until=until.isoformat(), halt_remaining=True)
                return valid, report
            gate_path = cache_root / 'locks/eastmoney/delta-verification.json'
            gate = gate_verified(gate_path, target)
            report['gate_status'] = 'verified' if gate else 'waiting_verification'
            if not gate and (budget < 2 or any(code not in valid or delayed(existing_attempt(cache_root, code, target, clock()), target, clock()) for code in ANCHORS)):
                report.update(status='waiting_verification', halt_remaining=True, reason='both_anchor_checks_required')
                return valid, report
            last_start = None

            def candidate(code):
                nonlocal last_start
                stock = stocks[code]
                old = p.read_json(em.location(cache_root, code))
                is_initial = code in initial
                if not is_initial and (not source.valid_record(old, stock) or old['checked_through'] > target or not old['candles']):
                    raise Rejected('valid_historical_overlap_required')
                if not gate and code in ANCHORS and len(old['candles']) <= 5:
                    raise Rejected('anchor_total_scope_ambiguous')
                if last_start is not None:
                    sleeper(max(0., interval - (monotonic() - last_start)))
                last_start = monotonic()
                report['network_requests'] += 1
                logging.disable(logging.CRITICAL)
                try:
                    if is_initial:
                        bundle = (full_fetcher or source.fetch_history)(stock, target)
                        if not bundle['candles']:
                            raise Rejected('initial_source_pending')
                        record = source.make_record(stock,target,bundle,clock().isoformat(timespec='milliseconds'))
                    else:
                        start = old['candles'][-min(5,len(old['candles']))][0]
                        status, raw = (fetcher or probe.fetch_once)(stock, start, target)
                        if status != 200:
                            reason = 'eastmoney returned HTTP '+str(int(status))
                            raise Rejected(reason, em.cooldown(reason))
                        record = merge_response(raw, stock, old, start, target, clock().isoformat(timespec='milliseconds'))
                        if not gate and code in ANCHORS and record['candles'][-1][0] <= old['checked_through']:
                            raise Rejected('anchor_new_source_date_pending')
                except Rejected:
                    raise
                except Exception as exc:
                    reason = str(exc) if re.fullmatch(r'eastmoney returned HTTP \d{3}',str(exc)) else 'source_response_unavailable'
                    raise Rejected(reason,em.cooldown(reason)) from None
                finally:
                    logging.disable(previous_logging)
                if code in frozen:
                    record['listing_evidence'] = {'as_of': frozen[code][1], 'universe_sha256': frozen[code][3]}
                if not source.valid_record(record,stock) or len(json.dumps(record,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode())>source.MAX_BYTES:
                    raise Rejected('invalid_merged_record')
                return record

            def save(code, record):
                p.atomic_json(em.location(cache_root,code),record)
                valid.add(code)
                last = record['series'][-1][0] if record['series'] else None
                report['updated'] += 1
                report['pending'] += int(last != target)
                report['initial_recoveries'] += int(code in initial)
                report['results'].append({'stock_code':code, 'available_end':last, 'provider_total':record['provider_total'],
                                          'status':'updated' if last==target else 'pending'})

            def reject(code, error):
                moment = clock()
                p.atomic_json(em.attempt_location(cache_root,code), {'attempted_at':moment.isoformat(timespec='milliseconds'),
                              'target_end':target,'reason':error.reason})
                report['results'].append({'stock_code':code,'status':'retained_previous' if code in valid else 'pending', 'reason':error.reason})
                if error.cooldown:
                    until = em.persist_provider_stop(cache_root,error.reason,error.cooldown,moment)
                    report.update(status='source_backoff', blocked_until=until.isoformat(),halt_remaining=True)

            if not gate:
                staged = {}
                for code in ANCHORS:
                    try:
                        staged[code] = candidate(code)
                    except Rejected as error:
                        report.update(status='waiting_verification',halt_remaining=True,reason=error.reason)
                        reject(code,error)
                        return valid, report
                p.atomic_json(gate_path,{'schema':1,'source':source.SOURCE,'target_end':target,'status':'verified',
                              'klt':101,'fqt':0,'anchors':list(ANCHORS),'verified_at':clock().isoformat(),
                              'evidence':{code:record['update_evidence'] for code,record in staged.items()}})
                # Both responses verified above. Persist their evidence first so that
                # a process interruption between the two atomic price writes can resume.
                for code, record in staged.items():
                    save(code,record)
                report['gate_status'] = 'verified'
                queue = [code for code in queue if code not in staged]
            report['status'] = 'ok'
            consecutive_failures = 0
            for code in queue:
                if report['network_requests'] >= budget:
                    report['status'] = 'job_limit'
                    break
                try:
                    save(code,candidate(code))
                    consecutive_failures = 0
                except Rejected as error:
                    reject(code,error)
                    if error.cooldown:
                        break
                    consecutive_failures += 1
                    if consecutive_failures >= 5:
                        until = em.persist_provider_stop(cache_root,'repeated_delta_validation_errors',900,clock())
                        report.update(status='source_backoff', reason='five_consecutive_errors',
                                      blocked_until=until.isoformat(), halt_remaining=True)
                        break
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        report.update(status='local_state_unavailable', halt_remaining=True, reason=type(exc).__name__)
    finally:
        logging.disable(previous_logging)
    return valid,report
