# Agent instructions

## Language

Use Thai when communicating directly with the user, including replies, questions, progress updates, and handoff summaries in chat. Write project artifacts in English, including documentation, plans, code identifiers and comments, UI copy, CLI output, diagnostics, test descriptions, and commit and pull request text. Preserve verbatim user input, quoted material, and test data whose original language matters.

## Project layout

- [`workspace/`](workspace/) contains the Workspace implementation, including the SDK, Runner, CLI, Web UI, and deployment tooling.
- [`workspace-test/`](workspace-test/) contains tests, fixtures, and the sample consumer project. Install the SDK as a dependency in the sample project so end-to-end tests exercise the same entry points as a user installation.

## Development order

Read [the development plan](docs/development-plan.md) before planning, implementing, or reviewing a milestone. It defines the detailed deliverables and acceptance criteria.

1. **Build the local demo.** Identify the first workload, candidate deployment target, and Python version while starting the SDK. Make the sample flow run through the Python API and CLI, then connect the localhost UI. Verify that the same sample returns the expected output and that a failure in a nested source file can be diagnosed from the UI. Verify CPU tensor identity and device preservation and record the initial overhead baseline alongside the UI work.
2. **Validate the deployment target.** Start as soon as the CLI runs the sample. In a clean target environment, validate a build and send a representative request through the serving path using the first workload's real model and dependencies. Record reproducible build and test commands. Resolve failures before implementing deployment.
3. **Ship deployment to one target.** Reuse the serving path proven in step 2. Validate and smoke-test the build that will serve traffic. Verify a successful request and a forced failure traceable to its build, run, node, and source file.
4. **Add local run history.** Begin once the first UI and the target workload's performance baseline are ready and the overhead limits are set. Verify that a run survives browser disconnect and that history distinguishes the source versions actually used, including uncommitted edits.
5. **Verify release readiness.** Have a new user install, run, debug, and deploy from the documented steps. Repeat the benchmark for direct execution, the Runner alone, and the Runner with the live UI. Verify the agreed limits for added p95 latency and peak memory.

## Working rules

- Identify the current milestone, its dependencies, and its acceptance criteria before editing. Keep work scoped to the requested outcome. Use the documented parallel work where its prerequisites are met.
- Preserve the first-release scope of one project, one Python environment per installation, one request/response flow, and one deployment target. Schedule separate model deployment and GPU worker management after the first target is delivered.
- Keep `flow.py` as the source of the graph and nodes directly callable in Python. Pass Python objects between nodes in the same process. Give the CLI and UI the Runner's events and summaries.
- Share project validation across the CLI, UI, and deployment. Run it with the target interpreter and keep flow loading free of model initialization and network calls. Report the failure stage and source location when available.
- Keep raw payloads and request-derived exception text out of stored diagnostics by default. Enable sample capture explicitly per node.
- Verify changed behavior through its normal entry points. Cover the relevant success and failure cases. Mark a milestone complete only after its acceptance criteria are verified.
- At handoff, report the files changed, the verification performed and its results, and any remaining blockers or unverified acceptance criteria.

## Agent skills

### Issue tracker

Issues and specs are tracked as Markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the standard triage labels as ticket status values. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. See `docs/agents/domain.md`.
