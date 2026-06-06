# Transactions

`client.transaction()` is an async context manager: it commits on clean exit and
rolls back on any exception. Mutations and queries issued through the transaction
run under its transaction id.

```python
async with client.transaction(isolation="serializable") as tx:
    await tx.insert(User(id=1, name="Ada"))
    await tx.update(User).set(name="Ada L.").where(User.id == 1)
# committed here; any exception rolls the whole transaction back

# Explicit control is also available:
tx = client.transaction()
await tx.begin()
try:
    await tx.insert(User(id=2, name="Grace"))
    await tx.commit()
except Exception:
    await tx.rollback()
    raise
```

A transaction id is **node-local**: it is allocated by the server that began the
transaction and is only meaningful on that server.

## Transactions in the AuraDB cluster preview

AuraDB's multi-node mode is an **experimental, opt-in preview** — there is no
production high availability, no automatic failover, and no distributed
transactions. Single-node mode remains the recommended production deployment, and
in single-node mode none of the rules below ever trigger.

In the preview, only the Raft **leader** accepts writes. A write sent to a
follower is rejected with a structured `not_leader` response, which the connector
raises as [`AuraNotLeaderError`](ERRORS.md). The transaction-safety rules are:

1. **No automatic redirect inside an active transaction.** A `not_leader` raised
   inside `client.transaction()` propagates to you unchanged; the connector never
   silently re-points an open transaction at another node.
2. **Transaction ids are node-local and cannot move across nodes.** There is no
   way to "continue" a transaction begun on a follower against the leader.
3. **Restart the transaction on the leader.** On `not_leader`, open a new client
   bound to the leader (`Client.connect_to_leader(exc)`), then begin a *fresh*
   transaction there and re-issue its statements. Do not replay individual writes
   blindly.
4. **Streaming cursors are not redirected mid-stream.** An open cursor's state
   also lives on one node; resolve the leader first, then start a new query.
5. **The leader-redirect helper is for pre-operation leader resolution, not blind
   write replay.** `client.with_leader_redirect()` only ever redirects on a
   `not_leader` response — a *pre-application* rejection, so the write never
   entered the Raft log and cannot be double-applied. It refuses to wrap a
   transaction (`LeaderRedirect.transaction()` raises `AuraTransactionError`) or a
   stream (`LeaderRedirect.stream()` raises `AuraBackendCapabilityError`).
6. **Redirects are bounded.** `max_redirects` caps the hops; there is no unbounded
   retry loop.

```python
from aura import AuraNotLeaderError

try:
    async with client.transaction() as tx:
        await tx.insert(Record(id=1, value="x"))
except AuraNotLeaderError as exc:
    # The whole transaction was rejected by a follower; restart it on the leader.
    leader = await client.connect_to_leader(exc)        # token auth + TLS preserved
    try:
        async with leader.transaction() as tx:
            await tx.insert(Record(id=1, value="x"))     # re-issue from scratch
    finally:
        await leader.close()
```

See [CLIENT.md](CLIENT.md) and [AURADB.md](AURADB.md) for the redirect helpers,
and `examples/auradb_leader_redirect.py` for a runnable walkthrough.
