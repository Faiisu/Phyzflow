# 01: Trace node dependencies on the graph

**What to build:** Select a node to follow its immediate inputs and outputs on the execution graph. Use the Inspector to see which arguments each connection supplies and jump to the connected node.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] Selecting a node emphasizes its directly connected edges and visually de-emphasizes unrelated edges. Long highlighted connections stay traceable where several edges cross columns.
- [x] Selected connections show their target argument names. If a source supplies several arguments to one destination, all bindings remain discoverable. Run inputs are distinguishable from upstream nodes.
- [x] Inputs and Feeds in the Inspector can focus and select the associated graph node using a pointer or keyboard.
- [x] In the Telemetry Control Room sheet, select Persist Batch, identify Reconcile Events as the source of `audit`, and navigate to it without reading the source or run output. Verify in a browser test.

## Verification

Chrome tests followed Persist Batch's audit input with the keyboard and a downstream connection with the pointer, checked labels at 390 px, and verified multiple bindings and run inputs. The tests passed with the installed SDK wheel.
