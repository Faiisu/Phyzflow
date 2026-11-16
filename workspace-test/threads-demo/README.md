# Three-thread flow demo

From the repository root, use the SDK environment from the [local demo](../README.md#local-demo):

```sh
cd workspace-test/threads-demo
../../.venv/bin/workspace check
../../.venv/bin/workspace run '{"value":4}'
../../.venv/bin/workspace ui --port 0
```

Open the URL printed by the last command and select **Run flow**. Select **Collect** in Flow outline and open **Inspector** from **Panels** to see the twelve inputs. With the prefilled request, the final values for A, B, and C are 81, 841, and 2401. Run output lists the value and actual worker thread for every stage.

Each worker executes Prepare, Double, Add One, and Square in its own Python thread. A barrier starts the three branches together. Every stage hands a future to the next stage and to Collect, which waits for all twelve results and shuts down the workers. The stage badges show when the Runner has scheduled work. Collect succeeds after the worker results are ready.
