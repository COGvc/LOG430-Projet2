"""Adaptateur de sortie PostgreSQL (asyncpg) : l'inventaire des services. Aucune colonne IMSI (H4)."""
import json
from datetime import timedelta

import asyncpg

from .domain import CauseEchec, Etat, ServiceInstance

SCHEMA = """
CREATE TABLE IF NOT EXISTS service (
    service_id      uuid        PRIMARY KEY,
    order_item_id   text        NOT NULL UNIQUE,
    order_id        text        NOT NULL,
    client_id       text        NOT NULL,
    drone_id        text        NOT NULL,
    service_type    text        NOT NULL,
    characteristics jsonb       NOT NULL,
    state           text        NOT NULL,
    failure_cause   text,
    attempts        int         NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL,
    accepted_at     timestamptz NOT NULL,
    received_at     timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    activated_at    timestamptz,
    failed_at       timestamptz
);
CREATE INDEX IF NOT EXISTS service_a_activer ON service (next_attempt_at) WHERE state IN ('PENDING', 'ACTIVATING');
CREATE INDEX IF NOT EXISTS service_commande ON service (client_id, order_id);
CREATE INDEX IF NOT EXISTS service_drone ON service (client_id, drone_id);
"""

# I7 : un service réservé dont le traitement a disparu (redémarrage) redevient disponible après le bail.
# Cela couvre aussi la reprise au redémarrage (Q3) : aucun traitement spécial au démarrage.
BAIL = timedelta(seconds=10)


def _service(r) -> ServiceInstance:
    return ServiceInstance(
        str(r["service_id"]), r["order_item_id"], r["order_id"], r["client_id"], r["drone_id"], r["service_type"],
        json.loads(r["characteristics"]), Etat(r["state"]), r["accepted_at"],
        CauseEchec(r["failure_cause"]) if r["failure_cause"] else None, r["attempts"],
        r["started_at"], r["activated_at"], r["failed_at"])


class InventairePostgres:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def ajouter_si_absent(self, s: ServiceInstance) -> tuple[ServiceInstance, bool]:
        r = await self.pool.fetchrow(
            """INSERT INTO service (service_id, order_item_id, order_id, client_id, drone_id, service_type,
                                    characteristics, state, next_attempt_at, accepted_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, 'PENDING', now(), $8)
               ON CONFLICT (order_item_id) DO NOTHING RETURNING *""",
            s.service_id, s.order_item_id, s.order_id, s.client_id, s.drone_id, s.service_type,
            json.dumps(s.characteristics), s.accepted_at)
        if r:
            return _service(r), True
        r = await self.pool.fetchrow("SELECT * FROM service WHERE order_item_id = $1", s.order_item_id)
        return _service(r), False

    async def reserver_echus(self, limite: int) -> list[ServiceInstance]:
        rows = await self.pool.fetch(
            """UPDATE service SET state = 'ACTIVATING', started_at = coalesce(started_at, now()),
                                  next_attempt_at = now() + $2::interval
               WHERE service_id IN (
                   SELECT service_id FROM service
                   WHERE state IN ('PENDING', 'ACTIVATING') AND next_attempt_at <= now()
                   ORDER BY next_attempt_at LIMIT $1 FOR UPDATE SKIP LOCKED)
               RETURNING *""", limite, BAIL)
        return [_service(r) for r in rows]

    async def enregistrer(self, s: ServiceInstance) -> None:
        await self.pool.execute(
            "UPDATE service SET state = $2, failure_cause = $3, activated_at = $4, failed_at = $5"
            " WHERE service_id = $1",
            s.service_id, s.state.value, s.failure_cause.value if s.failure_cause else None,
            s.activated_at, s.failed_at)

    async def reporter(self, s: ServiceInstance, delai: timedelta) -> None:
        await self.pool.execute(
            "UPDATE service SET state = 'PENDING', attempts = attempts + 1, next_attempt_at = now() + $2::interval"
            " WHERE service_id = $1", s.service_id, delai)

    async def par_commande(self, client_id: str, order_id: str) -> list[ServiceInstance]:
        rows = await self.pool.fetch("SELECT * FROM service WHERE client_id = $1 AND order_id = $2"
                                     " ORDER BY order_item_id", client_id, order_id)
        return [_service(r) for r in rows]

    async def par_drone(self, client_id: str, drone_id: str) -> list[ServiceInstance]:
        rows = await self.pool.fetch("SELECT * FROM service WHERE client_id = $1 AND drone_id = $2"
                                     " ORDER BY accepted_at DESC LIMIT 100", client_id, drone_id)
        return [_service(r) for r in rows]
