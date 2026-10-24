# Effort

Status: Draft

## Scope

How the `effort` parameter controls model reasoning across vendors.

```python
class Effort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"
```

## Invariants

- **EFFORT-1**: `effort` MUST accept only `Effort` members or their string
  values; anything else raises `ValueError` before a request is sent.
- **EFFORT-2**: When `effort` is `None`, no reasoning parameter MUST be sent.
- **EFFORT-3**: An explicit `effort` MUST NOT disable reasoning.
- **EFFORT-4**: For known models, an unsupported value MUST NOT be sent. `max`
  resolves to the model's highest level; other values to the nearest supported
  level, rounding up. Unknown models receive the mapping below unresolved.
- **EFFORT-5**: The same `effort` and model MUST always produce the same vendor
  parameters.
- **EFFORT-6**: A request MUST NOT return an empty string because reasoning
  exhausted the output limit; it raises instead.
- **EFFORT-7**: A request that enables reasoning MUST ask for the reasoning's
  text wherever the vendor leaves it out by default. Showing it costs nothing:
  the vendor bills the reasoning the same whether it shows it or not. The
  request asks the same way every time, so EFFORT-5 holds.

## Mapping

| `effort` | Anthropic | OpenAI | Gemini 3.x | Token-budget models |
|---|---|---|---|---|
| `low` | `"low"` | `"low"` | `"low"` | 1,024 |
| `medium` | `"medium"` | `"medium"` | `"medium"` | 8,192 |
| `high` | `"high"` | `"high"` | `"high"` | 16,384 |
| `max` | `"max"` | `"max"` | `"high"` | model maximum |

- Anthropic: `thinking: {type: "adaptive", display: "summarized"}` +
  `output_config.effort`. Without `display`, the newer models return thinking
  blocks with empty text; the older ones accept it and summarize by default
  (EFFORT-7).
- OpenAI: `reasoning.effort` (Responses API).
- Gemini 3.x: `thinking_config.thinking_level`.
- Token-budget models (e.g. Gemini 2.5, Claude Haiku 4.5): `thinking_budget` /
  `budget_tokens`. Anthropic's show their reasoning unasked, so no `display` is
  sent.

## Non-goals

- Turning reasoning off.
- Hiding the reasoning's text, which some vendors offer so that a streamed
  answer starts sooner.
- Returning reasoning content from `ask`. `complete` and `stream` return
  it (EFFORT-7, [messages.md](messages.md), [streaming.md](streaming.md)).

## Open questions

- Should resolving to a different level log, or raise in a strict mode?
- Should a request without `effort` ask for the reasoning's text on models that
  reason by default? EFFORT-2 sends nothing, so their thinking blocks come back
  empty.
