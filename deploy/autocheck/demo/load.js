import { check } from 'k6';
import http from 'k6/http';

const token = open('/run/secrets/demo_load_token').trim();
const profile = __ENV.DEMO_LOAD_PROFILE || 'steady';
if (profile !== 'steady' && profile !== 'ramp') {
  throw new Error('DEMO_LOAD_PROFILE must be steady or ramp');
}

const ramp = profile === 'ramp';
const rate = Number(__ENV.DEMO_LOAD_RATE || (ramp ? 60 : 15));
const duration = __ENV.DEMO_LOAD_DURATION || (ramp ? '30s' : '45s');
if (!Number.isSafeInteger(rate) || rate < 1) {
  throw new Error('DEMO_LOAD_RATE must be a positive integer');
}

const capacity = ramp
  ? {
      executor: 'ramping-arrival-rate',
      startRate: Math.ceil(rate / 12),
      timeUnit: '1s',
      preAllocatedVUs: 16,
      maxVUs: 64,
      stages: [
        { duration, target: Math.ceil(rate / 3) },
        { duration, target: Math.ceil(2 * rate / 3) },
        { duration, target: rate },
        { duration, target: rate },
        { duration, target: 0 },
      ],
    }
  : {
      executor: 'constant-arrival-rate',
      rate,
      timeUnit: '1s',
      duration,
      preAllocatedVUs: 8,
      maxVUs: 24,
    };

export const options = {
  scenarios: { capacity },
  thresholds: {
    checks: ['rate>=0.99'],
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1000'],
    dropped_iterations: ['count==0'],
  },
};

export default function () {
  const response = http.get('http://gateway:8080/v1/capacity', {
    headers: { Authorization: `Bearer ${token}` },
    redirects: 0,
    timeout: '3s',
  });
  check(response, { 'capacity responds with HTTP 200': (r) => r.status === 200 });
}
