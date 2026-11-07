# Recovering Dream inference-session expiry

The pinned `dreamlabs-client==1.1.3` / `dreamlabs==1.0.37` client documents `DreamSessionExpired` as a recoverable session boundary: the client has already adopted the replacement server session, but the action from that response is not returned. Calling `get_action()` again continues the replacement session. This is explicit in `dream/facade.py::DreamClient.get_action` and the implementation in `dream/client.py::_request` (session ID adoption precedes raising the typed exception).

The previous worker treated this typed exception as terminal. Live runs repeatedly completed one retimed Unreal trajectory, then stopped at the next inference because the server expired the episode during the gap. The expiry duration is a server behavior observed in those runs, not a measured or promised TTL.

`DreamSim/session_recovery.py` now permits exactly one replacement-session retry per control cycle. It:

- retries only `DreamSessionExpired`, not malformed actions, authentication failures or unrelated errors;
- waits for fresh verified Unreal camera/joint feedback before each request;
- ensures that feedback has been installed in Dream's observation window before publishing it to the inference thread;
- checks cancellation before requesting, after expiry and after a successful reply;
- reports `session.recovering` in the debugger;
- never applies the discarded expiry response, resets the physical/simulated pose, or silently starts the whole task over.

The typed error documentation also requires a safe pose before continuing. This implementation is currently limited to editor kinematic playback, which holds all link transforms during inference; the refresh verifies that held pose against every live link. A future dynamic controller must establish a safe hold/contact condition before using this recovery path.

A second expiry in the same control cycle still fails the command. Successful recovery establishes continuity of inference, not successful object grasping or calibrated dynamics.

Offline tests cover refresh/retry ordering, discarded expiry responses, a two-attempt bound, non-expiry propagation, cancellation during expiry, and cancellation after a reply. Live validation remains separate.

## Live verification

Request `5773d12046c742619cfd7cc3a19c6502` submitted the exact prompt **pick up red cube**. Its first 49-sample trajectory completed in 9.170 seconds. Cycle 2 received the documented expiry, emitted `session.recovering`, obtained a fresh action on the replacement session, and completed its 49 samples in 8.211 seconds. The command reached `done`; all three camera recordings contain 139 frames. Neither action required aperture endpoint correction. The event and pose evidence is in `docs/validation/dream-session-recovery-live.json`.

This proves the session recovery and multi-cycle kinematic control path. The virtual arms moved but did not pick up the cube; object contacts and task-space behavior still need work. A command status of done means its requested bounded control cycles completed, not semantic task success.
