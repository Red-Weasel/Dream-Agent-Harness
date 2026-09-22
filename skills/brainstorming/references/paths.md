# The three paths, in detail

**Spike** — a feasibility question ("can we…", "is it possible…", "quick and dirty is fine"). The output is
an answer, not code you keep. Say the question and what you will try in 2-3 sentences, get a nod, then find
out as cheaply as correctness allows. No design doc. Report a recommendation; anything built stays labeled
throwaway. Keeping spike code is a new request — classify it again.

**Bounded** — a well-scoped change to a flow that already exists in this workspace: a new flag, a small
endpoint, a one-file fix. Understanding the kind of app is not enough; bounded means the flow you change is
here to read. Read it first. Ask the clarifying questions that matter, present a short design in chat
(approach, files touched, how it is tested), and stop. Implementation starts only after an explicit yes.

**Architectural** — a new project, a new subsystem, a change that restructures how components fit or alters
interfaces others depend on. Ask questions one per message (purpose, constraints, success criteria). Propose
2-3 approaches with trade-offs and a recommendation. Present the design in sections scaled to their
complexity (architecture, components, data flow, error handling, testing) and confirm each. Write the spec
to `docs/plans/<YYYY-MM-DD>-<topic>-design.md`, self-review it (no placeholders, no contradictions, one
interpretation per requirement, scoped to one implementation plan), and ask the user to review the file
before any implementation plan.

If a request describes several independent subsystems, say so first and help decompose it; brainstorm the
first sub-project through the normal flow. Each sub-project gets its own spec, plan and build.

## Red flags
"This is too simple to need a design." — Simple means a short design, not no design.
"It's bounded and the design is obvious, I'll start while they read it." — The gate is the approval.
"I understand this kind of app, so it's bounded." — Bounded measures the repo, not your familiarity.
"The spike works, so I'll keep the code." — A spike's output is an answer.
"It grew, but I'm almost done." — Hidden complexity upgrades the path. Stop and say so.
