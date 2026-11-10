# Baseline check: can this eval show a win?

Do these before round 1. Each has quietly wrecked a hill-climb.

1. **Noise floor vs headroom.** Put three numbers in front of the user:
   - the noise floor: the validation `ci95` that `EVAL summary` reports for the baseline (half
     the width of its Wilson interval, over validation cases). Quote that number, not a formula
     of your own: every later step, the δ in the plan included, uses it;
   - the headroom: 100% minus the validation baseline;
   - the smallest gain they would act on.

   If the floor is bigger than either of the other two, no change can show above noise. Offer
   more reps or more cases before starting.
2. **Recompute the headline by hand.** Take `H/baseline/results.jsonl` and count the passes per
   split yourself. The count must match `EVAL summary`. A number that looks too good is a
   measurement bug until checked.
3. **Spot-check the grading.** For 3–5 failing train cases, read the agent's answer, its result
   file and the gold result.
   - Is the gold right? Is the fail real, or a formatting or grain mismatch the comparator
     should forgive?
   - Classify every zero as agent failure or harness error, and take harness errors out of the
     denominator: re-record that case into the fresh variant below with
     `EVAL record --class harness_error --reason "<what failed>"` and no answer file.
   - Fix a bad gold in `answers.jsonl`, re-run its gold SQL into `eval/gold/<case_id>.json`,
     run `EVAL refreeze --reason "<the case and why>"`, then re-`record` the stored answer and
     result files into a fresh variant (for example `baseline2`). `record` never overwrites a
     pair, and nothing re-runs the model. Use that variant as the baseline from then on:
     `compare` refuses rows from before the refreeze.
4. **Prove the mechanism is wired.** In the train transcripts, the agent should call the
   registry search tool before writing SQL in most cases. If it almost never does, fix the
   prompt first. That becomes round 1, because context changes can't help an agent that never
   looks.
5. **Train and validation agree.** The two pass rates should be within noise of each other:
   their `ci95` intervals overlap.
   If they aren't, tell the user: the sets are small or unevenly hard. Add cases or reps, which
   needs a refreeze and a new baseline. Never redraw the split: a draw chosen after seeing
   scores is no longer held out, and `split` refuses it.
