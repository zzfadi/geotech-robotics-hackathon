"""Train the small policy on the bench-expert dataset with model.py's numpy trainer.

Writes the checkpoint (.npz, loadable by model.Policy.load with numpy only) and
records its provenance inside the checkpoint's meta and next to it as JSON:
config, dataset manifest sha256 and per-file hashes, wall time, library and
Python versions, platform, sample counts and the loss history.
"""
import json
import os
import platform
import sys
import time

import numpy as np

from edge_model import dataset as ds
from edge_model.model import train_numpy

DEFAULT_CONFIG = {'hidden': [128, 128], 'epochs': 60, 'batch_size': 256, 'lr': 1e-3,
                  'weight_decay': 1e-5, 'grip_weight': 0.5, 'seed': 0}


def train(data_dir, out_path, config=None, log=None, include_dagger=False):
    cfg = dict(DEFAULT_CONFIG, **(config or {}))
    obs, act, _ = ds.load(data_dir, 'train')
    dagger_samples = 0
    if include_dagger:
        dobs, dact, _ = ds.load(data_dir, 'dagger')
        dagger_samples = int(len(dobs))
        obs, act = np.concatenate([obs, dobs]), np.concatenate([act, dact])
    vobs, vact, _ = ds.load(data_dir, 'val')
    if not len(obs):
        raise ValueError('No gate-passing training episodes in %s' % data_dir)
    with open(os.path.join(data_dir, 'manifest.json')) as fh:
        manifest = json.load(fh)
    t0 = time.time()
    policy, history = train_numpy((obs, act), (vobs, vact), cfg, log=log)
    seconds = time.time() - t0
    best = min(history, key=lambda r: r['val_loss'])
    # Held-out demonstrations give an offline loss on seeds never used for training or selection.
    hobs, hact, _ = ds.load(data_dir, 'heldout')
    offline = None
    if len(hobs):
        dq, grip = policy.predict(hobs)
        offline = {
            'heldout_samples': int(len(hobs)),
            'dq_rmse_rad': round(float(np.sqrt(((dq - hact[:, :6]) ** 2).mean())), 5),
            'grip_accuracy': round(float(((grip > 0.5) == (hact[:, 6] > 0.5)).mean()), 4),
        }
    meta = {
        'trained': True,
        'trainer': 'numpy (edge_model/model.py train_numpy)',
        'config': cfg,
        'data_source': manifest['source'],
        'data_manifest_sha256': ds.manifest_hash(data_dir),
        'data_files_sha256': {k: v['sha256'] for k, v in manifest['files'].items()},
        'train_samples': int(len(obs)), 'val_samples': int(len(vobs)),
        'dagger_samples': dagger_samples, 'includes_dagger': bool(include_dagger),
        'train_episodes': manifest['files']['train']['episodes'],
        'seeds': manifest['seeds'],
        'train_seconds': round(seconds, 2),
        'created': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'python': platform.python_version(), 'numpy': np.__version__,
        'platform': platform.platform(), 'machine': platform.machine(),
        'parameters': policy.parameter_count,
        'best_epoch': best['epoch'], 'best_val_loss': best['val_loss'],
        'final_train_loss': history[-1]['train_loss'],
        'offline_heldout': offline,
        'git_commit': manifest.get('git_commit'),
        'note': 'Kinematic bench expert labels only; not physics; not trained on Dream outputs.',
    }
    policy.meta = meta
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    policy.save(out_path)
    with open(os.path.splitext(out_path)[0] + '.json', 'w') as fh:
        json.dump(dict(meta, history=history, checkpoint=os.path.basename(out_path),
                       checkpoint_sha256=ds.sha256(out_path)), fh, indent=2)
    return policy, meta


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', default='edge_model/local/dataset')
    p.add_argument('--out', default='edge_model/local/checkpoints/policy.npz')
    p.add_argument('--epochs', type=int)
    a = p.parse_args(argv)
    cfg = {'epochs': a.epochs} if a.epochs else {}
    _, meta = train(a.data, a.out, cfg, log=lambda r: print(r, file=sys.stderr))
    print(json.dumps({k: meta[k] for k in ('train_samples', 'best_epoch', 'best_val_loss', 'offline_heldout',
                                           'train_seconds', 'parameters')}, indent=2))


if __name__ == '__main__':
    main()
