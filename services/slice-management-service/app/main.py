"""Adaptateurs d'entrée (API, traitement d'activation), client D4 et point d'assemblage."""
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime

import asyncpg
import httpx
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from candronex_common.application import creer_app
from candronex_common.clienthttp import DependanceIndisponible, Disjoncteur, creer_client, entetes, envoyer
from candronex_common.contexte import correlation_id
from candronex_common.erreurs import ErreurApi
from candronex_common.jeton import client_courant

from . import adapters_db
from .application import GestionServices, TraitementActivation
from .domain import IdentiteIntrouvable, Imsi, ServiceInstance
from .reseau import MockOpen5GSAdapter

DRONE_URL = os.environ.get("DRONE_URL", "http://drone-management-service:8000")


class IdentiteReseauHttp:
    """D4 : endpoint interne de drone-management-service, sans jeton (H3), derrière un disjoncteur."""

    def __init__(self, client: httpx.AsyncClient, disjoncteur: Disjoncteur):
        self.client, self.disjoncteur = client, disjoncteur

    async def resoudre(self, client_id: str, drone_id: str) -> Imsi:
        r = await self.disjoncteur.appeler(
            envoyer, self.client, "GET", f"/internal/v1/drone/{drone_id}/network-identity",
            params={"clientId": client_id}, headers=entetes(transmettre_jeton=False, correlation=correlation_id.get()))
        if r.status_code == 404:
            raise IdentiteIntrouvable(drone_id)
        if r.status_code != 200:
            raise DependanceIndisponible(f"D4 : réponse {r.status_code}")
        return Imsi(r.json()["imsi"])  # gardé en mémoire seulement


def creer_reseau():
    """Choix de l'adaptateur réseau (Phase 3 : NETWORK_ADAPTER=open5gs)."""
    if os.environ.get("NETWORK_ADAPTER", "mock") != "mock":
        raise RuntimeError("seul NETWORK_ADAPTER=mock existe en Phase 2")
    return MockOpen5GSAdapter(int(os.environ.get("MOCK_LATENCE_MIN_MS", "300")),
                              int(os.environ.get("MOCK_LATENCE_MAX_MS", "1500")),
                              float(os.environ.get("MOCK_TAUX_ECHEC", "0")))


gestion: GestionServices


@asynccontextmanager
async def lifespan(_):
    global gestion
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=2, max_size=int(os.environ.get("DB_POOL", "30")))
    async with pool.acquire() as c, c.transaction():
        await c.execute("SELECT pg_advisory_xact_lock(1)")
        await c.execute(adapters_db.SCHEMA)
    inventaire = adapters_db.InventairePostgres(pool)
    drones = creer_client(DRONE_URL, float(os.environ.get("D4_DELAI_S", "1")))
    reveil = asyncio.Event()
    gestion = GestionServices(inventaire, reveil)
    traitement = TraitementActivation(inventaire, IdentiteReseauHttp(drones, Disjoncteur("D4")), creer_reseau(),
                                      reveil, concurrence=int(os.environ.get("ACTIVATION_CONCURRENCE", "500")))
    tache = asyncio.create_task(traitement.tourner())
    yield
    tache.cancel()
    await drones.aclose()
    await pool.close()


app = creer_app("slice-management-service", lifespan)


def _dto(s: ServiceInstance) -> dict:
    return {"serviceId": s.service_id, "orderItemId": s.order_item_id, "orderId": s.order_id, "droneId": s.drone_id,
            "serviceType": s.service_type, "characteristics": s.characteristics, "state": s.state.value,
            "failureCause": s.failure_cause.value if s.failure_cause else None,
            "acceptedAt": s.accepted_at, "activatedAt": s.activated_at}


publique = APIRouter(prefix="/api/slice-management/v1")


@publique.get("/service")
async def services(droneId: str | None = Query(None, max_length=64), orderId: str | None = Query(None, max_length=32),
                   client_id: str = Depends(client_courant)):
    """Inventaire (client, par la passerelle) et D3 (service-ordering-service, jeton transmis)."""
    if not (droneId or orderId):
        raise ErreurApi(400, "DONNEES_INVALIDES", "Paramètre droneId ou orderId obligatoire.")
    return [_dto(s) for s in await gestion.services(client_id, orderId, droneId)]


class DemandeActivation(BaseModel):
    orderItemId: str = Field(min_length=1, max_length=32)
    orderId: str = Field(min_length=1, max_length=32)
    clientId: str = Field(min_length=1, max_length=128)
    droneId: str = Field(min_length=1, max_length=64)
    serviceType: str = Field(min_length=1, max_length=32)
    characteristics: dict
    acceptedAt: datetime


interne = APIRouter(prefix="/internal/v1")


@interne.post("/activation", status_code=202)
async def demander_activation(d: DemandeActivation, response: Response):
    """D2, sans jeton (H3) : jamais routé par la passerelle. Rejouer la même demande retourne le service existant."""
    s, cree = await gestion.demander_activation(d.orderItemId, d.orderId, d.clientId, d.droneId, d.serviceType,
                                                d.characteristics, d.acceptedAt)
    if not cree:
        response.status_code = 200
    return _dto(s)


@app.get("/health")
async def sante():
    return {"status": "UP"}


app.include_router(publique)
app.include_router(interne)
