# geotech-robotics-hackathon

GeoTech robotics hackathon workspace.

## Dashboard

Track the team's progress at **https://zzfadi.github.io/geotech-robotics-hackathon/**. To post an update, see [dashboard/README.md](dashboard/README.md).

## Contributing

All development takes place directly on `main`. Read [AGENTS.md](AGENTS.md) for the branch workflow, conflict-resolution guidance, and agent working rules.

## Turn-taking prototype

The [local turn gate](turn_gate/README.md) tests hand occupancy, observation freshness and explicit robot turns with a deterministic replay. It runs without hardware or API credentials. See the [robot stack integration boundary](docs/robot-stack-contract.md) before connecting the detector, Dream-1 or a physical arm.

## Unreal project

Open [TheHumanTouch.uproject](Unreal/TheHumanTouch/TheHumanTouch.uproject) with **Unreal Engine 5.8.3** (CL 58210709). The baseline has one empty map. See the [project instructions](Unreal/TheHumanTouch/README.md) and [AgentCoPresence migration research](docs/agentcopresence-migration.md).

For the two-person capture demo, see the [Mac Live Link, simulated contact and Quest overlay plan](docs/performance-capture.md), including a printable table registration target.
