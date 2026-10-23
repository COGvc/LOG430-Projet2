// Test de charge via la passerelle.
// Le jeton est obtenu UNE SEULE FOIS dans setup() : sinon on mesure Keycloak, pas CanDroneX.
//   k6 run -e CLIENT_SECRET=$INSPECTRA_DEMO_SECRET load-tests/drones.js
import http from 'k6/http';
import { check } from 'k6';

const KC = __ENV.KC || 'http://localhost:8081';
const GW = __ENV.GW || `http://localhost:${__ENV.GATEWAY_PORT || 8080}`;
const CLIENT_ID = __ENV.CLIENT_ID || 'inspectra-demo';

export const options = {
  scenarios: {
    lecture: { executor: 'constant-arrival-rate', rate: Number(__ENV.RATE || 200), timeUnit: '1s',
               duration: __ENV.DURATION || '30s', preAllocatedVUs: 50, maxVUs: 200 },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<500'],
  },
};

export function setup() {
  const r = http.post(`${KC}/realms/candronex/protocol/openid-connect/token`, {
    grant_type: 'client_credentials', client_id: CLIENT_ID, client_secret: __ENV.CLIENT_SECRET,
  });
  if (r.status !== 200) throw new Error(`jeton refuse par Keycloak : ${r.status} ${r.body}`);
  const { access_token, expires_in } = r.json();
  // Jeton de 5 min : la duree du test doit rester en deca, sinon les requetes passent a 401.
  console.log(`jeton obtenu pour ${CLIENT_ID}, valide ${expires_in}s`);
  return { auth: { headers: { Authorization: `Bearer ${access_token}` } } };
}

export default function ({ auth }) {
  const r = http.get(`${GW}/api/drone-management/v1/drone`, auth);
  check(r, { 'GET /api/drone-management/v1/drone -> 200': (x) => x.status === 200 });
}
