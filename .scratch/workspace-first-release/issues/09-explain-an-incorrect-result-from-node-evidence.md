# 09: Explain an incorrect result from node evidence

**What to build:** Let a developer identify the node responsible for a run that finishes successfully but produces an incorrect result by inspecting evidence in local run history.

**Blocked by:** 08 (Recover local run history across browser disconnects)

**Status:** ready-for-agent

- [ ] Node code can record decision reasons, and a developer can opt in to input and output examples for a selected node.
- [ ] The UI shows decision reasons and opted-in examples for the relevant run and node. Nodes without sample capture enabled retain the default metadata-only behavior for inputs and outputs.
- [ ] Using a fixture with a successful but incorrect result, a new user can identify the responsible node and point to the evidence in the UI.
