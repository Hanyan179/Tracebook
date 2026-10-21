# Tracebook read API v1

Base URL is user-configured, default `http://127.0.0.1:8765` on the Mac running Tracebook. Authentication is `Authorization: Bearer <scoped read token>`. Never distribute the device/admin connection token. No external account or hosted service is included.

| Method | Path | Behavior |
|---|---|---|
| GET | `/v1/assistant/capabilities` | Version, retrieval mode, read-only flag, granted scopes, limits |
| POST | `/v1/assistant/search` | Retrieve source excerpts |
| POST | `/v1/assistant/query` | Same retrieval contract, not an LLM completion |
| GET | `/v1/assistant/items/{kind}/{id}` | Read one source, with bounded pagination |

Scopes map to source kinds: `notes` → `note`, `transcripts` → `recording`, `daily` → `daily`, `analyses` → `analysis`, `confirmedMemory` → `memory`, `localInputs` → `input`, `documents` → `document`. New source kinds require explicit grants; an existing `notes` grant does not authorize local tool inputs or imported documents. Daily and analysis IDs are `YYYY-MM-DD`; other IDs are UUIDs. Pending/rejected/archived memories and archived notes/recordings are excluded. Audio, credentials, configuration, prompts and local chat history are never accessible with a read token. Explicitly granted `localInputs` exposes the normalized user-input archive, not whole tool sessions. An imported document's author is unconfirmed. A daily document may contain quotes of its contributing notes or recordings: granting `daily` authorizes that document's complete contents, independently of the source scopes.

Search JSON: `query` (1–500 characters), optional `scopes` (subset of grant), `dateFrom`, `dateTo`, `limit` (1–20; default 8), `language` (`en` or `zh`). An omitted scope uses all granted scopes. Dates are inclusive in the library timezone. Common relative dates today/yesterday/this week/last week and their Chinese equivalents are recognized when no explicit date is supplied. Retrieval uses words/Chinese character groups, not embeddings or model reasoning. Queries and source text are not logged by the external endpoint.

Results contain `mode: retrieval`, `generatedAnswer: false`, `trust: source_data`, `sources`, `coverage` and `createdAt`. Each source contains `id`, `kind`, `title`, `date`, `state`, `textAvailable`, `contentVersion` (SHA-256 of text), `uri` (stable `library://kind/id` identifier), `excerpt` and `excerptTruncated`. Excerpts are limited to 1,400 characters each and 12,000 total. Coverage states selected scopes, dates, eligible count, returned count and `hasMore`. There is no full-corpus export or search pagination endpoint; narrow the query when needed. External file edits are reindexed within roughly 60 seconds while the node is running. App edits are indexed immediately.

Read query parameters: `offset` (default 0), `limit` (1–16,000, default 8,000), optional `version`. The result includes source metadata plus `text`, `offset`, `totalCharacters`, `nextOffset` (null at end). Offsets count Unicode code points. Use the returned `contentVersion` as `version` for subsequent reads. A changed source returns 409.

Tokens expire after 1–90 days, can be revoked immediately, and are stored only as hashes. Maximum 60 requests per minute per token; restart resets the in-memory rate window. Last-use metadata is retained, not query text. Manage grants only through the app on the host computer. These are prototype limits, not a hardened public-service SLA.

HTTP status: 400 invalid request; 401 invalid/expired/revoked credential; 403 unauthorized scope or operation; 404 unavailable source; 409 content changed; 429 rate limited. The helper rejects redirects, non-loopback HTTP, insecure token-file permissions and oversized responses. Cloud agents cannot reach localhost without a separately arranged secure connection; this package does not create one.

The skill follows the [Agent Skills specification](https://agentskills.io/specification). `openapi.json` follows [OpenAPI 3.1](https://spec.openapis.org/oas/v3.1.1.html).
