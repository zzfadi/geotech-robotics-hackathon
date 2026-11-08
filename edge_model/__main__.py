"""python -m edge_model <command>   (numpy 2 + Python 3.10 or newer; on the Mac use arm/.venv/bin/python)

  run-all    collect -> dataset -> train -> evaluate, end to end (--quick for a small smoke run)
  collect    run the scripted expert on the bench and write the dataset
  train      train the numpy MLP on a dataset
  evaluate   score a checkpoint on held-out seeds in the three scenarios

Everything is written under --out (default edge_model/local/, git-ignored).
Kinematic bench, not physics; nothing moves a physical arm or calls an API.
"""
import argparse
import json
import os
import sys
import time

from edge_model import collect as col
from edge_model import dataset as ds
from edge_model import evaluate as ev
from edge_model import train as tr

HERE = os.path.dirname(os.path.abspath(__file__))
PRESETS = {
    'quick': {'seeds': 10, 'variants': ('clean',), 'epochs': 30, 'eval_seeds': 2},
    'full': {'seeds': 100, 'variants': col.DEFAULT_VARIANTS, 'epochs': 150, 'eval_seeds': None},
}
SEED_START = 1000


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def do_collect(out, n_seeds, variants, dagger_policy=None):
    seeds = list(range(SEED_START, SEED_START + n_seeds))
    t0 = time.time()
    episodes, splits = col.collect(seeds, variants=variants, log=_log)
    cfg = {'seed_start': SEED_START, 'seeds': n_seeds, 'tables': list(col.TABLES), 'variants': list(variants),
           'variant_settings': {k: col.VARIANTS[k] for k in set(variants)}, 'est_sigma_m': col.EST_SIGMA_M,
           'max_steps': col.MAX_STEPS, 'after_pass_steps': col.AFTER_PASS_STEPS}
    manifest = ds.write(os.path.join(out, 'dataset'), episodes, splits, cfg, time.time() - t0)
    return episodes, splits, cfg, manifest


def run_all(a):
    preset = dict(PRESETS['quick' if a.quick else 'full'])
    if a.seeds:
        preset['seeds'] = a.seeds
    if a.epochs:
        preset['epochs'] = a.epochs
    # --quick never overwrites the full run's dataset, checkpoint and scores unless --out says so.
    out = a.out or os.path.join(HERE, 'local', 'quick' if a.quick else '')
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    _log('== collect: %d seeds x 9 tables x %d variants' % (preset['seeds'], len(preset['variants'])))
    episodes, splits, cfg, manifest = do_collect(out, preset['seeds'], preset['variants'])
    _log('   %d episodes, %d gate-passing; files %s' % (manifest['episodes_total'], manifest['gate_passing'],
                                                       {k: v['samples'] for k, v in manifest['files'].items()}))
    data_dir = os.path.join(out, 'dataset')
    ckpt = os.path.join(out, 'checkpoints', 'policy.npz')
    _log('== train: %d epochs' % preset['epochs'])
    policy, meta = tr.train(data_dir, ckpt, {'epochs': preset['epochs']},
                            log=lambda r: _log('   epoch %(epoch)d train %(train_loss).4f val %(val_loss).4f' % r)
                            if r['epoch'] % 10 == 0 else None)
    relabelled = []
    for round_ in range(a.dagger):
        _log('== dagger round %d on training seeds' % (round_ + 1))
        relabelled += col.relabel(policy, splits['train'], log=_log)
        manifest = ds.write(data_dir, episodes, splits, cfg, relabelled=relabelled)
        policy, meta = tr.train(data_dir, ckpt, {'epochs': preset['epochs']}, include_dagger=True)
    _log('   best epoch %s, val loss %s, held-out offline %s' % (meta['best_epoch'], meta['best_val_loss'],
                                                                  meta['offline_heldout']))
    held = splits['heldout'][:preset['eval_seeds']] if preset['eval_seeds'] else splits['heldout']
    _log('== evaluate on held-out seeds %s' % held)
    summary = ev.evaluate(ckpt, held, list(col.TABLES), os.path.join(out, 'eval'), os.path.join(out, 'sessions'),
                          log=_log)
    summary['pipeline_seconds'] = round(time.time() - t0, 1)
    summary['preset'] = 'quick' if a.quick else 'full'
    summary['dataset'] = {k: manifest[k] for k in ('source', 'episodes_total', 'gate_passing', 'failure_reasons',
                                                    'seeds')}
    summary['dataset']['files'] = {k: {'episodes': v['episodes'], 'samples': v['samples'], 'sha256': v['sha256']}
                                   for k, v in manifest['files'].items()}
    summary['dataset']['manifest_sha256'] = ds.manifest_hash(data_dir)
    with open(os.path.join(out, 'eval', 'eval_summary.json'), 'w') as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps({k: {kk: v.get(kk) for kk in ('attempts', 'success', 'collision_attempts', 'drop_attempts',
                                                      'wrong_cup_attempts', 'peak_commanded_speed_m_s')}
                      for k, v in summary['rows'].items()}, indent=1))
    _log('== done in %.0fs; summary at %s' % (time.time() - t0, os.path.join(out, 'eval', 'eval_summary.json')))
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m edge_model', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', help='output folder (default edge_model/local/; run-all --quick: edge_model/local/quick/)')
    sub = p.add_subparsers(dest='command')
    r = sub.add_parser('run-all', help='collect, train and evaluate')
    r.add_argument('--quick', action='store_true', help='10 seeds, clean variant only, 30 epochs, 2 held-out seeds')
    r.add_argument('--seeds', type=int, help='scene seeds (split 70/10/20 by seed)')
    r.add_argument('--epochs', type=int)
    r.add_argument('--dagger', type=int, default=0, help='DAgger rounds on training seeds (expert relabels)')
    c = sub.add_parser('collect')
    c.add_argument('--seeds', type=int, default=100)
    c.add_argument('--quick', action='store_true')
    t = sub.add_parser('train')
    t.add_argument('--epochs', type=int, default=150)
    t.add_argument('--dagger', action='store_true', help='include dagger.npz if present')
    e = sub.add_parser('evaluate')
    e.add_argument('--checkpoint', default=None)
    e.add_argument('--seeds', type=int, nargs='*', help='held-out seeds (default: from the dataset manifest)')
    e.add_argument('--no-sessions', action='store_true')
    a = p.parse_args(argv)
    if a.command != 'run-all':
        a.out = a.out or os.path.join(HERE, 'local')
    if a.command == 'run-all':
        run_all(a)
    elif a.command == 'collect':
        variants = ('clean',) if a.quick else col.DEFAULT_VARIANTS
        _, _, _, m = do_collect(a.out, a.seeds, variants)
        print(json.dumps({k: m[k] for k in ('episodes_total', 'gate_passing', 'failure_reasons', 'files')}, indent=2))
    elif a.command == 'train':
        _, meta = tr.train(os.path.join(a.out, 'dataset'), os.path.join(a.out, 'checkpoints', 'policy.npz'),
                           {'epochs': a.epochs}, include_dagger=a.dagger)
        print(json.dumps({k: meta[k] for k in ('train_samples', 'best_epoch', 'best_val_loss', 'offline_heldout')},
                         indent=2))
    elif a.command == 'evaluate':
        ckpt = a.checkpoint or os.path.join(a.out, 'checkpoints', 'policy.npz')
        seeds = a.seeds
        if not seeds:
            with open(os.path.join(a.out, 'dataset', 'manifest.json')) as fh:
                seeds = json.load(fh)['seeds']['heldout']
        s = ev.evaluate(ckpt, seeds, list(col.TABLES), os.path.join(a.out, 'eval'), os.path.join(a.out, 'sessions'),
                        log=_log, write_sessions=not a.no_sessions)
        print(json.dumps(s['rows'], indent=1))
    else:
        p.print_help()


if __name__ == '__main__':
    main()
