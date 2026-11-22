# 04. Open only the linked sheet

Status: resolved

Opening a Workspace URL with `#flow-tab-1` sends open requests for both Main and the target sheet and mounts both iframes.

Initial synchronization selects the first tab and starts its worker before bootstrap checks the URL fragment. The target then starts a second worker and renders another graph.

- [x] A direct link opens only the target sheet and displays its graph.
- [x] Missing or unavailable sheet links still open the first available sheet.
- [x] Normal tab selection still opens sheets on demand.

## Verification

The Chrome regression observed only the linked sheet’s open request and iframe. Selecting Main then opened it on demand. Deleted and invalid URL fragments selected the first available sheet.
