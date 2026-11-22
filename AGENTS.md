# Agent instructions

## Language

Use Thai when communicating directly with the user, including replies, questions, progress updates, and handoff summaries in chat. Write project artifacts in English, including documentation, plans, code identifiers and comments, UI copy, CLI output, diagnostics, test descriptions, and commit and pull request text. Preserve verbatim user input, quoted material, and test data whose original language matters.

## Project layout

- [`workspace/`](workspace/) contains the Workspace implementation, including the SDK, Runner, CLI, Web UI, and deployment tooling.
- [`workspace-test/`](workspace-test/) contains tests, fixtures, and the sample consumer project. Install the SDK as a dependency in the sample project so end-to-end tests exercise the same entry points as a user installation.

## Planning and acceptance

Before planning, implementing, or reviewing a feature, read the relevant requirements and release acceptance scenarios in the [Application Specification](docs/application-spec.md). Check the matching feature spec and tickets in [`.scratch/`](.scratch/) for scope, dependencies, and acceptance criteria. For work on the initial inference release, refer to the [first-release tickets](.scratch/workspace-first-release/issues/).

## Working rules

- Keep new code comment-free. Use clear names and structure, and record design rationale in project documentation when needed.
- Keep work scoped to the requested outcome and follow the prerequisites recorded for the feature.
- When adding or changing a Workspace feature, add or update its MCP tools in the same change so authorized AI agents can perform the same Workspace operations. Share feature logic across entry points and enforce the applicable Workspace grants and permissions.
- Include MCP coverage in the feature's acceptance criteria. Verify the changed behavior through an MCP client before marking the feature complete.
- Before refactoring or implementing a feature, determine whether the change adds a capability or replaces an existing one.
- For a replacement, inspect the existing implementation and its callers. Adapt it if it can meet the requirements. Create a new implementation when needed.
- When a new implementation replaces existing behavior, move callers to it and remove the superseded code once it has no remaining callers.
- Keep `flow.py` as the source of the graph and nodes directly callable in Python. Pass Python objects between nodes in the same process. Give the CLI and UI the Runner's events and summaries.
- Share project validation across the CLI, UI, and deployment. Run it with the target interpreter and keep flow loading free of model initialization and network calls. Report the failure stage and source location when available.
- Keep raw payloads and request-derived exception text out of stored diagnostics by default. Enable sample capture explicitly per node.
- Verify changed behavior through its normal entry points. Cover the relevant success and failure cases. Mark work complete after verifying its acceptance criteria.
- At handoff, report the files changed, the verification performed and its results, and any remaining blockers or unverified acceptance criteria.

## Agent skills

### Issue tracker

Issues and specs are tracked as Markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the standard triage labels as ticket status values. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. See `docs/agents/domain.md`.
