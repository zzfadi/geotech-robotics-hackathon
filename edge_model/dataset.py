"""Bench-expert dataset on disk: one .npz per split plus a manifest (numpy only).

Layout (default edge_model/local/dataset/, git-ignored):
  train.npz, val.npz, heldout.npz   gate-passing expert demonstrations, by scene seed
  eval_failures.npz                 every episode that failed the gate (never trained on)
  manifest.json                     source, seeds per split, counts, sha256 of each file

Each .npz holds obs (N,14) float32, act (N,7) float32, episode (N,) int32, step
(N,) int32, and per-episode arrays ep_seed, ep_table, ep_variant, ep_passed,
ep_steps. source is always 'bench-expert': labels are the scripted expert's own
actions on the kinematic bench, never Dream output (see the terms note in
docs/handoffs/edge-model.md). Bench data is never Dream-export eligible.
"""
import hashlib
import json
import os
import platform
import subprocess
import time

import numpy as np

from edge_model.bench import ACTION_NAMES, OBS_NAMES

SOURCE = 'bench-expert'
FILES = {'train': 'train.npz', 'val': 'val.npz', 'heldout': 'heldout.npz', 'eval_failures': 'eval_failures.npz',
         'dagger': 'dagger.npz'}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def git_commit():
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=5,
                              cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _pack(episodes):
    variants = sorted({e['variant'] for e in episodes}) or ['clean']
    obs = [e['obs'] for e in episodes]
    act = [e['act'] for e in episodes]
    return {
        'obs': np.concatenate(obs).astype(np.float32) if obs else np.zeros((0, len(OBS_NAMES)), np.float32),
        'act': np.concatenate(act).astype(np.float32) if act else np.zeros((0, len(ACTION_NAMES)), np.float32),
        'episode': np.concatenate([np.full(e['steps'], i, np.int32) for i, e in enumerate(episodes)])
        if episodes else np.zeros(0, np.int32),
        'step': np.concatenate([np.arange(e['steps'], dtype=np.int32) for e in episodes])
        if episodes else np.zeros(0, np.int32),
        'ep_seed': np.array([e['seed'] for e in episodes], np.int64),
        'ep_table': np.array([e['table'] for e in episodes], np.int32),
        'ep_variant': np.array([variants.index(e['variant']) for e in episodes], np.int32),
        'ep_passed': np.array([e['demonstration'] for e in episodes], bool),
        'ep_steps': np.array([e['steps'] for e in episodes], np.int32),
        'variant_names': np.array(variants),
    }


def write(out_dir, episodes, splits, collect_config, seconds=None, relabelled=None):
    """Write the split files and the manifest; returns the manifest dict.

    relabelled: optional DAgger episodes (states the policy visited on TRAIN seeds,
    labelled with the expert's action there). Kept in their own file, never mixed
    into the demonstrations or the eval set."""
    os.makedirs(out_dir, exist_ok=True)
    groups = {
        'train': [e for e in episodes if e['split'] == 'train' and e['demonstration']],
        'val': [e for e in episodes if e['split'] == 'val' and e['demonstration']],
        'heldout': [e for e in episodes if e['split'] == 'heldout' and e['demonstration']],
        'eval_failures': [e for e in episodes if not e['demonstration']],
    }
    if relabelled:
        if any(e['seed'] not in splits['train'] for e in relabelled):
            raise ValueError('DAgger relabelling may only use training seeds')
        groups['dagger'] = list(relabelled)
    files = {}
    for name, eps in groups.items():
        path = os.path.join(out_dir, FILES[name])
        np.savez_compressed(path, **_pack(eps))
        files[name] = {
            'file': FILES[name], 'sha256': sha256(path), 'episodes': len(eps),
            'samples': int(sum(e['steps'] for e in eps)),
        }
    failures = {}
    for e in groups['eval_failures']:
        key = (e['result']['reasons'] or ['unknown'])[0]
        failures[key] = failures.get(key, 0) + 1
    manifest = {
        'schema': 'geotech.edge-dataset/1',
        'source': SOURCE,
        'source_note': ('Scripted expert (edge_model/expert.py) on the kinematic bench (edge_model/bench.py). '
                        'Not physics, not Dream output, never Dream-export eligible.'),
        'created': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'collect_seconds': None if seconds is None else round(seconds, 1),
        'git_commit': git_commit(),
        'python': platform.python_version(),
        'numpy': np.__version__,
        'obs_names': list(OBS_NAMES),
        'action_names': list(ACTION_NAMES),
        'action_note': 'dq = expert joint target minus measured joints (rad); grip_target 1 open, 0 closed',
        'rate_hz': 5,
        'split_by': 'scene seed',
        'seeds': {k: list(v) for k, v in splits.items()},
        'tables': sorted({e['table'] for e in episodes}),
        'collect_config': collect_config,
        'episodes_total': len(episodes),
        'gate_passing': sum(e['demonstration'] for e in episodes),
        'failure_reasons': failures,
        'files': files,
        'dream_export_eligible': False,
    }
    if relabelled:
        manifest['dagger_note'] = ('dagger.npz: states visited by the trained policy (alone and blended with a '
                                   'simulated operator) on training seeds only, each labelled with the scripted '
                                   "expert's action at that state. The policy's own actions are never targets; "
                                   'these are not demonstrations and not gate-filtered.')
        manifest['dagger_rollouts_passing'] = sum(e.get('rollout_success', False) for e in relabelled)
    with open(os.path.join(out_dir, 'manifest.json'), 'w') as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def manifest_hash(out_dir):
    return sha256(os.path.join(out_dir, 'manifest.json'))


def load(out_dir, split, verify=True):
    """(obs, act, extras) for one split; verify checks the manifest's sha256."""
    with open(os.path.join(out_dir, 'manifest.json')) as fh:
        manifest = json.load(fh)
    if manifest.get('source') != SOURCE:
        raise ValueError('Only bench-expert datasets are accepted, got %r' % manifest.get('source'))
    info = manifest['files'][split]
    path = os.path.join(out_dir, info['file'])
    if verify and sha256(path) != info['sha256']:
        raise ValueError('%s does not match its manifest sha256' % path)
    with np.load(path, allow_pickle=False) as z:
        data = {k: z[k] for k in z.files}
    return data['obs'], data['act'], data
