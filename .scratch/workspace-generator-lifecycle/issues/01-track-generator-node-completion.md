# 01: Track generator node completion

**What to build:** Keep a node that produces a stream of values in Running while its generator is open. Show Succeeded and the completed duration when the generator ends normally.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] Run Live Counter and verify in the browser that Count Up and Multiply by Ten both show Running while Live output advances through successive values.
- [x] Run a finite variant and verify that Count Up becomes Succeeded only after its last value has been consumed and the generator has ended normally. The graph and History show the producer's completed duration and the expected flow output.
- [x] Regular nodes still complete when their functions return, and sheets continue to use one worker process each. The producer and consumer remain separate graph nodes, with source available from their cards.

## Verification

Chrome verified that both Live Counter nodes remain Running while values advance. A finite variant stayed Running after the last value while awaiting generator completion, then reported the expected output and producer duration in the graph and History. A Runner test verified that partially consumed generators close as Stopped.
