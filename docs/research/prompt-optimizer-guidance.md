# Prompt Optimizer: model guidance

Researched 2026-09-13 for DREAM-075. This is guidance for drafting user requests,
not measured evidence that optimization improves a model's task performance.

The common format comes from the owner's requested goal, evidence, constraints,
autonomy, output and verification sections. Clear headings are sufficient; no
special markup or elaborate persona is required. Empty optional sections can be
omitted. Original requirements take precedence over a suggested writing style.

## Astra

The official Astra guide emphasizes clear autonomy and follow-through, especially
avoiding unnecessary pauses for routine decisions. It also advises making output
expectations and instruction priority explicit. Dream's Astra style therefore
keeps the goal, deliverable and boundaries concise and permits reversible choices
within the user's scope. It does not increase permissions or override approval
requirements. [OpenAI model guide](https://developers.openai.com/api/docs/guides/latest-model)

OpenAI's reasoning guidance favors direct instructions, clear delimiters and
specific success criteria. Asking for private step-by-step reasoning is unnecessary.
Dream's reasoning choices request checks, assumptions or a brief comparison of
alternatives, separate from the adapter's actual effort setting.
[Reasoning best practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices)

## Fable 5.1

The official guide recommends explicit scope, completion expectations and desired
formatting. For coding, it cautions against unnecessary extra changes and testing.
Dream's Fable style makes the finish condition and output format clear while
retaining the user's scope. It does not add a long expert-role preamble or assume
that the same effort value behaves identically across models.
[Fable 5.1 prompting guide](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1)

## Practical limits

Target styles are small writing adjustments, not separate model implementations.
The current provider performs optimization; changing the target style does not
select or load that model. Source-only factual tasks need evidence limits and
UNKNOWN for missing facts; creative tasks should not be source-locked by default.

Selected files are bounded reference material. Unsupported formats remain
metadata-only and are carried into Chat for the actual task. No unselected project
files, global memory, computer actions or tool calls are available to the optimizer.
The service validates response structure, not semantic faithfulness. Keep the
original input visible and review the proposed prompt before using it.

Model comparisons and real task-quality testing remain unperformed. No local
models were loaded for this implementation.
