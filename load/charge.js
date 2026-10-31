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

function jeton(clientId, secret) {
  const r = http.post(TOKEN_URL, { grant_type: 'client_credentials', client_id: clientId, client_secret: secret });
  if (r.status !== 200) throw new Error(`jeton ${clientId} : ${r.status}`);
  return r.json('access_token');
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
  const clients = [
    { auth: jeton('inspectra-demo', __ENV.INSPECTRA_DEMO_SECRET), drones: [] },
    { auth: jeton('aerotech-demo', __ENV.AEROTECH_DEMO_SECRET), drones: [] },
  ];
  // Flotte initiale : les consultations et commandes visent des drones existants.
  clients.forEach((c, i) => {
    for (let n = 0; n < DRONES_PAR_CLIENT; n++) {
      const droneId = `F-${run}-${i}-${n}`; // F : flotte initiale (distincte des K- enregistrés pendant le test)
      const r = enregistrer(c.auth, droneId, identite(run, 0, i * DRONES_PAR_CLIENT + n));
      if (r.status === 201) c.drones.push(droneId);
    }
  });
  return { run, clients };
}

const commandes = []; // commandes créées par ce VU, pour les consulter

export default function (data) {
  const c = data.clients[exec.vu.idInTest % 2];
  const auth = { Authorization: `Bearer ${c.auth}` };
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
    const r = enregistrer(c.auth, `K-${data.run}-${s}-${n}`, identite(data.run, s, n));
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
