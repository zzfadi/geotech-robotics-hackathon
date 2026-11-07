# The Human Touch — Unreal

Requires Unreal Engine **5.8.3**, changelist **58210709** on every participant. Launcher association `5.8` selects the installation; verify Engine/Build/Build.version for the patch.

Open `TheHumanTouch.uproject`. Both startup maps point to `/Game/Maps/Empty`, the only project level. It contains no actors. The map was created, saved and reopened by UE 5.8.3 on Mac.

See [migration research](../../docs/agentcopresence-migration.md) for agents, voice, Multi-User and Quest 3S requirements. Engine plugins are enabled; CoPresence custom plugins and live voice/VR integration are not yet installed.

`Tools/create_empty_map.py` is the one-time creation script and refuses to overwrite an existing map. Generated caches, logs and user state stay outside git.
