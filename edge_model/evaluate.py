"""Score a checkpoint on held-out scene seeds in the three scenarios, with and without it.

Rows (each over held-out seeds x tables):
  autonomous/expert     the scripted expert (reference, no model)
  autonomous/untrained  same network, random weights (the 'before' row)
  autonomous/model      the trained policy alone
  lag/hold              chunked teacher with Dream-like late replies, hold on late (no model)
  lag/bridge            same replies, the edge model bridges while no reply is in hand
  assist/alone          simulated operator with 2-4 cm aim error (no model)
  assist/assist         same operator, the model snaps the last centimetres

Every attempt writes one control-center session (geotech.telemetry/1) under
sessions_dir. Kinematic bench, not physics: these are simulation results, not
physical ones.
"""
import json
import os
import platform
import time

import numpy as np

from edge_model import scenarios
from edge_model.model import Policy
from edge_model.telemetry_log import SessionLog

ROWS = (('autonomous', 'expert'), ('autonomous', 'untrained'), ('autonomous', 'model'),
        ('lag', 'hold'), ('lag', 'bridge'), ('assist', 'alone'), ('assist', 'assist'))


def _run(scenario, variant, seed, table, policy, untrained, log):
    if scenario == 'autonomous':
        pol = untrained if variant == 'untrained' else policy
        return scenarios.autonomous(seed, table, variant, pol, log=log)
    if scenario == 'lag':
        return scenarios.lag(seed, table, variant, policy, log=log)
    return scenarios.assist(seed, table, variant, policy, log=log)


def _limits(scenario):
    if scenario == 'lag':
        return {'rtt_threshold_ms': scenarios.THRESHOLD_MS, 'window': scenarios.WINDOW,
                'chunk_actions': scenarios.CHUNK_STEPS * scenarios.ACTIONS_PER_STEP, 'blend_actions': 0}
    return {}


def _pct(values, q):
    return round(float(np.percentile(values, q)), 3) if values else None


def _med(values):
    return round(float(np.median(values)), 3) if values else None


def aggregate(attempts):
    rows = {}
    for scenario, variant in ROWS:
        group = [a for a in attempts if a['scenario'] == scenario and a['variant'] == variant]
        if not group:
            continue
        n = len(group)
        ok = [a for a in group if a['success']]
        row = {
            'attempts': n, 'success': len(ok), 'success_rate': round(len(ok) / n, 4),
            'wrong_cup_attempts': sum(bool(a['wrong_cup']) for a in group),
            'drop_attempts': sum(a['drops'] > 0 for a in group), 'drops_total': sum(a['drops'] for a in group),
            'collision_attempts': sum(a['collisions'] > 0 for a in group),
            'collisions_total': sum(a['collisions'] for a in group),
            'gate_pass_s_median': _med([a['passed_at_s'] for a in ok]),
            'peak_commanded_speed_m_s': round(max(a['speed']['peak_requested'] for a in group), 4),
            'speed_clamps_total': sum(a['speed']['clamps'] for a in group),
            'failure_reasons': {},
        }
        for a in group:
            if not a['success']:
                key = (a['reasons'] or ['no hold'])[0]
                row['failure_reasons'][key] = row['failure_reasons'].get(key, 0) + 1
        lat = [a['latency_ms'] for a in group if a.get('latency_ms')]
        if lat:
            row['inference_ms'] = {'median_of_attempt_medians': _med([x['median'] for x in lat]),
                                   'p99_worst_attempt': round(max(x['p99'] for x in lat), 4),
                                   'calls': sum(x['calls'] for x in lat),
                                   'note': 'single-sample numpy forward pass on this Mac, wall clock'}
        if scenario == 'lag':
            L = [a['lag'] for a in group]
            frozen = [x['frozen_s'] for x in L]
            row['lag'] = {
                'frozen_s_mean': round(float(np.mean(frozen)), 3), 'frozen_s_median': _med(frozen),
                'frozen_s_p95': _pct(frozen, 95), 'frozen_s_total': round(float(np.sum(frozen)), 1),
                # Split: waiting for the very first chunk (the bridge starts moving at t = 0 instead) vs later gaps.
                'frozen_before_first_reply_s_mean': round(float(np.mean([x['frozen_before_first_reply_s'] for x in L])), 3),
                'frozen_after_first_reply_s_mean': round(float(np.mean([x['frozen_after_first_reply_s'] for x in L])), 3),
                'frozen_after_first_reply_s_p95': _pct([x['frozen_after_first_reply_s'] for x in L], 95),
                'bridge_s_mean': round(float(np.mean([x['bridge_s'] for x in L])), 3),
                'time_to_hold_s_median': _med([x['time_to_hold_s'] for x in L if x['time_to_hold_s'] is not None]),
                'requests': sum(x['requests'] for x in L), 'late': sum(x['late'] for x in L),
                'failed': sum(x['failed'] for x in L), 'stale_replies_dropped': sum(x['stale_dropped'] for x in L),
                'chunks_played': sum(x['chunks_played'] for x in L),
                'skipped_actions': sum(x['skipped_actions'] for x in L),
                'bridge_cap_hits': sum(x['bridge_cap_hits'] for x in L),
            }
        if scenario == 'assist':
            aims = [a['aim_error_cm'] for a in group]
            row['aim_error_cm'] = {'min': round(min(aims), 2), 'median': _med(aims), 'max': round(max(aims), 2)}
            row['assist_steps_mean'] = round(float(np.mean([a['assist_steps'] for a in group])), 2)
        rows['%s/%s' % (scenario, variant)] = row
    return rows


def paired_lag(attempts):
    """Per-attempt differences where both lag variants passed the gate on the same table and seed."""
    by = {}
    for a in attempts:
        if a['scenario'] == 'lag':
            by.setdefault((a['seed'], a['table']), {})[a['variant']] = a
    d_frozen, d_first, d_hold = [], [], []
    for pair in by.values():
        if 'hold' in pair and 'bridge' in pair:
            d_frozen.append(pair['hold']['lag']['frozen_s'] - pair['bridge']['lag']['frozen_s'])
            d_first.append(pair['hold']['lag']['frozen_before_first_reply_s']
                           - pair['bridge']['lag']['frozen_before_first_reply_s'])
            if pair['hold']['success'] and pair['bridge']['success']:
                d_hold.append(pair['hold']['lag']['time_to_hold_s'] - pair['bridge']['lag']['time_to_hold_s'])
    mean = lambda xs: round(float(np.mean(xs)), 3) if xs else None  # noqa: E731
    return {'pairs': len(d_frozen), 'frozen_s_saved_mean': mean(d_frozen),
            'frozen_s_saved_before_first_reply_mean': mean(d_first),
            'frozen_s_saved_after_first_reply_mean': mean([a - b for a, b in zip(d_frozen, d_first)]),
            'both_passed': len(d_hold),
            'time_to_hold_s_saved_median': _med(d_hold), 'time_to_hold_s_saved_mean': mean(d_hold)}


def realized_rtts(seeds, tables, upto=60):
    """The round-trip stream itself (first `upto` requests per table), for the record."""
    vals, fails = [], 0
    for s in seeds:
        for t in tables:
            rt = scenarios.RoundTrips(s, t)
            for v in rt.values[:upto]:
                if v is None:
                    fails += 1
                else:
                    vals.append(v)
    return {'requests': len(vals) + fails, 'median_ms': _med(vals), 'p95_ms': _pct(vals, 95),
            'over_850_ms_fraction': round(float(np.mean(np.array(vals) > 850)), 4) if vals else None,
            'failed_fraction': round(fails / max(1, len(vals) + fails), 4)}


def validate_sessions(sessions_dir, ids):
    from control_center import telemetry
    bad, events = 0, 0
    for sid in ids:
        evs, _ = telemetry.read(os.path.join(sessions_dir, sid, 'events.jsonl'))
        events += len(evs)
        bad += sum(1 for e in evs if telemetry.validate(e))
    return {'sessions': len(ids), 'events': events, 'invalid_events': bad}


def evaluate(policy_path, seeds, tables, out_dir, sessions_dir, rows=ROWS, log=print, write_sessions=True):
    policy = Policy.load(policy_path)
    hidden = tuple(policy.meta.get('config', {}).get('hidden', [128, 128]))
    untrained = Policy.untrained(hidden=hidden, seed=1, norm=policy.norm)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    os.makedirs(out_dir, exist_ok=True)
    attempts, session_ids = [], []
    t0 = time.time()
    for scenario, variant in rows:
        t_row = time.time()
        for seed in seeds:
            for table in tables:
                slog = SessionLog(sessions_dir, scenario, variant, seed, table, stamp=stamp,
                                  limits=_limits(scenario)) if write_sessions else None
                r = _run(scenario, variant, seed, table, policy, untrained, slog)
                if slog:
                    slog.finish(r)
                    session_ids.append(slog.session)
                attempts.append(dict(r, scenario=scenario, variant=variant, seed=seed, table=table,
                                     session=slog.session if slog else None))
        done = [a for a in attempts if a['scenario'] == scenario and a['variant'] == variant]
        if log:
            log('%s/%s: %d/%d passed the gate (%.1fs)' % (scenario, variant, sum(a['success'] for a in done),
                                                          len(done), time.time() - t_row))
    summary = {
        'schema': 'geotech.edge-eval/1',
        'created': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'kind': 'kinematic bench simulation (edge_model/bench.py): not physics, not a physical result',
        'checkpoint': os.path.basename(policy_path),
        'checkpoint_meta': {k: policy.meta.get(k) for k in ('data_source', 'data_manifest_sha256', 'train_samples',
                                                             'dagger_samples', 'best_epoch', 'best_val_loss',
                                                             'parameters', 'offline_heldout', 'train_seconds',
                                                             'config')},
        'heldout_seeds': list(seeds), 'tables': list(tables), 'attempts_per_row': len(seeds) * len(tables),
        'rows': aggregate(attempts),
        'lag_paired': paired_lag(attempts),
        'lag_rtt_model': scenarios.RoundTrips.describe(),
        'lag_rtt_realized': realized_rtts(seeds, tables),
        'lag_protocol': {'chunk_actions': 48, 'chunk_s': 2.4, 'bench_rate_hz': 5, 'actions_per_bench_step': 4,
                         'async_request_lead_actions': 32, 'threshold_ms': scenarios.THRESHOLD_MS,
                         'window': scenarios.WINDOW, 'timeout_s': scenarios.TIMEOUT_S,
                         'bridge_cap_s': scenarios.BRIDGE_CAP_S, 'speed_limit_m_s': scenarios.SPEED_LIMIT,
                         'teacher': 'scripted expert (not Dream), continuation chunks',
                         'frozen_time_resolution_s': 0.2},
        'assist_protocol': {'aim_error_m': list(scenarios.AIM_ERROR_M), 'zone_m': scenarios.ASSIST_ZONE_M,
                            'ramp_s': scenarios.ASSIST_RAMP_STEPS * 0.2},
        'seconds': round(time.time() - t0, 1),
        'python': platform.python_version(), 'numpy': np.__version__, 'machine': platform.machine(),
    }
    if write_sessions:
        summary['telemetry'] = dict(validate_sessions(sessions_dir, session_ids),
                                    sessions_dir=sessions_dir, component='edge-model',
                                    purpose='sim')
    with open(os.path.join(out_dir, 'eval_summary.json'), 'w') as fh:
        json.dump(summary, fh, indent=2)
    with open(os.path.join(out_dir, 'eval_attempts.jsonl'), 'w') as fh:
        for a in attempts:
            fh.write(json.dumps({k: v for k, v in a.items() if k not in ('dream_export_note',)}, default=float) + '\n')
    return summary
