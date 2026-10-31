"""Format d'erreur uniforme : {"erreur", "message", "correlationId"} (conception, étape 6 §4)."""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .contexte import correlation_id
from .journal import evenement


class ErreurApi(Exception):
    def __init__(self, statut: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.statut, self.code, self.message = statut, code, message


def reponse(statut: int, code: str, message: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=statut,
        content={"erreur": code, "message": message, "correlationId": correlation_id.get()},
    )


_CODES_HTTP = {404: "INTROUVABLE", 405: "METHODE_NON_PERMISE"}


def installer(app: FastAPI) -> None:
    @app.exception_handler(ErreurApi)
    async def _metier(_: Request, e: ErreurApi):
        return reponse(e.statut, e.code, e.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        details = "; ".join(f"{'.'.join(map(str, err['loc'][1:]))}: {err['msg']}" for err in e.errors())
        return reponse(400, "DONNEES_INVALIDES", details)

    @app.exception_handler(HTTPException)
    async def _http(_: Request, e: HTTPException):
        return reponse(e.status_code, _CODES_HTTP.get(e.status_code, "ERREUR_HTTP"), str(e.detail))

    @app.exception_handler(Exception)
    async def _imprevue(_: Request, e: Exception):
        evenement("erreur imprévue", logging.ERROR, type=type(e).__name__)
        return reponse(500, "ERREUR_INTERNE", "Erreur interne.")
