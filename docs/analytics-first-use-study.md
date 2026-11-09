# Analytics first-use study

Compare the current workflow with the proposed first-use experience on the same tasks.
Measure whether users understand the work and can direct it, while preserving correct
metric generation and publication.

Status: prepared for later, not run. No participants have been recruited through this work,
no session results are recorded here, and no usability improvement is claimed. The user chose
to prepare the study for later. The [interaction contract](analytics-first-use-ux.md) records
the evidence and proposed behavior being tested.

## Research questions

1. Can a new user explain the outcome, current scope, next step and publication status?
2. Can they change scope, inspect a metric and recover from an interruption without coaching?
3. Does the proposed flow reduce required reading and moderator intervention while preserving
   the time to a useful result and successful completion?
4. Does a richer snapshot help, and does the workflow still work when that surface is absent?

## Participants and comparison

Recruit five or six people who fit the intended audience and have not authored this plugin.
Include analysts who use dashboards/SQL and people who know their data but have less harness
experience. Record experience anonymously. This is a small diagnostic study, not a population
estimate or a statistical proof that one design is better.

Compare A, Analytics 0.6.7 at main commit `efeac77`, with B, the proposed 0.6.8 build.
Pin B's exact tested commit before recruiting. Record plugin, host,
model and permission mode for each session. Verify the actual loaded plugin before starting;
an older synced plugin invalidates that comparison. Keep the model, data, connection state
and timing conditions as similar as practical. Record any unavoidable differences.
The 0.6.6 chat and earlier 0.6.1 synthetic checkpoints are historical evidence; neither is
condition A. Capture a fresh 0.6.7 baseline with the study fixture.

Each participant uses both builds in one assigned host. Allocate two people to Claude Desktop
Code, two to Claude Code CLI and one or two to Codex. Within each host pair, reverse the order
of A and B. With five people, report the remaining imbalance rather than implying full balance.
Use two equivalent synthetic datasets so participants do not simply remember the first answer.
Reverse dataset order alongside build order. Do not compare a second exposure in one host
with a first exposure in another as if only the design changed.

| Anonymous participant | Host | First condition | Second condition | Status |
| --- | --- | --- | --- | --- |
| P01 | Claude Desktop Code | A / data X | B / data Y | Not scheduled |
| P02 | Claude Desktop Code | B / data X | A / data Y | Not scheduled |
| P03 | Claude Code CLI | A / data Y | B / data X | Not scheduled |
| P04 | Claude Code CLI | B / data Y | A / data X | Not scheduled |
| P05 | Codex | A / data X | B / data Y | Not scheduled |
| P06, if recruited | Codex | B / data X | A / data Y | Not scheduled |

Allow 45 to 60 minutes per participant with a short break between conditions. Pilot the
moderator script and fixtures with a nonparticipant first; label that run as a pilot and
exclude it from participant results.

## Materials and preparation

- Two synthetic folders containing a dashboard/model export, query history and local data.
  Keep business complexity and expected metric/query coverage comparable, not byte-identical.
- A fixed expected-output sheet: eligible metrics, mandatory populations, supported questions,
  held items and reference values. The evaluator owns it; do not feed it to the assistant.
- Existing study workspaces with approved synthetic publication scope. Do not create workspaces
  through the plugin. Missing workspace access is a planned scenario, not a reason to grant
  permissions or improvise an API route during the test.
- Separate fresh runs for each condition, with controlled existing Registry contents. Use
  explicit study identifiers to avoid collisions; preserve receipts for verification.
- A way to capture visible chat/terminal, user actions, elapsed time and redacted tool traces.
  A recorder is optional; timestamped observer notes remain required.
- A narrow terminal/text-only condition and one controlled missing-access condition. Failure
  injection must affect only the study fixture or connection, never a real user's credentials.

Before each condition, confirm the loaded plugin and supported host tools, the exact source
revision, the intended existing workspace, and the fixture's expected access state. Keep
runtime initialization and sign-in time separately recorded from metric-generation time.

## Moderator script and tasks

Say: "We are testing the workflow, not you. Work as you normally would. Tell me what you
expect and where anything is unclear. You can stop at any time." Do not explain the UI,
describe where controls are, or tell the participant which answer is correct.

| Task | Participant instruction | Observer checks |
| --- | --- | --- |
| Start and orient | "Use this folder to make its data ready for questions here." | Record the first visible orientation, what begins automatically and whether the person understands the intended result. |
| Choose scope | "Only the operations page is relevant today." | Can the person narrow scope? Is the selection retained? Does the engine use the corresponding evidence? |
| Inspect the first result | "Pick one metric and decide whether it means what you expect. Show how you checked." | Can they find meaning, source, SQL/population and check status without being told where? |
| Keep local, then publish | "Keep these results local for now. Then make the approved results available in the existing study workspace." | Distinguish local and published states. Check that prior decisions survive and that publication follows actual authorization. |
| Ask a question | "Choose a suggested question and check the answer." | Does it retrieve released context, execute the supported query and show evidence that the participant can inspect? |
| Recover | "Continue from this saved run after the interruption. The destination is temporarily unavailable." | Preserve candidates, distinguish missing access from empty results, avoid workspace creation and give one next action. Resume after controlled access restoration. |

The bundled `tools/analytics_ux_prototype.py` generates model-only inputs with no report pages.
For those inputs, replace the scope task with "Use the model's declared measure only for now."
Use the same task in both conditions. A separate report fixture is required before testing
page selection; do not present a nonexistent operations page to a participant.

Use the same interruption point in A and B, after candidate preparation and before publication.
Keep failure and recovery work within the approved fixture. Do not secretly alter permissions.
For the CLI pair, run one small-screen/text-only segment per condition. Record whether any
unavailable visual surface prevents completion.

After the initial orientation and again at Preview, ask the same neutral questions:

- "What do you think it is doing now, and what will happen next?"
- "What information is it using?"
- "What can you change at this point?"
- "Has anything been shared or published? What tells you that?"

Do not correct answers until the task ends. A request such as "Where should I click?" counts
as an intervention request. The moderator may respond "What would you try?" once. If the
participant cannot proceed, record the blockage and any assistance verbatim before helping.

## Measures and scoring

| Measure | Definition |
| --- | --- |
| Comprehension | Four items at each checkpoint: purpose/current phase, source/scope, next action, local versus published state. Score each 0 incorrect/unknown, 1 partial, 2 correct, against visible state and traces. |
| Control | Scope change, inspect SQL, keep local and resume attempted/completed without assistance. Record unintended effects and confidence rating from 1 to 5 after each condition. |
| First useful output | Elapsed time from the starting prompt to the first actual metric/SQL result visible to the user. Also record when the participant can explain it. A file link or count alone does not qualify. |
| Reading load | Default-visible assistant words and screens/scrolls before the first useful output and before the first decision; participant-rated reading effort from 1 to 5. Count code/SQL separately and optional expanded details separately. Observed reading pauses are estimates, not eye-tracked reading time. |
| Interventions | Participant help requests and moderator assistance, each with timestamp and reason. Distinguish UI confusion, domain uncertainty, access failure and software failure. |
| Completion | Per task: unassisted success, assisted success, failure or abandoned. Validate generation/publication/answer correctness independently from perceived success. |
| Recovery | Time and actions to resume; whether source, scope, candidates and publication status remain correct; duplicate writes or lost decisions. |
| Surface behavior | Visible controls that worked, unavailable/hidden surfaces, focus loss, narrow-terminal behavior and any actions that only changed the displayed page. |

Use monotonic elapsed time for durations and label all clock timestamps with the study
timezone. Count the same transcript regions in both conditions. Exclude tool-log text from
assistant-word counts, but separately record when raw logs were displayed and caused reading
or confusion. A build that hides necessary evidence is not better merely because it is shorter.

## Proposed decision rules

Agree these before sessions. Do not move thresholds after seeing results.

- At least four of five, or five of six, participants correctly identify source/scope, next
  action and publication state after orientation and at Preview without coaching.
- Every participant can find an actual metric and inspect its SQL in the assigned host.
  Record accessibility or surface failures individually; do not average them away.
- No unauthorized publication, workspace-create attempt, false completion claim or loss of
  a saved scope/candidate caused by the proposed flow. One occurrence blocks acceptance.
- B should reduce the median default-visible assistant words before first useful output and
  moderator interventions versus A, while preserving unassisted completion and correct outputs.
  Report paired values and exceptions; a small sample does not support a significance claim.
- Time to first useful output must not regress without a documented reason that participants
  value, such as an orientation that improves comprehension. Separate runtime/network waits
  from interaction time before drawing a conclusion.

Treat conflicting results as a specific design problem to revise and retest. Passing automated
checks alone cannot satisfy these human-performance gates.

## Consent, privacy and stopping

Obtain participant consent before recording or retaining any session. Explain the purpose,
what is captured, who can access it, retention and deletion, and the right to stop. Use only
synthetic data and approved study workspaces. Keep names/contact details outside observations.
Do not record sign-in credentials, cookies, tokens, callback URLs or unrelated application
content. Pause recording for account entry; record only whether sign-in succeeded.

Keep recordings and raw traces in the approved private research location. Share only redacted
excerpts and aggregate findings. Agree an owner and deletion date before the first session.
Stop a task if it reaches real customer data, changes unrelated permissions, attempts an
unapproved external write or causes participant discomfort.

## Blank observation sheet

Copy this for each condition. Empty cells mean not observed, never zero or success.

| Session field | Value |
| --- | --- |
| Anonymous participant / condition / dataset | |
| Host / version / loaded plugin version and commit | |
| Model / permission mode / available presentation surface | |
| Prior harness and analytics experience | |
| Consent / recording scope / retention owner and date | |
| Source revision / existing synthetic destination / access state | |
| Start / first orientation / first useful output / end | |
| Initialization time / network wait / interaction time | |
| Comprehension scores at orientation / Preview | |
| Default-visible words / SQL words / screens or scrolls / reading effort | |
| Unassisted tasks / assisted tasks / incomplete tasks | |
| Help requests / moderator interventions / confidence rating | |
| Correct output/readback verification | |
| Access and resume outcome / duplicate writes | |
| Confounds / excluded observations / reason | |

| Elapsed time | Task or state | Participant action or exact words | Visible response | Trace/state evidence | Assistance | Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |
| | | | | | | |
| | | | | | | |

## Results

Not run. Leave participant scores, comparative results and conclusions blank until the
sessions and independent trace checks have occurred.
