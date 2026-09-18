# Use a recorded demonstration as evidence

Use this route only when a user-provided demonstration is relevant to the missing
capability. Recording is a separate visible user action; these tools do not start
capture, and reading or drafting never authorizes replay of the demonstrated work.

Call `demonstration_list()` to locate existing recordings, then
`demonstration_read(id, offset, limit)` to page metadata and sampled frame paths.
Inspect selected frames with `see` before describing their contents. Frame paths
are references, not observations by themselves. Sampling does not log keystrokes
or prove which click caused a later screen; do not invent hidden actions.

Use `demonstration_draft(id, goal, steps, uncertainty)` to prepare a SKILL.md draft.
Each step supplies `action`, cited `frames`, `basis`, and a concrete `verify` check.
Use `basis="visible"` for inspected visual evidence, `"inferred"` for a proposed
transition, or `"user-confirmed"` only when the user actually confirmed it. State
missing frames and uncertain actions explicitly. Ask for clarification only when
an unresolved step matters to the requested outcome or safe execution.

The result remains a draft. It does not install, enable, or replay the skill.
Review it, test the proposed procedure against independent cases in the capability
lab, and retain the original evidence and uncertainties. Follow the disabled
candidate and reviewed promotion/rollback workflow in
[experiment.md](experiment.md) when packaging the resulting capability.
