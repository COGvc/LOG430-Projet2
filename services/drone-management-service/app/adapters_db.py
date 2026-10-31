"""Adaptateur de sortie PostgreSQL (asyncpg)."""
import asyncpg

from .domain import Drone, DroneDejaEnregistre, IdentiteReseau, Iccid, Imsi, SimType

SCHEMA = """
CREATE TABLE IF NOT EXISTS drone (
    client_id     text        NOT NULL,
    drone_id      text        NOT NULL,
    sim_type      text        NOT NULL,
    status        text        NOT NULL,
    registered_at timestamptz NOT NULL,
    PRIMARY KEY (client_id, drone_id)
);
CREATE TABLE IF NOT EXISTS identite_reseau (
    client_id text        NOT NULL,
    drone_id  text        NOT NULL,
    imsi      char(15)    NOT NULL UNIQUE,
    iccid     varchar(20) NOT NULL UNIQUE,
    PRIMARY KEY (client_id, drone_id),
    FOREIGN KEY (client_id, drone_id) REFERENCES drone ON DELETE CASCADE
);
"""


def _drone(r) -> Drone:
    return Drone(r["client_id"], r["drone_id"], SimType(r["sim_type"]), r["status"], r["registered_at"])


class DepotDronesPostgres:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def ajouter(self, drone: Drone, identite: IdentiteReseau) -> None:
        try:
            async with self.pool.acquire() as c, c.transaction():
                await c.execute(
                    "INSERT INTO drone VALUES ($1, $2, $3, $4, $5)",
                    drone.client_id, drone.drone_id, drone.sim_type.value, drone.status, drone.registered_at,
                )
                await c.execute(
                    "INSERT INTO identite_reseau VALUES ($1, $2, $3, $4)",
                    drone.client_id, drone.drone_id, identite.imsi.valeur, identite.iccid.valeur,
                )
        except asyncpg.UniqueViolationError as e:
            raise DroneDejaEnregistre(drone.drone_id) from e

    async def trouver(self, client_id: str, drone_id: str) -> Drone | None:
        r = await self.pool.fetchrow(
            "SELECT * FROM drone WHERE client_id = $1 AND drone_id = $2", client_id, drone_id)
        return _drone(r) if r else None

    async def lister(self, client_id: str, limite: int) -> list[Drone]:
        rows = await self.pool.fetch(
            "SELECT * FROM drone WHERE client_id = $1 ORDER BY registered_at DESC LIMIT $2", client_id, limite)
        return [_drone(r) for r in rows]

    async def trouver_identite(self, client_id: str, drone_id: str) -> IdentiteReseau | None:
        r = await self.pool.fetchrow(
            "SELECT imsi, iccid FROM identite_reseau WHERE client_id = $1 AND drone_id = $2", client_id, drone_id)
        return IdentiteReseau(Imsi(r["imsi"]), Iccid(r["iccid"])) if r else None
