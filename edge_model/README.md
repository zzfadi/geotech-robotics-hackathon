# edge_model (scaffold, handed off)

Starter code for the [edge model handoff](../docs/handoffs/edge-model.md). It is a scaffold, not a finished pipeline: there is no collection runner, dataset writer, eval harness or CLI yet. Nothing here moves a physical arm or calls the Dream API.

```sh
python3 -m pip install numpy   # the only dependency; torch is optional for training
```

Quick check that the bench and expert work (about 15 s on a laptop):

```python
from edge_model.bench import BenchTable, DT
from edge_model.expert import Expert
from edge_model import gate

table = BenchTable(table_index=0, seed=1000)
expert = Expert(table.cup(table.target).center)
times, evidence = [], []
for step in range(110):
    q, grip, phase = expert.act(table)
    table.step(q, grip)
    times.append((step + 1) * DT)
    evidence.append(table.evidence())
print(gate.evaluate(times, evidence))
```

Run it from the repo root. `kinematics.py` adds `DreamSim/` and `Unreal/TheHumanTouch/Tools/` to the import path for the pinned FK.

The bench is kinematic, not physics: its evidence says `physics_simulated: false`, so nothing from it can pass the Dream exporter's positive gate. Use it to build and test the pipeline; use the Unreal fleet for physics.
