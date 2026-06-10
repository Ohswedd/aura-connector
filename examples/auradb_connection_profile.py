"""Build a client from an environment-driven ConnectionProfile.

A ConnectionProfile is a small convenience layer over a DSN plus the operational
knobs deployments usually read from the environment (auth token, TLS CA, SNI
server name, timeouts, default isolation). It is **not** a secret manager: the
auth token is whatever your deployment's secret management already placed in the
environment, and the profile only redacts it from ``repr`` so it does not leak
into logs.

Run: ``python examples/auradb_connection_profile.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, ConnectionProfile


async def main() -> None:
    # In production these come from the real environment, e.g.:
    #   export AURA_ADDR="auras://db.internal:7000/app"
    #   export AURA_AUTH_TOKEN="$(read-from-secret-manager)"
    #   export AURA_TLS_CA="/etc/aura/ca.pem"
    # Here we pass an explicit mapping and use the in-memory reference backend so
    # the example runs with no server and no secrets.
    env = {
        "AURA_ADDR": "aura+memory://localhost/shop",
        "AURA_CONNECT_TIMEOUT_MS": "2500",
        "AURA_ISOLATION": "snapshot",
    }

    profile = ConnectionProfile.from_env(prefix="AURA", environ=env)
    # The token (if any) is redacted in the repr.
    print("profile:", profile)
    print("resolved isolation:", profile.isolation)  # snapshot (serializable -> snapshot)

    # Inspect the fully-resolved config without connecting.
    config = profile.to_client_config()
    print("connect timeout (s):", config.connect_timeout_s)

    # Open a client straight from the profile.
    async with Aura.from_profile(profile) as client:
        print("connected:", not client.is_closed)
        print("memory backend:", client.config.is_memory)


if __name__ == "__main__":
    asyncio.run(main())
