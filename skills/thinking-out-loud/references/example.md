# Worked example: context management in the Dream harness

This is a real conversation, lightly condensed, that shows every stage of the skill. Match the shape, the plain language, and the rhythm of the turns, not the topic.

## The ramble

> well we need to figure out the best workflow and ctx aligned to quality and speed. for example, would it best for the main agent to always act as an orchestrator and be given max ctx for the model then be designed to always send agents to do small tasks. My concern is an agent with 32k ctx doesnt have much head to get anything done... Is it even possible to run all agents with max model ctx... can we have newly launched subagents get different sized windows on-the-fly, like... one gets a 100k window and the other gets a 32k window... or at least change it on the fly for fresh subagents...

## Turn 1: break it down, ask the two fixed questions

> You raised several questions and topics:
>
> Regarding context and capability:
> - Generally, yes: agents can be launched with different context windows in the same session, and the window can change between launches.
> - The model in use and the hardware set the limits and the best setup, and we can test and measure both.
>
> Question 1: In one sentence, what is your idea about?
> Question 2: What was your first thought, or what made you start thinking about this?

User:
> - Context management in the harness, and best practices for it while running subagents.
> - I bet context management is important. I wondered what's possible and how engineering it could improve the harness.

## Turn 2: frame the idea, split it into pieces, checked facts, two questions

> Got it. The idea is to design how the harness manages context for the main agent and its subagents, and turn that into best practices.
>
> It breaks into four pieces:
> - Budget: how much context each agent gets, and who decides it.
> - Handoff: what the main agent gives a subagent, and what comes back.
> - Lifecycle: when a subagent starts fresh, when its context is discarded, and when the main agent's context gets trimmed.
> - Limits: what the model and the hardware actually allow. We can measure that instead of guessing.
>
> What exists today:
> - (checked facts only)
>
> Question 1: Of those four pieces, which one feels most important to get right first?
> Question 2: How would you know it's working?

## Turn 3: organise their answer, answer with facts, decision questions

The user said all four pieces matter and asked a few questions of their own: how compaction works, whether a subagent's context disappears once it returns, and whether the agent could write itself a handoff automatically.

> Here's what you said, organized:
> - The goal: runs never fail, and none hits the limit with nothing to show for it.
> - Subagents running out of context matters most to you.
> - …
>
> What exists today (checked in the code):
> - Context sizes are fixed when the server starts. Lane 0 gets `--ctx`, and extra lanes get `--slot-ctx`, 32k by default.
> - Compaction isn't the agent taking notes. The code stubs out old tool results first, then old messages, and saves what it cut to notes the agent can read back.
> - Your handoff idea already half-exists as Fresh start, but only as a manual step between turns.
>
> Question 1: When a subagent is about to run out, do you want it to stop and report what it has, or write a handoff so a fresh subagent continues?
> Question 2: Should the main agent do the handoff automatically instead of compacting, or only when you're away?

## Turn 4: answer them honestly, recommend, organise their decisions

The user asked two things: "does compaction actually hurt quality, or were you just repeating what I said?" and "what would you recommend for subagents?"

> - Does compaction hurt quality? I was repeating what you said. Nobody has measured it. One logged case lost a learned fact to it, which is one data point, not proof.
> - My recommendation for subagents is to default to `return`. Compaction cuts old tool results first, and for a subagent that just read ten files, those results are the work.
> - …
>
> Question 1: When should the automatic handoff fire: at a set fill level, or at a natural stopping point after a warning?
> Question 2: …

## Turns 5 and 6: adopt their renames, and ask when their wording has two readings

The user renamed the feature twice and settled on "Handoff". From then on the assistant used "Handoff" everywhere. The user asked for a warning at "5% of the auto-trigger", and the assistant asked which of two readings they meant:

> Question 1: Does the warning at "5% of the trigger" mean 5 points before it (warn at 75%), or 5% of 80 (warn at 76%)?

## Closing: the recap

> Here's where we landed.
>
> The idea: Dream gets configurable context management, so no run, main agent or subagent, dies on a full context with nothing to show for it.
>
> Decided:
> - Main agent on a full context: compact, handoff, or stop.
> - Subagent on a full context: compact, handoff, or return (return is the recommended default).
> - One trigger for every option: 80% by default, changeable. Warning 5 points earlier.
> - Every option is a setting, both in config and as a / command.
>
> Still open:
> - Whether `stop` replaces today's `error`. (The user said to replace it.)
> - Honest flag: moving compaction from 85% to 80% on big windows brings back some wasted window space. It's still worth it for one simple knob.
>
> Next step: write it up as a fix-list item and build it test-first. The first measurement is Handoff vs compaction on one long task.
