# Source baseline and API contract

Reviewed on 2026-09-15. The CLI targets the HOMER 7 web API family. It is an independent Python client; it does not bundle or run the upstream server.

| Repository | Branch at checkout | Pinned commit |
|---|---|---|
| [sipcapture/homer](https://github.com/sipcapture/homer/tree/3b5683591bbe1b0d40c838c56b5b4af18aa8505e) | homer7 | `3b5683591bbe1b0d40c838c56b5b4af18aa8505e` |
| [sipcapture/homer-app](https://github.com/sipcapture/homer-app/tree/fea7c9fa153b6b28f8467fe95749626a0e4e35b4) | master | `fea7c9fa153b6b28f8467fe95749626a0e4e35b4` |
| [sipcapture/homer-ui](https://github.com/sipcapture/homer-ui/tree/4d0f7bc461a632151dedb861990d877f9aa32741) | master | `4d0f7bc461a632151dedb861990d877f9aa32741` |

Backend `version.go` reports `1.5.21`. This version string is not the HOMER product major version. Upstream master branches may contain newer compatibility changes; the backend at the pinned commit is the implementation reference. The user's installed HOMER 7 minor/build version was not supplied, so universal compatibility is not claimed.

## Routes actually used

All routes are relative to the configured prefix plus `/api/v3`.

| Method | Route | Contract |
|---|---|---|
| POST | `/auth` | `{username,password}`; success HTTP 201 with root `token` |
| GET | `/mapping/protocol` | `{count,data:[{hepid,profile,fields_mapping:[{id,type,...}]}]}` |
| POST | `/search/call/data` | `param.search[profile]` is an array of conditions; values must be strings |
| POST | `/call/transaction` | `param.search[profile]` is an object with `id`, `callid:[]`, `uuid:[]`; returns `data.messages` |
| POST | `/search/call/message` | `param.search[profile]` is an object with numeric `id`; returns `data:[]` |
| POST | `/export/call/messages/pcap` | Transaction body; binary response |
| POST | `/export/call/messages/text` | Transaction body; text response |

The CLI intentionally has no arbitrary HTTP/SQL execution command. Mapping reset routes are GET requests but mutate server state; they are outside the route allowlist.

## Critical semantics confirmed from code

- [`router/v1/search.go`](https://github.com/sipcapture/homer-app/blob/fea7c9fa153b6b28f8467fe95749626a0e4e35b4/router/v1/search.go) and [`controller/v1/search.go`](https://github.com/sipcapture/homer-app/blob/fea7c9fa153b6b28f8467fe95749626a0e4e35b4/controller/v1/search.go): exact paths, request model, 201 search responses and binary exports.
- [`main.go`](https://github.com/sipcapture/homer-app/blob/fea7c9fa153b6b28f8467fe95749626a0e4e35b4/main.go): API base, JWT middleware and optional configured API Token header/access.
- [`controller/v1/user.go`](https://github.com/sipcapture/homer-app/blob/fea7c9fa153b6b28f8467fe95749626a0e4e35b4/controller/v1/user.go): login request and root token response.
- [`data/service/mapping.go`](https://github.com/sipcapture/homer-app/blob/fea7c9fa153b6b28f8467fe95749626a0e4e35b4/data/service/mapping.go): live profile/mapping discovery.
- [`model/search.go`](https://github.com/sipcapture/homer-app/blob/fea7c9fa153b6b28f8467fe95749626a0e4e35b4/model/search.go): timestamp fields, location nodes, protocol header fields.
- [`data/service/search.go`](https://github.com/sipcapture/homer-app/blob/fea7c9fa153b6b28f8467fe95749626a0e4e35b4/data/service/search.go): `buildQuery` reads `limit` from the condition array; all values must be JSON strings. `SearchData` uses this per-node limit before the in-memory sort. `total` is returned rows, not total database hits. Therefore an offset-based full retrieval guarantee would be unsound.
- The same file truncates incoming milliseconds to whole seconds before querying inclusive `BETWEEN`; the CLI rounds outward, partitions with overlapping boundaries and filters search results back to the requested half-open interval.
- Integer `!=` conditions are problematic: operator selection checks the prefix but integer conversion receives the prefix as part of the string. CLI rejects integer exclusions instead of returning silently incorrect results. String sanitization also rewrites `&` and quotes; ambiguous values are rejected.
- The same file applies transaction correlation lookups/ranges and deduplication; the CLI does not promise exact capture counts for traces. Some backend database errors are not propagated in the top-level search/transaction flow; successful HTTP alone is not proof of healthy capture/database coverage.
- [`utils/heputils/heputils.go`](https://github.com/sipcapture/homer-app/blob/fea7c9fa153b6b28f8467fe95749626a0e4e35b4/utils/heputils/heputils.go): an empty database node list matches all sessions, and nodes are matched case-insensitively.
- [`utils/exportwriter/exportwriter.go`](https://github.com/sipcapture/homer-app/blob/fea7c9fa153b6b28f8467fe95749626a0e4e35b4/utils/exportwriter/exportwriter.go): exported packets reconstruct Ethernet/IP/transport layers from stored fields and raw text; they do not preserve original on-wire IP fragmentation.

`tests/fixtures/mapping.json` contains just field IDs and types extracted from `FieldsMapping1call` in [`migration/jsonschema/jsonschema.go`](https://github.com/sipcapture/homer-app/blob/fea7c9fa153b6b28f8467fe95749626a0e4e35b4/migration/jsonschema/jsonschema.go). Synthetic SIP fixtures and the HTTP test service are authored for this client; they are not production traffic.
