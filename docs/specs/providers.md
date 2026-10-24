# Providers

Status: Draft

## Scope

How a client reaches the vendors: the kinds of API it speaks, the providers it
is configured with, the model ids that name them, their keys, their base URLs,
and the timeout of one call.

## Words

- **Kind**: one vendor API, as this library speaks it: the request it builds,
  the response it parses, the effort mapping, the model table. The kinds are
  `anthropic` (the Messages API) and `openrouter` (OpenRouter's chat
  completions API).
- **Provider**: an endpoint speaking one kind, reached with one key: a kind, an
  API key and a base URL, under a name. The vendor's own endpoint is one
  provider; a gateway in front of it is another, of the same kind.
- **Model id**: `provider/model`, the name of a provider and the model as the
  vendor names it.

## Invariants

- **PROV-1**: A model id is split at its first `/`: the provider's name, then
  the model, which may itself contain `/`. A model id without a model, or
  naming a provider the client does not have, raises `ValueError` before
  anything is sent.
- **PROV-2**: Every client has the built-in providers, one per kind and named
  after it: `anthropic` at `https://api.anthropic.com`, whose key is
  `api_keys["anthropic"]` or else `ANTHROPIC_API_KEY`; and `openrouter` at
  `https://openrouter.ai/api/v1`, whose key is `api_keys["openrouter"]` or else
  `OPENROUTER_API_KEY`. Programs written before this specification keep
  working unchanged.
- **PROV-3**: `providers` adds providers by name, each with a `kind` and
  optionally an `api_key` and a `base_url`. One named like a built-in replaces
  it. A name that is empty or contains `/`, a kind the port does not know, or a
  name in both `api_keys` and `providers` raises `ValueError` when the client
  is created.
- **PROV-4**: A configured provider sends the key it was given and never reads
  an environment variable, so that a key never reaches a host it was not given
  for. A provider without a key raises `AuthenticationError` before anything is
  sent, the first time it is used.
- **PROV-5**: A provider's requests go to its `base_url`, with or without a
  trailing slash, followed by the kind's path: `/v1/messages` for `anthropic`,
  `/chat/completions` for `openrouter`. Without `base_url`, the kind's public
  URL. Nothing else about the request depends on the provider: two providers of
  one kind send the same body and headers for the same call, the key apart.
- **PROV-6**: The effort mapping ([effort.md](effort.md)) and the model table
  are the kind's: a provider of kind `anthropic` resolves `claude-haiku-4-5` to
  a token budget wherever it is served.
- **PROV-7**: Replay (MSG-2) is per kind: an assistant message returned by any
  provider of a kind is replayed as it came to any provider of that kind.
- **PROV-8**: `ask`, `complete` and `stream` take a `timeout` in seconds: the
  most to wait to connect, and for each read and write of the exchange; it is
  not a bound on the whole call. `None` leaves the client's timeout, 600 s with
  10 s to connect. A timeout that is not positive raises `ValueError` before
  anything is sent.

## Mapping

| | `anthropic` | `openrouter` |
|---|---|---|
| Public base URL | `https://api.anthropic.com` | `https://openrouter.ai/api/v1` |
| Path | `/v1/messages` | `/chat/completions` |
| Environment variable of the built-in | `ANTHROPIC_API_KEY` | `OPENROUTER_API_KEY` |
| Key header | `x-api-key` | `Authorization: Bearer` |

The base URLs follow the vendors' SDKs, so a base URL configured for an SDK
configures this library the same.

## Non-goals

- OpenAI's APIs: a kind for the Responses API and one for the chat completions
  API that OpenAI-compatible servers speak. Deferred; `openrouter` is the chat
  completions kind until then.
- Headers of the deployment's own, such as a gateway's tenant header.
- A key fetched when needed, or rotated. The key is a string.
- A timeout for the whole call, or across retries. That is the caller's.
