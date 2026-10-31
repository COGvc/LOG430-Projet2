"""Module catalogue : spécifications de service et leurs caractéristiques (données, pas code).

Le module commande n'utilise que la classe Catalogue (interface publique du module), jamais la table.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceSpecification:
    service_type: str
    name: str
    characteristics: dict


class Catalogue:
    """Chargé une fois au démarrage : le catalogue change rarement et la commande le consulte à chaque requête."""

    def __init__(self, specs: list[ServiceSpecification]):
        self._specs = {s.service_type: s for s in specs}

    def lister(self) -> list[ServiceSpecification]:
        return list(self._specs.values())

    def specification(self, service_type: str) -> ServiceSpecification | None:
        return self._specs.get(service_type)
