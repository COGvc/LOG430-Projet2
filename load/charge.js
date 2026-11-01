// Test de charge CanDroneX Phase 2 : taux d'arrivée constant, par la passerelle.
// 1 min de montée jusqu'à DEBIT ops/s, puis PLATEAU à DEBIT ops/s (énoncé : 1 min + 5 min à 600 ops/s).
// Mélange : 60 % consultations, 30 % commandes, 10 % enregistrements de drones.
// Lancé par load/mesurer.sh (conteneur grafana/k6 sur le réseau backend de Docker Compose).
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';

const GW = __ENV.GW || 'http://api-gateway:8080';
const TOKEN_URL = `${__ENV.KC || 'http://keycloak:8080'}/realms/candronex/protocol/openid-connect/token`;
const DEBIT = Number(__ENV.DEBIT || 600);
const MONTEE = __ENV.MONTEE || '1m';
const PLATEAU = __ENV.PLATEAU || '5m';
const DRONES_PAR_CLIENT = 50;
const JSON_H = { 'Content-Type': 'application/json' };

export const options = {
  scenarios: {
    montee: {
      executor: 'ramping-arrival-rate', startRate: 10, timeUnit: '1s',
      stages: [{ duration: MONTEE, target: DEBIT }], preAllocatedVUs: 200, maxVUs: 3000,
    },
    plateau: {
      executor: 'constant-arrival-rate', rate: DEBIT, timeUnit: '1s', duration: PLATEAU, startTime: MONTEE,
      preAllocatedVUs: 400, maxVUs: 3000,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],        // taux d'erreur < 1 %
    dropped_iterations: ['count==0'],     // aucune itération abandonnée
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

const pad = (n, l) => String(n).padStart(l, '0');

const CLIENTS = [
  { id: 'inspectra-demo', secret: __ENV.INSPECTRA_DEMO_SECRET },
  { id: 'aerotech-demo', secret: __ENV.AEROTECH_DEMO_SECRET },
];

// Jeton client credentials auprès de Keycloak (pas la passerelle), étiqueté à part : ce n'est pas une opération.
// `renouveler` : instant (ms) où le client en redemande un, entre 60 s et 30 s avant l'expiration
// (aléatoire pour étaler les demandes des VU).
function jeton(client) {
  const r = http.post(TOKEN_URL, { grant_type: 'client_credentials', client_id: client.id, client_secret: client.secret },
    { tags: { op: 'jeton' } });
  if (r.status !== 200) throw new Error(`jeton ${client.id} : ${r.status}`);
  const marge = 30 + 30 * Math.random();
  return { auth: r.json('access_token'), renouveler: Date.now() + (r.json('expires_in') - marge) * 1000 };
}

// Comme un vrai client B2B, chaque VU renouvelle son jeton avant l'expiration (durée fixée par le realm : 300 s).
const jetons = {};
function jetonValide(data, i) {
  if (!jetons[i]) { // jeton de setup, échéance décalée par VU (jusqu'à 30 s plus tôt)
    const j = data.clients[i].jeton;
    jetons[i] = { auth: j.auth, renouveler: j.renouveler - 30000 * Math.random() };
  }
  if (Date.now() >= jetons[i].renouveler) jetons[i] = jeton(CLIENTS[i]);
  return jetons[i].auth;
}

// Identifiants uniques par exécution : le test peut être relancé sans vider les bases.
function identite(run, scenario, n) {
  return {
    imsi: `9${pad(run % 10000, 4)}${scenario}${pad(n, 9)}`,    // 15 chiffres
    iccid: `8930${pad(run % 10000, 4)}${scenario}${pad(n, 10)}`, // 19 chiffres
  };
}

function enregistrer(auth, droneId, id) {
  return http.post(`${GW}/api/drone-management/v1/drone`,
    JSON.stringify({ droneId, imsi: id.imsi, iccid: id.iccid, simType: 'ESIM' }),
    { headers: { ...JSON_H, Authorization: `Bearer ${auth}` }, tags: { op: 'drone' } });
}

export function setup() {
  const run = Math.floor(Date.now() / 1000);
  const clients = CLIENTS.map((c) => ({ jeton: jeton(c), drones: [] }));
  // Flotte initiale : les consultations et commandes visent des drones existants.
  clients.forEach((c, i) => {
    for (let n = 0; n < DRONES_PAR_CLIENT; n++) {
      const droneId = `F-${run}-${i}-${n}`; // F : flotte initiale (distincte des K- enregistrés pendant le test)
      const r = enregistrer(c.jeton.auth, droneId, identite(run, 0, i * DRONES_PAR_CLIENT + n));
      if (r.status === 201) c.drones.push(droneId);
    }
  });
  return { run, clients };
}

const commandes = []; // commandes créées par ce VU, pour les consulter

export default function (data) {
  const i = exec.vu.idInTest % 2;
  const c = data.clients[i];
  const jetonVu = jetonValide(data, i);
  const auth = { Authorization: `Bearer ${jetonVu}` };
  const drone = c.drones[Math.floor(Math.random() * c.drones.length)];
  const tirage = Math.random();

  if (tirage < 0.6) { // consultation : drone, commande ou état des services d'un drone
    const choix = Math.floor(Math.random() * 3);
    let r;
    if (choix === 1 && commandes.length) {
      r = http.get(`${GW}/api/service-ordering/v1/serviceOrder/${commandes[Math.floor(Math.random() * commandes.length)]}`,
        { headers: auth, tags: { op: 'consultation', name: 'GET serviceOrder/{id}' } });
    } else if (choix === 2) {
      r = http.get(`${GW}/api/slice-management/v1/service?droneId=${drone}`,
        { headers: auth, tags: { op: 'consultation', name: 'GET service?droneId' } });
    } else {
      r = http.get(`${GW}/api/drone-management/v1/drone/${drone}`,
        { headers: auth, tags: { op: 'consultation', name: 'GET drone/{id}' } });
    }
    check(r, { 'consultation 200': (x) => x.status === 200 });
  } else if (tirage < 0.9) { // commande C2 + IMAGERY
    const r = http.post(`${GW}/api/service-ordering/v1/serviceOrder`,
      JSON.stringify({ items: [{ droneId: drone, serviceType: 'C2' }, { droneId: drone, serviceType: 'IMAGERY' }] }),
      { headers: { ...JSON_H, ...auth, 'Idempotency-Key': `k6-${data.run}-${exec.scenario.name}-${exec.scenario.iterationInTest}` },
        tags: { op: 'commande' } });
    if (check(r, { 'commande 201': (x) => x.status === 201 })) {
      commandes.push(r.json('orderId'));
      if (commandes.length > 50) commandes.shift();
    }
  } else { // enregistrement d'un drone
    const s = exec.scenario.name === 'montee' ? 1 : 2;
    const n = exec.scenario.iterationInTest;
    const r = enregistrer(jetonVu, `K-${data.run}-${s}-${n}`, identite(data.run, s, n));
    check(r, { 'drone 201': (x) => x.status === 201 });
  }
}

export function handleSummary(data) {
  const m = data.metrics;
  const v = (nom, cle) => (m[nom] && m[nom].values[cle] !== undefined ? m[nom].values[cle] : 0);
  const texte = [
    `itérations       : ${v('iterations', 'count')} (${v('iterations', 'rate').toFixed(1)}/s)`,
    `abandonnées      : ${v('dropped_iterations', 'count')}`,
    `requêtes HTTP    : ${v('http_reqs', 'count')} (${v('http_reqs', 'rate').toFixed(1)}/s)`,
    `taux d'erreur    : ${(100 * v('http_req_failed', 'rate')).toFixed(3)} %`,
    `durée HTTP (ms)  : méd ${v('http_req_duration', 'med').toFixed(1)}, p95 ${v('http_req_duration', 'p(95)').toFixed(1)}, max ${v('http_req_duration', 'max').toFixed(1)}`,
    `seuils           : ${Object.entries(data.metrics).filter(([, x]) => x.thresholds)
      .map(([n, x]) => `${n} ${Object.values(x.thresholds).every((t) => t.ok) ? 'OK' : 'ÉCHEC'}`).join(', ')}`,
  ].join('\n');
  return { stdout: `\n${texte}\n`, [`/resultats/${__ENV.NOM || 'k6'}.json`]: JSON.stringify(data, null, 2) };
}
