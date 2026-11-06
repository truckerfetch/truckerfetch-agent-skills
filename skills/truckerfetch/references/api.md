# TruckerFetch API reference

Use the maintained [documentation](https://truckerfetch.com/docs), [live OpenAPI contract](https://truckerfetch.com/api/v1/openapi.json) and [MCP client resources](https://github.com/truckerfetch/truckerfetch-mcp) for complete request schemas. The API and MCP share product authorization and data, but use different credentials.

## Access and scopes

REST uses `Authorization: Bearer $TRUCKERFETCH_API_KEY` over TLS. Set the key outside source control. Hosted MCP uses OAuth tokens for the resource `https://truckerfetch.com/api/mcp`; a REST key is not a hosted MCP token. Active Pro is required for both. The local MCP bridge uses a REST key.

| Scope | Purpose |
| --- | --- |
| `data:read` | Published catalog, editions, profiles, authority records, changes and permitted evidence |
| `contacts:read` | Permitted contact fields; also required for external CSV exports |
| `insurance:read` | Insurance filings and renewal research; insurance availability is separately enforced |
| `watchlists:read` / `watchlists:write` | Read / change the owner's watchlists and targets |
| `exports:read` / `exports:write` | Read / create the owner's export jobs |
| `archive:unlock` | Open a historical edition after account-owner confirmation |
| `webhooks:read` | Read sanitized webhook endpoint/delivery state |

Scopes combine with the selection and requested data. Possessing an ID or an old cursor grants no access. Downgrades, revocation, suppression and expired historical dates override prior access.

## Read and search

Paths below are relative to `/api/v1`.

| Request | Inputs and result |
| --- | --- |
| `GET /account`, `GET /usage` | Current account and allowance information |
| `GET /editions/latest`, `GET /editions`, `GET /editions/{id}` | Published metadata; listing dates spends no historical unlock |
| `POST /carriers/search` | `{filters, limit, cursor?}` searches the catalog |
| `POST /editions/{id}/search` | Same search body; date must be latest or already unlocked and still available |
| `GET /carriers/{dot}` | USDOT string; optional `edition_id` query for authorized pinned profile |
| `GET /carriers/{dot}/authorities` | Resolved authority records; preserve docket prefix/type and record ID |
| `GET /carriers/{dot}/insurance-filings` | Observed filings, date basis and supporting evidence |
| `POST /renewals/search` | Search body plus required explicit `date_basis` and `window_days` (30, 45 or 60) |
| `GET /evidence/{id}` | Redacted authorized source reference |
| `GET /changes` | Optional `usdot`, `watchlist_id`, `since`, `event_types`, `cursor`, `limit` |
| `GET /watchlists`, `GET /watchlists/{id}` | Owner-scoped watchlists |
| `GET /watchlists/{id}/targets`, `GET /watchlists/{id}/events` | Exact authority targets / matching events |
| `GET /exports`, `GET /exports/{id}` | Owner-scoped export state |
| `GET /exports/{id}/download`, `GET /exports/{id}/parts/{part}` | Authenticated manifest and bounded CSV parts |

Search `filters` supports `q`, uppercase two-letter `states`, `classifications`, `entity_types`, `cargo`, `filing_types`, `insurer`, `census_status`, `operations`, and inclusive `fleet`, `trucks`, `drivers` ranges with `min`/`max`. Use known source codes; do not invent filter codes. Page size defaults to 50, maximum 100. Preserve the original filters with each cursor; never parse or synthesize opaque cursors. Data-search cursors expire one hour after the first page. On `cursor_expired` restart explicitly with the same user selection, acknowledging that the published release may have changed. On `selection_changed`, restart rather than reusing the cursor.

JSON success uses `data` and `meta`, with optional `next_cursor`. Record IDs and USDOTs are strings. Error payloads usually have `error.code`, `error.message`, and `requestId`; confirmation responses additionally return `confirmation_url` and `expires_at`. Prefer the stable code and sanitized request ID when reporting a failure.

```python
import os
from client import Client

client = Client(api_key=os.environ["TRUCKERFETCH_API_KEY"])
page = client.request("POST", "carriers/search", data={
    "filters": {"states": ["TX"], "fleet": {"min": 1, "max": 10}},
    "limit": 20,
})
```

## Dates and evidence

`edition_date_et` is an Eastern calendar date. Latest can retain an older source date during a publication delay; do not relabel it as today's data. Historical availability is a rolling 90 Eastern calendar dates, plus the latest published edition during a delay. An expired historical edition returns `archive_expired` even if opened earlier.

Keep these date bases distinct:

- `filing_anniversary`: anniversary of an observed filing, not policy expiration.
- `estimated_policy_renewal`: explicitly estimated; retain model/evidence context.
- `verified_expiration`: supported expiration evidence only.
- `filed_cancellation`: filed cancellation date, with its own effective/observation context.
- `unknown`: no supported named date basis.

Retain source, release, observed-at and effective-at references in research outputs. Census registration, operating authority and insurance filing statuses describe different facts. Unknown and scope-withheld fields are not interchangeable.

## Changes, confirmations and idempotency

| Request | Requirement |
| --- | --- |
| `POST /editions/{id}/access` | `archive:unlock`, body `{confirm_edition_date_et}`, saved `Idempotency-Key`; follow account-owner approval link and retry identically |
| `POST /watchlists` | `watchlists:write`, `{name}`, saved key; use user-confirmed name |
| `POST /watchlists/{id}/items` | `watchlists:write`, `{usdots: [...]}`; maximum 100, user-authorized target selection |
| `POST /watchlists/{id}/targets` | Saved key and `{usdot, docket_number, authority_type, authority_record_id}` resolved from published authority evidence |
| `DELETE /watchlists/{id}/targets/{targetId}` | User-authorized exact target; deletion is idempotent |
| `PATCH /watchlists/{id}/rules` | `{paused? , event_types?}`; at least one field, using supported event types |
| `POST /exports` | `exports:write`, `contacts:read`, saved key and authorized selection; renewals also need insurance scope |

A key is 8–128 characters from letters, digits, `-._:`. Generate it once (a UUID is suitable), durably save it with the validated payload, and reuse on retries. A conflicting payload is `409`; never retry a conflict using a fresh key automatically. The helper makes one network attempt and exposes errors so the caller retains control.

Export selections use `scope: "catalog"`, `"edition"`, `"list"` or `"renewals"`. Edition requires `edition_id`; list requires `list_id`; renewals requires `date_basis` and `window_days`. The sample workflow is dashboard-only. REST export creation returns `202` with the job; hosted MCP may first return `confirmation_required` with an owner-review URL. Follow only a same-origin `/developer/confirm/…` URL from TruckerFetch. Never auto-approve it. Wait for approval and retry the exact original MCP request with the same key. Poll the returned job rather than enqueueing another export. Manifest and part downloads require authentication and current scopes; do not publish download links or data.

## Recovery

- `401`: stop and reconnect/rotate the authorized credential.
- `402 pro_required`: active Pro is required; do not retry as if it were transient.
- `403 insufficient_scope`: request the narrowly needed authorized scope; do not infer withheld fields.
- `404`: resource is absent or inaccessible to this owner.
- `409 confirmation_required`: hand the review URL to the owner and retain request identity.
- `409 selection_changed` / idempotency conflict: resolve state before starting a new operation.
- `410 archive_expired`, `cursor_expired` or expired artifact: explain the specific expiry; unlocks cannot recover expired editions.
- `429`: honor `Retry-After`; retain payload and key.
- `503`: a binding, source family or service may be unavailable; use bounded retries only within the user's authorization.

For change feeds, persist the resume cursor only after storing the page and deduplicate by event ID. Do not replace durable feed state with a local wall-clock timestamp. Consult the current docs for replay availability and resynchronization.
