# The Human Touch — Unreal

Requires **Unreal Engine 5.8.3**, changelist **58210709**. The baseline map is `/Game/Maps/Empty`; agent presence is editor/session visualization.

## Shared human and agent editor

Run **one Unreal Editor per computer**. The person and agent use that editor together. Agent runners, voice helpers and the Multi-User server are separate processes. Do not use the original AgentCoPresence two-editor launch workflow here.

After building the native CoPresenceFab plugin for your platform, run:

```sh
uv run --script Tools/join_human_touch.py --local-address YOUR_VPN_IP --name "Your Agent Name"
```

The launcher refuses if any Unreal Editor is running, including another project. Save work and close that editor gracefully before restarting. It joins Luna2's `TheHumanTouch` server/session on UDP 9040, connects chat to `http://100.84.28.59:8791`, and serves scoped agent tools on localhost port 8041. The Agents and Chat tabs open automatically. The agent uses your shared viewport; automatic voice-companion camera following is disabled.

A runner must also be started. On Mac:

```sh
COPRESENCE_HUB_URL=http://100.84.28.59:8791 python3 Tools/copresence_runner_service.py --project "$PWD" --brain codex --port 8041
```

On Windows, supervise `uv run --script Tools/copresence_mcp.py run-agent claude --port 8041` with `COPRESENCE_HUB_URL=http://100.84.28.59:8791` and `HUMANTOUCH_SHARED_EDITOR=1`. The relevant CLI must already be signed in. A runner has no reason to open another editor.

The `+ Agent` station workflow cannot launch extra editors in this project. Additional agents belong on other computers unless the user explicitly changes the limit. Use the existing agent's **Call** button for voice; **Hang up** ends the call.

## Private configuration

Provision the session code in `Saved/CoPresenceLogs/hub_code`, host address in `Saved/CoPresenceLogs/host_address`, and voice credential reference in `Saved/CoPresence/voice.json`. Never commit them. On the managed Mac use the approved `op-agents` helper. Voice needs audio devices and OS microphone permission; a connected helper alone does not prove an audible conversation.

## Native build

Build `Plugins/CoPresenceFab/CoPresenceFab.uplugin` using UE's `RunUAT BuildPlugin`, targeting Mac or Win64. Copy the resulting platform `Binaries` into this project's plugin; generated binaries stay out of git. Optional CoPresenceAnnotate source is included but is not enabled until built. CoPresenceCall needs the engine's NDIMedia plugin. Both participants must have matching enabled plugins and project content.

## Validation

`Tools/validate_empty_map.py` loads the baseline and checks engine version and zero authored actors. The initial map was verified on Mac and Luna2. Scoped `Tools/copresence_mcp.py call session '{}'` uses `COPRESENCE_UNREAL_MCP_URL=http://127.0.0.1:8041/mcp`; verify the returned session and peer identities. Test a read-only chat request and voice independently.

Mac Codex and Luna2 Claude have both returned verified Agent Chat replies in TheHumanTouch; both avatars are visible and each machine runs one editor. Voice helpers are connected; end-to-end audible voice verification remains pending.

## Checkpoints

Luna2 checkpoints and pushes ready Unreal content to GitHub every **15 minutes**, plus completed milestones. The job defers active/unsaved work instead of committing an incomplete checkpoint. See [checkpoint operations](../../docs/checkpoints.md) and `Config/CheckpointPolicy.json`. Git commits do not clear Multi-User history: use the documented coordinated archive/fresh-session boundary to keep transaction history short.

See [migration research](../../docs/agentcopresence-migration.md) for VR and later robot work.
