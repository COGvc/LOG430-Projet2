"""Module catalogue : spécifications de service et leurs caractéristiques (données, pas code).

Le module commande n'utilise que la classe Catalogue (interface publique du module), jamais la table.
"""
import json
from dataclasses import dataclass

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_specification (
    service_type    text  PRIMARY KEY,
    name            text  NOT NULL,
    characteristics jsonb NOT NULL
);
INSERT INTO catalog_specification VALUES
  ('C2', 'Commande et contrôle (URLLC)',
   '{"sst": 2, "sd": "000001", "dnn": "c2", "fiveQi": 7, "arp": 2, "ambrUplink": "20 Mbps", "ambrDownlink": "20 Mbps"}'),
  ('IMAGERY', 'Imagerie (eMBB)',
   '{"sst": 1, "sd": "000002", "dnn": "imagery", "fiveQi": 9, "arp": 8, "ambrUplink": "100 Mbps", "ambrDownlink": "500 Mbps"}')
ON CONFLICT (service_type) DO NOTHING;
"""


@dataclass(frozen=True)
class ServiceSpecification:
    service_type: str
    name: str
    characteristics: dict


class Catalogue:
    """Lu une fois au démarrage : le catalogue change rarement et la commande le consulte à chaque requête."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._specs: dict[str, ServiceSpecification] = {}

    async def charger(self) -> None:
        rows = await self.pool.fetch("SELECT * FROM catalog_specification ORDER BY service_type")
        self._specs = {r["service_type"]: ServiceSpecification(r["service_type"], r["name"], json.loads(r["characteristics"]))
                       for r in rows}

    def lister(self) -> list[ServiceSpecification]:
        return list(self._specs.values())

    def specification(self, service_type: str) -> ServiceSpecification | None:
        return self._specs.get(service_type)
