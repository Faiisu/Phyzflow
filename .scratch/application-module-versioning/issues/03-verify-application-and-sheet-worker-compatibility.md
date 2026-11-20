# 03: Verify Application and Sheet Worker compatibility

**What to build:** Let the Application verify the Sheet Worker's startup interface and report the SDK that actually executes a sheet.

**Blocked by:** None (can start immediately).

Status: ready-for-agent

- [ ] The Application declares the worker protocol versions it accepts. The worker reports its protocol and loaded SDK identity through the startup exchange before project code executes.
- [ ] An accepted protocol allows the sheet to open and run. An unsupported or missing protocol produces a startup diagnostic with the observed value and accepted versions before project code loads.
- [ ] Sheet startup diagnostics report the loaded SDK version and the SDK version installed in the Workspace interpreter separately, with an explicit indication when a distribution is absent.
- [ ] Verify an accepted pair, a rejected pair, and a Workspace interpreter with a different installed SDK version using the normal sheet startup path. The rejected pair must leave project code unexecuted.
