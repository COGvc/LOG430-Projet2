"""Adaptateur PostgreSQL du module catalogue : schéma, données initiales et chargement."""
import json

import asyncpg

from .catalog import ServiceSpecification

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


async def charger(pool: asyncpg.Pool) -> list[ServiceSpecification]:
    rows = await pool.fetch("SELECT * FROM catalog_specification ORDER BY service_type")
    return [ServiceSpecification(r["service_type"], r["name"], json.loads(r["characteristics"])) for r in rows]
