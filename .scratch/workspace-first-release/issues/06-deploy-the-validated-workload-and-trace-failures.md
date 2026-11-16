# 06: Deploy the validated workload and trace failures

**What to build:** Let a developer deploy the validated first workload to one target and trace a failed served request back to the implementation.

**Blocked by:** 02 (Reject invalid flows before execution), 05 (Prove the first workload on one deployment target)

**Status:** ready-for-agent

- [ ] `workspace deploy` validates the exact build it will promote in the target Python environment. The first workload has a smoke test that uses the same request handling and response serialization as live traffic.
- [ ] A verified build returns the expected response to a real request. Broken syntax, a broken import, and a failing smoke test each prevent opening traffic and produce an actionable diagnostic.
- [ ] A request that deliberately fails in a node can be traced to the deployed build, run, node, and source file.
- [ ] Stored diagnostics exclude raw request payloads and request-derived exception text by default.
