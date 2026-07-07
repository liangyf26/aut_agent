# ADR 0007: LLM Capability Preflight and Task Routing

## Status

Accepted

## Context

Stage 2 depends on local or intranet LLMs, but provider capability is not uniform.
The prototype currently uses Browser Use for controlled exploration and Playwright
for deterministic verification. Browser Use's structured-output path may send
`response_format.type=json_schema`, while some providers or specific deployments
may only support plain text, `json_object`, or tool calling.

We have already observed a concrete divergence:

- A Browser Use run against `deepseek-v4-flash-260425` returned repeated
  `400 InvalidParameter` responses claiming `json_schema` is not supported.
- A direct chat completion against a DeepSeek-compatible endpoint can still
  succeed on ordinary prompts.

That means "the model responds" is not enough to route the model safely.
We need a reproducible preflight step that captures the exact capability surface
before the prototype starts.

## Decision

Before any stage-2 run, the system must execute a capability preflight that
probes the active model and records a capability tag set. The preflight must
distinguish at least:

1. Plain chat completion
2. `response_format.type=json_object`
3. `response_format.type=json_schema`
4. Tool calling
5. Forced tool calling with `tool_choice`
6. Auto tool calling without forced `tool_choice`
7. Browser Use structured-output compatibility
8. Browser Use DeepSeek wrapper compatibility, if that wrapper is used

The routing layer must then consume those tags and assign tasks accordingly:

- Models that pass `json_schema` are eligible for Browser Use structured-output
  paths.
- Models that only pass `json_object` or tool calling must not enter Browser Use
  paths that force `json_schema`.
- Models that support auto tool calling but reject forced `tool_choice` must not be
  routed to wrappers that force a specific tool call.
- Weak models may still be routed to summarization, classification, or simple
  extraction tasks.
- The routing decision must be persisted with the run artifacts so failures can
  be traced back to capability mismatch instead of being treated as generic
  execution failures.

## Consequences

- The prototype gains a deterministic gate before exploration starts.
- "The provider answered once" is no longer accepted as proof of capability.
- Browser Use-specific failures can be separated from provider-level failures.
- The system can safely support multiple local models with different capability
  envelopes.

## Implementation Notes

- Add a standalone capability probe script that reuses the same OpenAI-compatible
  client path used by the prototype.
- Keep the probe output structured and persisted to disk.
- Treat the probe result as part of run metadata and task routing, not as a
  one-off diagnostic.

## Follow-up

If future evidence shows that a provider supports `json_schema` only for a
subset of model names, model revisions, or request shapes, the routing layer
must record that nuance explicitly instead of collapsing it into a single
provider-wide tag.
