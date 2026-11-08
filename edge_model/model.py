"""Small arm-control policy: an MLP, numpy inference, numpy or PyTorch training.

Input: 14 numbers (6 joints, grip, tool xyz, cup offset from tool, cup height),
the cup position being what the overhead camera reports. Output: next joint
step (6 radians) and a grip command. About 20k parameters, so inference is
well under a millisecond on an Orin Nano CPU and needs only numpy. Weights
live in one .npz, whichever trainer produced them.
"""
import json
import time

import numpy as np

OBS_DIM = 14
ACT_DIM = 7


def init_params(sizes, rng):
    params = []
    for a, b in zip(sizes[:-1], sizes[1:]):
        params.append((rng.standard_normal((a, b)) * np.sqrt(2.0 / a)).astype(np.float32))
        params.append(np.zeros(b, dtype=np.float32))
    return params


def forward(params, x):
    h = x
    n = len(params) // 2
    for i in range(n):
        h = h @ params[2 * i] + params[2 * i + 1]
        if i < n - 1:
            h = np.maximum(h, 0.0)
    return h


class Policy:
    def __init__(self, params, norm, meta=None):
        self.params = [np.asarray(p, dtype=np.float32) for p in params]
        self.norm = {k: np.asarray(v, dtype=np.float32) for k, v in norm.items()}
        self.meta = meta or {}

    @property
    def parameter_count(self):
        return int(sum(p.size for p in self.params))

    def predict(self, obs):
        """obs (N,14) or (14,) -> dq (.., 6), grip probability (..,)."""
        x = (np.asarray(obs, dtype=np.float32) - self.norm['obs_mean']) / self.norm['obs_std']
        y = forward(self.params, x)
        dq = y[..., :6] * self.norm['dq_std'] + self.norm['dq_mean']
        grip = 1.0 / (1.0 + np.exp(-y[..., 6]))
        return dq, grip

    def save(self, path):
        arrays = {'p%d' % i: p for i, p in enumerate(self.params)}
        arrays.update({'norm_' + k: v for k, v in self.norm.items()})
        arrays['meta'] = np.array(json.dumps(self.meta))
        np.savez(path, **arrays)

    @classmethod
    def load(cls, path):
        with np.load(path, allow_pickle=False) as z:
            params = [z['p%d' % i] for i in range(sum(1 for k in z.files if k.startswith('p') and k[1:].isdigit()))]
            norm = {k[5:]: z[k] for k in z.files if k.startswith('norm_')}
            meta = json.loads(str(z['meta']))
        return cls(params, norm, meta)

    @classmethod
    def untrained(cls, hidden=(128, 128), seed=0, norm=None):
        """The 'before' row: same architecture, random weights."""
        rng = np.random.default_rng(seed)
        norm = norm or {'obs_mean': np.zeros(OBS_DIM), 'obs_std': np.ones(OBS_DIM),
                        'dq_mean': np.zeros(6), 'dq_std': np.full(6, 0.05)}
        return cls(init_params([OBS_DIM, *hidden, ACT_DIM], rng), norm, {'trained': False})


def normalization(obs, act):
    return {'obs_mean': obs.mean(0), 'obs_std': obs.std(0) + 1e-3,
            'dq_mean': act[:, :6].mean(0), 'dq_std': act[:, :6].std(0) + 1e-4}


def _loss_parts(y, dq_t, grip_t, grip_weight):
    err = y[:, :6] - dq_t
    logits = y[:, 6]
    p = 1.0 / (1.0 + np.exp(-logits))
    bce = -(grip_t * np.log(p + 1e-7) + (1 - grip_t) * np.log(1 - p + 1e-7))
    return float((err ** 2).mean()), float(bce.mean()), err, p


def train_numpy(train, val, cfg, log=None):
    """Plain numpy MLP + Adam. train/val: (obs, act) arrays. Returns Policy, history."""
    rng = np.random.default_rng(cfg.get('seed', 0))
    obs, act = train
    norm = normalization(obs, act)
    hidden = list(cfg.get('hidden', [128, 128]))
    params = init_params([OBS_DIM] + hidden + [ACT_DIM], rng)
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    lr, b1, b2, gw = cfg.get('lr', 1e-3), 0.9, 0.999, cfg.get('grip_weight', 0.5)
    wd = cfg.get('weight_decay', 1e-5)

    def prep(o, a):
        x = ((o - norm['obs_mean']) / norm['obs_std']).astype(np.float32)
        return x, ((a[:, :6] - norm['dq_mean']) / norm['dq_std']).astype(np.float32), a[:, 6].astype(np.float32)

    X, DQ, G = prep(obs, act)
    VX, VDQ, VG = prep(*val) if len(val[0]) else (None, None, None)
    history, best, best_params, step = [], float('inf'), None, 0
    batch = cfg.get('batch_size', 256)
    n_layers = len(params) // 2
    for epoch in range(cfg.get('epochs', 60)):
        t0 = time.time()
        order = rng.permutation(len(X))
        tot = 0.0
        for start in range(0, len(X), batch):
            idx = order[start:start + batch]
            x, dq_t, g_t = X[idx], DQ[idx], G[idx]
            acts = [x]
            h = x
            for i in range(n_layers):
                h = h @ params[2 * i] + params[2 * i + 1]
                if i < n_layers - 1:
                    h = np.maximum(h, 0.0)
                acts.append(h)
            mse, bce, err, p = _loss_parts(h, dq_t, g_t, gw)
            tot += (mse + gw * bce) * len(idx)
            grad = np.zeros_like(h)
            grad[:, :6] = 2 * err / (err.size)
            grad[:, 6] = gw * (p - g_t) / len(idx)
            grads = [None] * len(params)
            for i in reversed(range(n_layers)):
                a_in = acts[i]
                grads[2 * i] = a_in.T @ grad + wd * params[2 * i]
                grads[2 * i + 1] = grad.sum(0)
                if i > 0:
                    grad = (grad @ params[2 * i].T) * (acts[i] > 0)
            step += 1
            for j, g in enumerate(grads):
                m[j] = b1 * m[j] + (1 - b1) * g
                v[j] = b2 * v[j] + (1 - b2) * g * g
                params[j] -= (lr * (m[j] / (1 - b1 ** step)) / (np.sqrt(v[j] / (1 - b2 ** step)) + 1e-8)).astype(np.float32)
        train_loss = tot / len(X)
        if VX is not None:
            mse, bce, _, _ = _loss_parts(forward(params, VX), VDQ, VG, gw)
            val_loss = mse + gw * bce
        else:
            val_loss = train_loss
        row = {'epoch': epoch + 1, 'train_loss': round(train_loss, 6), 'val_loss': round(val_loss, 6),
               'seconds': round(time.time() - t0, 3)}
        history.append(row)
        if log:
            log(row)
        if val_loss < best:
            best, best_params = val_loss, [p.copy() for p in params]
    return Policy(best_params, norm), history


def train_torch(train, val, cfg, device, log=None):
    """Same model and loss in PyTorch (Apple MPS, CUDA on the Orin, or CPU)."""
    import torch
    torch.manual_seed(cfg.get('seed', 0))
    obs, act = train
    norm = normalization(obs, act)
    hidden = list(cfg.get('hidden', [128, 128]))
    layers = []
    sizes = [OBS_DIM] + hidden + [ACT_DIM]
    for i, (a, b) in enumerate(zip(sizes[:-1], sizes[1:])):
        layers.append(torch.nn.Linear(a, b))
        if i < len(sizes) - 2:
            layers.append(torch.nn.ReLU())
    net = torch.nn.Sequential(*layers).to(device)
    opt = torch.optim.Adam(net.parameters(), lr=cfg.get('lr', 1e-3), weight_decay=cfg.get('weight_decay', 1e-5))
    gw = cfg.get('grip_weight', 0.5)

    def prep(o, a):
        x = torch.tensor((o - norm['obs_mean']) / norm['obs_std'], dtype=torch.float32, device=device)
        dq = torch.tensor((a[:, :6] - norm['dq_mean']) / norm['dq_std'], dtype=torch.float32, device=device)
        g = torch.tensor(a[:, 6], dtype=torch.float32, device=device)
        return x, dq, g

    X, DQ, G = prep(obs, act)
    V = prep(*val) if len(val[0]) else None
    bce = torch.nn.BCEWithLogitsLoss()

    def loss_of(x, dq, g):
        y = net(x)
        return torch.mean((y[:, :6] - dq) ** 2) + gw * bce(y[:, 6], g)

    history, best, best_state = [], float('inf'), None
    batch = cfg.get('batch_size', 256)
    gen = torch.Generator().manual_seed(cfg.get('seed', 0))
    for epoch in range(cfg.get('epochs', 60)):
        t0 = time.time()
        net.train()
        order = torch.randperm(len(X), generator=gen).to(device)
        tot = 0.0
        for start in range(0, len(X), batch):
            idx = order[start:start + batch]
            loss = loss_of(X[idx], DQ[idx], G[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += float(loss) * len(idx)
        net.eval()
        with torch.no_grad():
            val_loss = float(loss_of(*V)) if V else tot / len(X)
        row = {'epoch': epoch + 1, 'train_loss': round(tot / len(X), 6), 'val_loss': round(val_loss, 6),
               'seconds': round(time.time() - t0, 3)}
        history.append(row)
        if log:
            log(row)
        if val_loss < best:
            best = val_loss
            best_state = [p.detach().cpu().numpy().copy() for p in net.parameters()]
    params = []
    for i in range(0, len(best_state), 2):
        params += [best_state[i].T.copy(), best_state[i + 1]]  # Linear stores (out, in)
    return Policy(params, norm), history
