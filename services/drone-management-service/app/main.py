"""Adaptateurs d'entrée HTTP et point d'assemblage de drone-management-service."""
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from candronex_common.application import creer_app
from candronex_common.erreurs import ErreurApi
from candronex_common.jeton import client_courant
from candronex_common.journal import evenement

from .adapters_db import SCHEMA, DepotDronesPostgres
from .application import GestionDrones
from .domain import DonneesInvalides, Drone, DroneDejaEnregistre, DroneIntrouvable, Imsi

gestion: GestionDrones


@asynccontextmanager
async def lifespan(_):
    global gestion
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=2, max_size=int(os.environ.get("DB_POOL", "20")))
    async with pool.acquire() as c, c.transaction():
        await c.execute("SELECT pg_advisory_xact_lock(1)")  # plusieurs processus au démarrage
        await c.execute(SCHEMA)
    gestion = GestionDrones(DepotDronesPostgres(pool))
    yield
    await pool.close()


app = creer_app("drone-management-service", lifespan)


class DroneEntrant(BaseModel):
    droneId: str = Field(min_length=1, max_length=64)
    imsi: str
    iccid: str
    simType: Literal["SIM", "ESIM"]


class DroneDTO(BaseModel):
    """Sans IMSI ni ICCID : construit depuis Drone, qui ne les porte pas."""
    droneId: str
    simType: str
    status: str
    registeredAt: datetime


def _dto(d: Drone) -> DroneDTO:
    return DroneDTO(droneId=d.drone_id, simType=d.sim_type.value, status=d.status, registeredAt=d.registered_at)


publique = APIRouter(prefix="/api/drone-management/v1")


@publique.post("/drone", status_code=201, response_model=DroneDTO)
async def enregistrer(corps: DroneEntrant, client_id: str = Depends(client_courant)):
    try:
        drone = await gestion.enregistrer(client_id, corps.droneId, corps.imsi, corps.iccid, corps.simType)
    except DonneesInvalides as e:
        raise ErreurApi(400, "DONNEES_INVALIDES", str(e))
    except DroneDejaEnregistre:
        raise ErreurApi(409, "DRONE_DEJA_ENREGISTRE", "Ce drone, cet IMSI ou cet ICCID est déjà enregistré.")
    evenement("drone enregistré", clientId=client_id, droneId=drone.drone_id, imsi=str(Imsi(corps.imsi)))
    return _dto(drone)


@publique.get("/drone", response_model=list[DroneDTO])
async def lister(limit: int = Query(100, ge=1, le=1000), client_id: str = Depends(client_courant)):
    return [_dto(d) for d in await gestion.lister(client_id, limit)]


@publique.get("/drone/{drone_id}", response_model=DroneDTO)
async def consulter(drone_id: str, client_id: str = Depends(client_courant)):
    try:
        return _dto(await gestion.consulter(client_id, drone_id))
    except DroneIntrouvable:
        evenement("accès refusé ou drone inexistant", clientId=client_id, droneId=drone_id)
        raise ErreurApi(404, "INTROUVABLE", "Drone introuvable.")


interne = APIRouter(prefix="/internal/v1")


@interne.get("/drone/{drone_id}/network-identity")
async def identite_reseau(drone_id: str, clientId: str):
    """D4, sans jeton (H3) : jamais routé par la passerelle."""
    try:
        imsi = await gestion.resoudre_identite(clientId, drone_id)
    except DroneIntrouvable:
        raise ErreurApi(404, "DRONE_INTROUVABLE", "Drone introuvable.")
    evenement("identité réseau résolue", clientId=clientId, droneId=drone_id, imsi=str(imsi))
    return {"imsi": imsi.valeur}


@app.get("/health")
async def sante():
    return {"status": "UP"}


app.include_router(publique)
app.include_router(interne)
