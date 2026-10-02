---
name: api-design
description: "Shape an API before building it: resources, requests, responses, errors, versioning and examples in one short spec."
---

# API design

Use this when a new endpoint, library interface or message format is about to be built, or when an existing one needs
a change that callers will notice.

## Method

1. Start from the callers. List who calls it, what they are trying to do and what they already hold (identifiers,
   tokens, context). Read the existing interfaces with `read_file` and `grep` so the new one matches their naming,
   pagination and error style.
2. Name the operations in the caller's words. For HTTP, pick resources and methods; for a library, pick functions and
   types. Prefer a few operations that compose over one that takes many flags.
3. Specify each operation: inputs with types, which are required, limits and defaults; the success response; every
   error with its code, meaning and whether a retry helps. State idempotency, ordering and consistency guarantees.
4. Decide what is safe to change later: versioning, additive fields, deprecation. Write down what callers may rely on.
5. Check authorization for each operation: who may call it, on whose data, and what is logged.
6. Write two or three worked examples (request and response) that cover a success, a validation error and an edge
   case. Where the project has a mock or test server, run them with `run_bash`.

## Output

A spec file written with `write_file` (Markdown, or the project's OpenAPI or schema format) containing: purpose,
operations table, types, errors, examples, compatibility rules and open questions. Keep it short enough to review in
one sitting.

## Checks

- Every error a caller can receive is listed and distinguishable.
- Names, casing, dates and pagination match the rest of the project.
- The examples are valid against the stated types.

## Pitfalls

- Designing from the database outward, so internal columns leak into the interface.
- Booleans that later need a third value; unbounded lists without paging.
- Errors that differ only in their message text.

Limits: a spec is a proposal. Review it with the people who will call it before code depends on it.
