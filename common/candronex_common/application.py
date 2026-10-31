"""Création d'une application FastAPI avec les éléments communs (corrélation, erreurs, journaux)."""
from fastapi import FastAPI

from . import erreurs, journal
from .correlation import CorrelationMiddleware


def creer_app(service: str, lifespan) -> FastAPI:
    journal.configurer(service)
    app = FastAPI(title=service, lifespan=lifespan)
    app.add_middleware(CorrelationMiddleware)
    erreurs.installer(app)
    return app
