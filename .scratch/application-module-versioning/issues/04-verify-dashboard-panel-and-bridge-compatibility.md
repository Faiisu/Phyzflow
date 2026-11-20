# 04: Verify Dashboard Panel and Dashboard Bridge compatibility

**What to build:** Let a Dashboard Panel declare which Dashboard Bridge interface versions it supports and show a local diagnostic when the active bridge is incompatible.

**Blocked by:** None (can start immediately).

Status: resolved

- [x] The bridge identifies the interface version it provides. A panel can declare the versions it accepts, and the host checks compatibility before delivering run data or accepting panel actions.
- [x] A compatible panel receives the current run snapshot and live updates through the existing sandbox.
- [x] An incompatible panel shows the observed and supported versions within the panel. The flow and compatible neighboring panels continue to work.
- [x] Existing panels without a declaration retain the current bridge behavior when the host still provides the baseline interface.
- [x] Verify compatible, incompatible, and undeclared panels in the Workspace UI.

## Verification

Browser tests verified live and completed run data for compatible and undeclared panels, local rejection of an incompatible panel, and continued operation of neighboring panels.
