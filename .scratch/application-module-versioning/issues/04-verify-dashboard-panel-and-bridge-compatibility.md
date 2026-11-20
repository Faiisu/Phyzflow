# 04: Verify Dashboard Panel and Dashboard Bridge compatibility

**What to build:** Let a Dashboard Panel declare which Dashboard Bridge interface versions it supports and show a local diagnostic when the active bridge is incompatible.

**Blocked by:** None (can start immediately).

Status: ready-for-agent

- [ ] The bridge identifies the interface version it provides. A panel can declare the versions it accepts, and the host checks compatibility before delivering run data or accepting panel actions.
- [ ] A compatible panel receives the current run snapshot and live updates through the existing sandbox.
- [ ] An incompatible panel shows the observed and supported versions within the panel. The flow and compatible neighboring panels continue to work.
- [ ] Existing panels without a declaration retain the current bridge behavior when the host still provides the baseline interface.
- [ ] Verify compatible, incompatible, and undeclared panels in the Workspace UI.
