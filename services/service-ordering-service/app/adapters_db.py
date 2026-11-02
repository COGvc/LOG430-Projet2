"""Adaptateur de sortie PostgreSQL (asyncpg) du module commande."""
import json
from datetime import timedelta

import asyncpg

from .domain import Commande, ElementCommande

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS order_seq START 1001;
CREATE TABLE IF NOT EXISTS service_order (
    order_id            text        PRIMARY KEY,
    client_id           text        NOT NULL,
    idempotency_key     text        NOT NULL,
    request_fingerprint text        NOT NULL,
    accepted_at         timestamptz NOT NULL,
    UNIQUE (client_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS service_order_client ON service_order (client_id, accepted_at DESC);
CREATE TABLE IF NOT EXISTS order_item (
    order_item_id     text        PRIMARY KEY,
    order_id          text        NOT NULL REFERENCES service_order,
    rang              int         NOT NULL,
    drone_id          text        NOT NULL,
    service_type      text        NOT NULL,
    characteristics   jsonb       NOT NULL,
    dispatch_status   text        NOT NULL,
    dispatch_attempts int         NOT NULL DEFAULT 0,
    next_dispatch_at  timestamptz NOT NULL,
    UNIQUE (order_id, drone_id, service_type)
);
CREATE INDEX IF NOT EXISTS order_item_order ON order_item (order_id);
CREATE INDEX IF NOT EXISTS order_item_a_transmettre ON order_item (next_dispatch_at) WHERE dispatch_status = 'PENDING';
"""

BAIL = timedelta(seconds=30)  # Un élément réservé et non traité redevient disponible après 30 s


def _element(r) -> ElementCommande:
    return ElementCommande(r["order_item_id"], r["drone_id"], r["service_type"],
                           json.loads(r["characteristics"]), r["dispatch_status"])


def _commande(r, elements=()) -> Commande:
    return Commande(r["order_id"], r["client_id"], r["idempotency_key"], r["request_fingerprint"],
                    r["accepted_at"], list(elements))


class DepotCommandesPostgres:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def prochain_numero(self) -> int:
        return await self.pool.fetchval("SELECT nextval('order_seq')")

    async def ajouter(self, c: Commande) -> Commande:
        try:
            async with self.pool.acquire() as cx, cx.transaction():
                await cx.execute("INSERT INTO service_order VALUES ($1, $2, $3, $4, $5)",
                                 c.order_id, c.client_id, c.idempotency_key, c.request_fingerprint, c.accepted_at)
                await cx.executemany(
                    "INSERT INTO order_item (order_item_id, order_id, rang, drone_id, service_type, characteristics,"
                    " dispatch_status, next_dispatch_at) VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'PENDING', $7)",
                    [(e.order_item_id, c.order_id, i, e.drone_id, e.service_type, json.dumps(e.characteristics),
                      c.accepted_at) for i, e in enumerate(c.elements, start=1)])
            return c
        except asyncpg.UniqueViolationError:
            return await self.trouver_par_cle(c.client_id, c.idempotency_key)

    async def _avec_elements(self, r) -> Commande | None:
        if r is None:
            return None
        items = await self.pool.fetch("SELECT * FROM order_item WHERE order_id = $1 ORDER BY rang", r["order_id"])
        return _commande(r, map(_element, items))

    async def trouver_par_cle(self, client_id: str, cle: str) -> Commande | None:
        return await self._avec_elements(await self.pool.fetchrow(
            "SELECT * FROM service_order WHERE client_id = $1 AND idempotency_key = $2", client_id, cle))

    async def trouver(self, client_id: str, order_id: str) -> Commande | None:
        return await self._avec_elements(await self.pool.fetchrow(
            "SELECT * FROM service_order WHERE client_id = $1 AND order_id = $2", client_id, order_id))

    async def lister(self, client_id: str, limite: int) -> list[Commande]:
        rows = await self.pool.fetch(
            "SELECT * FROM service_order WHERE client_id = $1 ORDER BY accepted_at DESC LIMIT $2", client_id, limite)
        return [_commande(r) for r in rows]

    async def reserver_a_transmettre(self, limite: int):
        rows = await self.pool.fetch(
            """UPDATE order_item i SET next_dispatch_at = now() + $2::interval
               FROM service_order o
               WHERE o.order_id = i.order_id AND i.order_item_id IN (
                   SELECT order_item_id FROM order_item
                   WHERE dispatch_status = 'PENDING' AND next_dispatch_at <= now()
                   ORDER BY next_dispatch_at LIMIT $1 FOR UPDATE SKIP LOCKED)
               RETURNING i.*, o.client_id, o.idempotency_key, o.request_fingerprint, o.accepted_at""",
            limite, BAIL)
        return [(_commande(r), _element(r), r["dispatch_attempts"]) for r in rows]

    async def marquer_transmis(self, order_item_id: str) -> None:
        await self.pool.execute("UPDATE order_item SET dispatch_status = 'DISPATCHED' WHERE order_item_id = $1",
                                order_item_id)

    async def marquer_rejete(self, order_item_id: str) -> None:
        await self.pool.execute("UPDATE order_item SET dispatch_status = 'REJECTED' WHERE order_item_id = $1",
                                order_item_id)

    async def reporter(self, order_item_id: str, tentatives: int, delai: timedelta) -> None:
        await self.pool.execute(
            "UPDATE order_item SET dispatch_attempts = $2, next_dispatch_at = now() + $3::interval"
            " WHERE order_item_id = $1", order_item_id, tentatives, delai)
