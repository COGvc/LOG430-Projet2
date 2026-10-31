-- Délai d'activation de bout en bout : 95e centile de activatedAt − acceptedAt sur la fenêtre de test.
-- Exécutée sur slice-db par load/mesurer.sh, avec trois variables psql :
--   :'debut', :'fin'  bornes de la fenêtre (horodatages UTC)
--   :total            nombre d'éléments commandés pendant la fenêtre, compté dans ordering-db
--                     (un élément jamais transmis à slice-management-service n'existe que là)
-- Règle de l'énoncé : un service qui n'est pas actif à la fin du test compte comme dépassant 5 s; il n'est pas
-- exclu. On classe donc les délais des services actifs à temps et on prend le rang ceil(0,95 × total) :
-- s'il n'existe pas (moins de 95 % des services actifs à temps), le P95 dépasse la fin du test.
WITH actifs AS (
    SELECT extract(epoch FROM activated_at - accepted_at) AS delai_s
    FROM service
    WHERE accepted_at >= :'debut'::timestamptz AND accepted_at < :'fin'::timestamptz
      AND state = 'ACTIVE' AND activated_at <= :'fin'::timestamptz
), rangs AS (
    SELECT delai_s, row_number() OVER (ORDER BY delai_s) AS rang FROM actifs
)
SELECT :total                                                            AS commandes_total,
       (SELECT count(*) FROM actifs)                                     AS actifs_a_temps,
       (SELECT count(*) FROM service
         WHERE accepted_at >= :'debut'::timestamptz AND accepted_at < :'fin'::timestamptz
           AND state = 'FAILED')                                         AS en_echec,
       (SELECT round(delai_s::numeric, 3) FROM rangs WHERE rang = ceil(0.50 * :total)) AS p50_s,
       (SELECT round(delai_s::numeric, 3) FROM rangs WHERE rang = ceil(0.95 * :total)) AS p95_s,
       (SELECT round(delai_s::numeric, 3) FROM rangs WHERE rang = ceil(0.99 * :total)) AS p99_s,
       (SELECT round(max(delai_s)::numeric, 3) FROM actifs)               AS max_s;
