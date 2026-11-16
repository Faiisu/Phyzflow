# Issue tracker: Local Markdown

Store each feature under `.scratch/<feature-slug>/`. Write the spec to `spec.md` and use one file per implementation ticket under `issues/<NN>-<slug>.md`, starting at `01`.

- Publish specs and tickets at those paths. Fetch a ticket by its path, or resolve its number within the current feature.
- Record triage state in a `Status:` line near the top of each ticket. Use the values in [Triage labels](triage-labels.md).
- Append discussion to `## Comments` at the end of the ticket.

## Wayfinder

Use `.scratch/<effort>/map.md` for Notes, Decisions-so-far, and Fog. Store child tickets in the effort's `issues/` directory with a `Type:` of `research`, `prototype`, `grilling`, or `task`, a `Status:` of `claimed` or `resolved` once claimed, and a `Blocked by:` line for prerequisites. Claim the lowest-numbered open, unclaimed ticket whose prerequisites are resolved. On resolution, add an `## Answer` section, set `Status: resolved`, and add a summary and link to Decisions-so-far in the map.
