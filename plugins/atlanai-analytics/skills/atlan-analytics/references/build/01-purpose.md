# Phase 1: purpose and audience

Goal: one short `brief.md` that every later phase drafts from.

1. Ask what the agent is for in one open question. For example: "Who will ask this agent
   questions, and what do they decide with the answers?" If the user already said it in the
   conversation, restate it and skip the question.
2. **Propose** the brief. Don't make the user write it. Draft:
   - **Audience**: the role(s) and what they decide
   - **Domain**: the business area, in the estate's own words ("Zendesk support tickets,
     CSAT, the Nora AI agent", not "customer service data")
   - **5–10 sample questions** the audience really asks, mixing counts, rates, breakdowns and
     one "why did X move" question
   - **Out of scope**: what the agent should decline
   - **Answer shape**: a number with its definition, the SQL, and the metric or saved query it
     used
3. Ask:
   - `<slug> · purpose · the brief`
   - **Looks right (Recommended)**
   - **Edit the sample questions**
   - **Narrow the audience**
4. Write `.atlan-analytics/<slug>/brief.md`, then record `answers.purpose` (audience, domain) in state.

The sample questions guide scope and context. They are **not** the eval set: phase 4 builds
that separately, and a sample question only reaches it when the user supplies trusted SQL for it.

**STOP gate.** "Brief saved: <audience>, <n> sample questions." Set `phase` to `scope`.
