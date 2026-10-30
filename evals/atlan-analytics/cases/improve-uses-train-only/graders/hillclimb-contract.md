---
type: llm
weight: 1
---

The explanation, grounded in the skill files, covers each of these (wording may differ):
1. a train/test split of the eval set that is fixed before the loop (not redrawn to chase a score);
2. a failure-analyst subagent that reads only TRAIN transcripts, never test ones;
3. one change per round, recorded so it can be reverted (for example a new registry artifact version);
4. re-running the eval and reporting the held-out TEST score, with its noise band or confidence
   interval, as the headline;
5. each round published to the registry as an experiment linked to the baseline.
Pass when all five are present. Fail if the train-only rule or the test-as-headline rule is missing.
