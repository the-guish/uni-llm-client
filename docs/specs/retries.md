# Retries

Status: Draft

## Scope

How `Client` and `AsyncClient` send a model call again when it failed in a way
that may pass on another attempt: a rate limit, an overloaded or failing vendor,
a timeout, a lost connection. `ask`, `complete` and `stream` retry alike; a
stream only until the vendor accepts it.

```python
Client(max_retries=2)
AsyncClient(max_retries=2)
```

## Invariants

- **RETRY-1**: `max_retries` is the number of retries after the first attempt:
  2 by default, 0 for none. A negative value raises `ValueError` when the client
  is created.
- **RETRY-2**: An attempt is retried when it fails with HTTP 408, 409, 429 or
  any 5xx, or with a connection failure or a timeout, and only then. The
  decision is on the error the attempt raised, so that an error a vendor
  reports in the body of a successful response is retried by the status code it
  carries, as if that were the status. Other 4xx, refusals, any other outcome
  of a response that parses, and errors raised before anything is sent (MSG-1)
  are not retried.
- **RETRY-3**: Unless RETRY-4 applies, the client waits `min(0.5 × 2^(n-1), 8)`
  seconds before retry `n`, less a random part of up to a quarter.
- **RETRY-4**: When the failed attempt's response carries `retry-after-ms`
  (milliseconds) or else `retry-after` (seconds, or an HTTP date), the client
  waits that long instead, if it is between 0 and 60 seconds. 0 retries at once.
  A longer, negative or unreadable value falls back to RETRY-3.
- **RETRY-5**: A connection failure or a timeout raises `TransportError`, with
  the HTTP library's exception as its cause, whether it is retried or not and
  wherever it happens, a stream included. Callers catch the library's errors
  alone.
- **RETRY-6**: Once the retries are spent, the last attempt's error is raised.
- **RETRY-7**: A stream is retried only until the vendor accepts it: on a
  connection failure or a timeout before a success status, and on an error
  status, whose body is read for its error. Once the vendor answers with a
  success status, any error, before or after the first event, is raised without
  a retry.
- **RETRY-8**: A retry sends the same request again: retries never change what
  a request contains. `ask` retries each request of its `pause_turn` loop on its
  own (MSG-8), and retries do not count towards that loop's limit.

## Mapping

| | Anthropic | OpenRouter |
|---|---|---|
| Retried statuses | 429 `rate_limit_error`; 500 `api_error`, 529 `overloaded_error` and any other 5xx | 408 timed out, 429 rate limited, 502 the model is down or answered badly, 503 no provider available |
| Statuses not retried | 400 `invalid_request_error`, 401, 403 `permission_error`, 404, 413 | 400, 401, 402 out of credits, 403 a refusal (MSG-5) |
| Wait asked | `retry-after` in seconds, with a rate limit | `retry-after` when it sends one |
| Errors in a 200 | None | `error`, at the top or in a choice with `finish_reason: "error"`, its `code` an HTTP status: retried by that code |
| Errors in a stream | `event: error`, not retried (RETRY-7) | A chunk with `error`, not retried (RETRY-7) |

409 and `retry-after-ms` are neither vendor's; they are honoured as the vendor
SDKs honour them.

## Non-goals

- Retrying a stream the vendor accepted, or resuming one
  ([streaming.md](streaming.md)).
- Idempotency keys. An attempt that timed out may have reached the vendor; its
  retry runs the call again, and the vendor bills both.
- A deadline across retries. It belongs to the caller, who bounds the whole
  call; each attempt has the HTTP client's timeout.
- Choosing the delays or the errors that are retried.
- Telling the caller about retries, through a hook or a count on the error.

## Open questions

- Should a stream retry an error the vendor sends before the first event, such
  as OpenRouter's error chunk, as `complete` retries OpenRouter's errors in the
  body?
- Should `retry-after` above 60 seconds raise at once rather than back off, as
  the vendor says the next attempts will fail too?
- Should the client honour `x-should-retry`, a header the vendor SDKs read to
  override the status?
