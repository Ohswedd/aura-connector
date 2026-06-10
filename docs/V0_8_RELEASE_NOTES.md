# Aura Connector v0.8.0 release notes

**Theme: production operability and search quality.**

Aura Connector v0.8.0 is the paired client for the AuraDB v1.4.0 line. It is an **additive,
backward-compatible** release that adds **client-side ergonomics only** — connection profiles,
search-eval report parsing helpers, and capability UX helpers. There is **no wire-protocol
change and no server protocol change required**: existing `connect(...)` / `Client(...)`
constructors are unchanged, and a v0.7.x connector also keeps working unchanged against AuraDB
v1.4.0.

See [COMPATIBILITY.md](COMPATIBILITY.md), [AURADB.md](AURADB.md),
[SEARCH_AND_RANKING.md](SEARCH_AND_RANKING.md), and [ROADMAP.md](ROADMAP.md).

## Shipped

- **`ConnectionProfile`** — a frozen dataclass bundling DSN/auth/TLS/pool settings.
- **`ConnectionProfile.from_env`** — build a profile from environment variables.
- **`Client.from_profile` / `Aura.from_profile`** — construct a client/session from a profile.
- **TLS CA profile wiring** — a profile can carry the TLS CA file.
- **TLS `server_name`/SNI wiring for the bundled TCP transport** — a profile's `server_name`
  is carried as the `tls_server_name` option and used as the SNI host by the bundled asyncio
  TCP transport.
- **Token-redacting `repr`** — `ConnectionProfile.__repr__` redacts the auth token.
- **Search-eval report parsing helpers** — parse AuraDB `auradb search eval` output.
- **`SearchEvalReport` / `SearchEvalMetrics` / `SearchEvalQueryResult`** — typed report models.
- **BM25 / hybrid report models** — typed access to per-mode parameters and metrics.
- **Capability `require`/`describe` helpers** — `BackendCapabilities.require(flag)` and
  `.describe()`.
- **Examples** — `examples/auradb_connection_profile.py`,
  `examples/auradb_search_eval_report.py`, and `examples/auradb_capabilities.py`.

## Unchanged

- **`DEFAULT_ISOLATION` is `snapshot`.**
- **`"serializable"` remains a deprecated alias to snapshot only** — not a distinct level.
- **No unbounded retry/failover automation.**
- **No production HA abstraction.**
- **No production ANN claim.**
- **AuraDB-only features require server capabilities** — a backend that does not advertise a
  capability raises a structured `AuraCapabilityError` rather than emulating it.

## Known limitations

- **`ConnectionProfile` is not a secret manager** — it bundles connection settings; secret
  storage and rotation remain the application's responsibility.
- **Native-accel SNI override remains a follow-up** — the `server_name`/SNI override is wired
  into the bundled asyncio TCP transport; the native-accel backend path does not yet consume it.
- **The search-eval helpers parse AuraDB CLI output** — they do not run server-side CLI
  commands or compute relevance themselves.

## Upgrade

No code changes are required to upgrade from v0.7.x: all existing constructors and APIs are
unchanged. Adopt `ConnectionProfile` / `from_env` / `from_profile` to centralize connection
settings, and `aura.search_quality` to consume `auradb search eval` reports. The default
transaction isolation remains `snapshot`.
