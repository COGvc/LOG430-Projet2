"""Adaptateurs d'entrée (API, relance) et point d'assemblage de service-ordering-service."""
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime

import asyncpg
from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field

from candronex_common.application import creer_app
from candronex_common.clienthttp import DependanceIndisponible, Disjoncteur, creer_client
from candronex_common.erreurs import ErreurApi
from candronex_common.jeton import client_courant

from . import adapters_db, catalog, catalog_db
from .adapters_http import DemandeActivationHttp, EligibiliteDroneHttp, EtatServicesHttp
from .application import GestionCommandes, Relance, VueCommande
from .domain import (CleIdempotenceReutilisee, Commande, CommandeIntrouvable, CommandeInvalide, DroneIntrouvable,
                     LigneDemandee, TypeServiceInconnu)

DRONE_URL = os.environ.get("DRONE_URL", "http://drone-management-service:8000")
SLICE_URL = os.environ.get("SLICE_URL", "http://slice-management-service:8000")

gestion: GestionCommandes
catalogue: catalog.Catalogue


@asynccontextmanager
async def lifespan(_):
    global gestion, catalogue
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=2, max_size=int(os.environ.get("DB_POOL", "20")))
    async with pool.acquire() as c, c.transaction():
        await c.execute("SELECT pg_advisory_xact_lock(1)")
        await c.execute(catalog_db.SCHEMA + adapters_db.SCHEMA)
    catalogue = catalog.Catalogue(await catalog_db.charger(pool))
    depot = adapters_db.DepotCommandesPostgres(pool)
    drones = creer_client(DRONE_URL, float(os.environ.get("D1_DELAI_S", "1")))
    slices_lecture = creer_client(SLICE_URL, float(os.environ.get("D3_DELAI_S", "1")))
    slices_activation = creer_client(SLICE_URL, float(os.environ.get("D2_DELAI_S", "2")))
    reveil = asyncio.Event()
    gestion = GestionCommandes(depot, catalogue, EligibiliteDroneHttp(drones), EtatServicesHttp(slices_lecture), reveil)
    relance = Relance(depot, DemandeActivationHttp(slices_activation, Disjoncteur("D2")), reveil)
    tache = asyncio.create_task(relance.tourner())
    yield
    tache.cancel()
    for c in (drones, slices_lecture, slices_activation):
        await c.aclose()
    await pool.close()


app = creer_app("service-ordering-service", lifespan)
publique = APIRouter(prefix="/api/service-ordering/v1")


class LigneEntrante(BaseModel):
    droneId: str = Field(min_length=1, max_length=64)
    serviceType: str = Field(min_length=1, max_length=32)


class CommandeEntrante(BaseModel):
    items: list[LigneEntrante] = Field(min_length=1, max_length=20)


def _commande_dto(c: Commande) -> dict:
    return {
        "orderId": c.order_id,
        "state": "ACKNOWLEDGED",
        "acceptedAt": c.accepted_at,
        "items": [{"orderItemId": e.order_item_id, "droneId": e.drone_id, "serviceType": e.service_type,
                   "state": "PENDING"} for e in c.elements],
    }


def _vue_dto(v: VueCommande) -> dict:
    items = []
    for ve in v.elements:
        s = ve.service or {}
        items.append({"orderItemId": ve.element.order_item_id, "droneId": ve.element.drone_id,
                      "serviceType": ve.element.service_type, "state": ve.state,
                      "serviceId": s.get("serviceId"), "failureCause": s.get("failureCause"),
                      "activatedAt": s.get("activatedAt")})
    return {"orderId": v.commande.order_id, "state": v.state, "acceptedAt": v.commande.accepted_at, "items": items}


@publique.get("/catalog")
async def lister_catalogue(_: str = Depends(client_courant)):
    return [{"serviceType": s.service_type, "name": s.name, "characteristics": s.characteristics}
            for s in catalogue.lister()]


@publique.post("/serviceOrder", status_code=201)
async def commander(corps: CommandeEntrante, response: Response,
                    idempotency_key: str | None = Header(default=None, max_length=128),
                    client_id: str = Depends(client_courant)):
    if not idempotency_key:
        raise ErreurApi(400, "CLE_IDEMPOTENCE_MANQUANTE", "En-tête Idempotency-Key obligatoire.")
    lignes = [LigneDemandee(l.droneId, l.serviceType) for l in corps.items]
    try:
        commande, creee = await gestion.commander(client_id, idempotency_key, lignes)
    except CommandeInvalide as e:
        raise ErreurApi(400, "DONNEES_INVALIDES", str(e))
    except TypeServiceInconnu as e:
        raise ErreurApi(422, "TYPE_SERVICE_INCONNU", f"Service hors catalogue : {e}.")
    except DroneIntrouvable as e:
        raise ErreurApi(422, "DRONE_INTROUVABLE", f"Drone introuvable : {e}.")
    except CleIdempotenceReutilisee:
        raise ErreurApi(422, "CLE_IDEMPOTENCE_REUTILISEE", "Cette Idempotency-Key a déjà servi pour une autre commande.")
    except DependanceIndisponible:
        raise ErreurApi(503, "DEPENDANCE_INDISPONIBLE", "Vérification du drone impossible; réessayez avec la même clé.")
    if not creee:
        response.status_code = 200
        return _vue_dto(await gestion.consulter(client_id, commande.order_id))
    return _commande_dto(commande)


@publique.get("/serviceOrder")
async def lister(limit: int = Query(100, ge=1, le=1000), client_id: str = Depends(client_courant)):
    return [{"orderId": c.order_id, "acceptedAt": c.accepted_at} for c in await gestion.lister(client_id, limit)]


@publique.get("/serviceOrder/{order_id}")
async def consulter(order_id: str, client_id: str = Depends(client_courant)):
    try:
        return _vue_dto(await gestion.consulter(client_id, order_id))
    except CommandeIntrouvable:
        raise ErreurApi(404, "INTROUVABLE", "Commande introuvable.")


@app.get("/health")
async def sante():
    return {"status": "UP"}


app.include_router(publique)
