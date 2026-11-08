"""Collect scripted-expert episodes on the kinematic bench (numpy only).

For every scene seed and every one of the nine fleet tables, the scripted
expert (expert.py) runs on BenchTable and we record, per 0.2 s step, the
14-number observation model.py reads and the expert's action at that state:
the joint step (target minus measured joints, 6 radians) and the grip target
(1 open, 0 closed).

Variants per scene add diversity without leaving the expert's own behaviour:
  clean      start at the fleet home pose, expert executes its own targets
  perturbed  start near home (joint noise) and execute the expert's targets
             with small joint noise during travel/approach only (DART-style);
             the label stays the expert's clean action at the visited state

Gate-passing episodes (gate.py, target cup only) are demonstrations; every
failure goes to a separate eval set and is never relabelled. Splits are by
scene seed (train / val / held-out) before anything is trained.

The cup position the expert plans from and the model observes is an estimate
(the true target centre plus a few millimetres of noise), standing in for the
overhead camera. Nothing here is physics: see bench.py.
"""
import time

import numpy as np

from edge_model import gate
from edge_model.bench import ACTION_NAMES, DT, OBS_NAMES, BenchTable
from edge_model.expert import Expert
from edge_model.kinematics import HOME

TABLES = tuple(range(9))
VARIANTS = {
    'clean': {'q0_sigma': 0.0, 'travel_sigma': 0.0, 'approach_sigma': 0.0},
    'perturbed': {'q0_sigma': 0.10, 'travel_sigma': 0.03, 'approach_sigma': 0.005},
}
DEFAULT_VARIANTS = ('clean', 'perturbed', 'perturbed')
EST_SIGMA_M = 0.003       # cup-estimate noise (stand-in for the overhead detector)
MAX_STEPS = 150           # 30 s at 5 Hz
AFTER_PASS_STEPS = 10     # keep 2 s of steady hold after the gate passes


def split_seeds(seeds, fractions=(0.7, 0.1, 0.2)):
    """Deterministic split by scene seed: the first 70 % train, 10 % val, 20 % held-out."""
    seeds = list(seeds)
    n = len(seeds)
    n_train = max(1, int(round(fractions[0] * n)))
    n_val = max(1, int(round(fractions[1] * n))) if n >= 3 else 0
    n_held = n - n_train - n_val
    if n_held < 1 and n >= 2:
        n_train -= 1
        n_held = 1
    return {'train': seeds[:n_train], 'val': seeds[n_train:n_train + n_val],
            'heldout': seeds[n_train + n_val:]}


def cup_estimate(table, rng, sigma=EST_SIGMA_M):
    est = table.cup(table.target).center.copy()
    est[:2] += rng.normal(0.0, sigma, 2)
    return est


def episode_rng(seed, table_index, variant_index, salt=0):
    return np.random.default_rng([int(seed), int(table_index), int(variant_index), int(salt), 7741])


def wrong_cup(table):
    return sorted(table.ever_held - {table.target})


def outcome(table, times, evidence):
    """Gate result plus the counts every scenario reports."""
    wrong = wrong_cup(table)
    result = gate.evaluate(times, evidence, target_held_ok=not wrong)
    collisions = [e for e in table.events if e['kind'] == 'collision']
    drops = [e for e in table.events if e['kind'] == 'drop']
    grasps = [e for e in table.events if e['kind'] == 'grasp']
    result.update({
        'success': bool(result['measured_pass']),
        'wrong_cup': wrong,
        'drops': len(drops),
        'collisions': len(collisions),
        'cups_knocked': len({e.get('cup') for e in collisions if e.get('what') == 'cup'}),
        'first_grasp_s': grasps[0]['t'] if grasps else None,
    })
    return result


def run_expert_episode(table_index, seed, variant='clean', variant_index=0, max_steps=MAX_STEPS,
                       after_pass=AFTER_PASS_STEPS):
    """One expert episode; returns observations, labels, gate result and provenance."""
    cfg = VARIANTS[variant]
    rng = episode_rng(seed, table_index, variant_index)
    q0 = HOME + rng.normal(0.0, cfg['q0_sigma'], 6) if cfg['q0_sigma'] else None
    table = BenchTable(table_index=table_index, seed=seed, q0=q0)
    est = cup_estimate(table, rng)
    expert = Expert(est)
    obs, act, phases, times, evidence = [], [], [], [], []
    stop_at = max_steps
    for step in range(max_steps):
        o = table.observe(est)
        q_target, grip, phase = expert.act(table)
        obs.append(o)
        act.append(np.r_[q_target - table.q, grip].astype(np.float32))
        phases.append(phase)
        sigma = cfg['travel_sigma'] if phase == 'travel' else cfg['approach_sigma'] if phase == 'approach' else 0.0
        executed = q_target + rng.normal(0.0, sigma, 6) if sigma else q_target
        table.step(executed, grip)
        times.append(round((step + 1) * DT, 6))
        evidence.append(table.evidence())
        if stop_at == max_steps:
            g = gate.evaluate(times, evidence)
            if g['passed_at_s'] is not None:
                stop_at = step + after_pass
        if step >= stop_at:
            break
    result = outcome(table, times, evidence)
    return {
        'seed': int(seed), 'table': int(table_index), 'variant': variant, 'variant_index': int(variant_index),
        'cup_estimate': [round(float(x), 6) for x in est],
        'cup_true': [round(float(x), 6) for x in table.cup(table.target).center],
        'obs': np.asarray(obs, dtype=np.float32), 'act': np.asarray(act, dtype=np.float32),
        'phases': phases, 'steps': len(obs), 'result': result,
        'demonstration': bool(result['success']),
    }


def collect(seeds, tables=TABLES, variants=DEFAULT_VARIANTS, log=None):
    """Run the expert over seeds x tables x variants. Returns episodes with their split."""
    splits = split_seeds(seeds)
    split_of = {s: name for name, group in splits.items() for s in group}
    episodes = []
    t0 = time.time()
    for seed in seeds:
        for table_index in tables:
            for vi, variant in enumerate(variants):
                ep = run_expert_episode(table_index, seed, variant, vi)
                ep['split'] = split_of[seed]
                episodes.append(ep)
        if log:
            done = [e for e in episodes if e['seed'] == seed]
            log('seed %d (%s): %d/%d gate-passing, %.1fs elapsed'
                % (seed, split_of[seed], sum(e['demonstration'] for e in done), len(done), time.time() - t0))
    return episodes, splits


__all__ = ['collect', 'run_expert_episode', 'split_seeds', 'OBS_NAMES', 'ACTION_NAMES']


# ---------------------------------------------------------------- DAgger relabelling
def relabel_rollout(policy, table_index, seed, kind='autonomous', variant_index=0, max_steps=MAX_STEPS):
    """States visited by the policy (alone, or blended with a simulated operator), each
    labelled with the scripted expert's action at that state (DAgger). The policy's
    own actions are never used as targets; labels are always the expert's."""
    import math
    from edge_model.scenarios import AIM_ERROR_M, Assist, ModelDriver, clamp_speed
    rng = episode_rng(seed, table_index, variant_index, salt=11)
    table = BenchTable(table_index=table_index, seed=seed)
    est = cup_estimate(table, rng)
    labeller = Expert(est)
    driver = ModelDriver(policy, est)
    operator = helper = None
    if kind == 'assist':
        angle, err = rng.uniform(0, 2 * math.pi), rng.uniform(*AIM_ERROR_M)
        aim = est.copy()
        aim[:2] += err * np.array([math.cos(angle), math.sin(angle)])
        operator, helper = Expert(aim), Assist(driver, est)
    obs, act, times, evidence = [], [], [], []
    for step in range(max_steps):
        o = table.observe(est)
        q_label, g_label, _ = labeller.act(table)
        obs.append(o)
        act.append(np.r_[q_label - table.q, g_label].astype(np.float32))
        if helper is not None:
            q_op, g_op, _ = operator.act(table)
            q_m, g_m, _ = helper.blend(table, q_op, g_op)
        else:
            q_m, g_m, _ = driver.act(table)
        table.step(clamp_speed(table.arm, table.q, q_m)[0], g_m)
        times.append(round((step + 1) * DT, 6))
        evidence.append(table.evidence())
    result = outcome(table, times, evidence)
    return {
        'seed': int(seed), 'table': int(table_index), 'variant': 'dagger-' + kind, 'variant_index': int(variant_index),
        'obs': np.asarray(obs, dtype=np.float32), 'act': np.asarray(act, dtype=np.float32),
        'steps': len(obs), 'result': result, 'demonstration': False, 'split': 'train',
        'rollout_success': bool(result['success']),
    }


def relabel(policy, seeds, tables=TABLES, kinds=('autonomous', 'assist'), log=None):
    out = []
    t0 = time.time()
    for seed in seeds:
        for table_index in tables:
            for vi, kind in enumerate(kinds):
                out.append(relabel_rollout(policy, table_index, seed, kind, vi))
        if log:
            done = [e for e in out if e['seed'] == seed]
            log('dagger seed %d: policy rollouts passing %d/%d, %.1fs elapsed'
                % (seed, sum(e['rollout_success'] for e in done), len(done), time.time() - t0))
    return out
