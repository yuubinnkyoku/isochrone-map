#!/usr/bin/env node
// Verify Issue #4 invariants against the actual browser config.
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../js/config.js', import.meta.url), 'utf8');
const sandbox = { console, Math };
sandbox.window = sandbox;
vm.runInNewContext(source, sandbox, { filename: 'js/config.js' });
const stops = sandbox.CONFIG.colorStops.map(s => [s.min, ...s.oklch]);

function lab([, L, C, h]) {
  const r = h * Math.PI / 180;
  return [L, C * Math.cos(r), C * Math.sin(r)];
}
function deltaE(a, b) {
  return Math.hypot(a[0]-b[0], a[1]-b[1], a[2]-b[2]);
}
function linearRgb(L, C, h) {
  const r = h * Math.PI / 180, a = C*Math.cos(r), b = C*Math.sin(r);
  const lp=L+0.3963377774*a+0.2158037573*b;
  const mp=L-0.1055613458*a-0.0638541728*b;
  const sp=L-0.0894841775*a-1.2914855480*b;
  const l=lp**3, m=mp**3, s=sp**3;
  return [
    4.0767416621*l-3.3077115913*m+0.2309699292*s,
    -1.2684380046*l+2.6097574011*m-0.3413193965*s,
    -0.0041960863*l-0.7034186147*m+1.7076147010*s,
  ];
}
function hueDelta(h0,h1) {
  return ((h1-h0+540)%360)-180;
}

if (stops.some(s => !Number.isFinite(s[1]) || !Number.isFinite(s[2]) || !Number.isFinite(s[3]))) {
  throw new Error('all color stops must be OKLCH values');
}
for (let i=1;i<stops.length;i++) {
  if (!(stops[i][1] > stops[i-1][1])) throw new Error(`L is not monotonic at ${stops[i][0]}`);
}

const tenMinute=[];
for (let i=0;i<stops.length-1;i++) {
  const dt=stops[i+1][0]-stops[i][0];
  if (dt===10) tenMinute.push(deltaE(lab(stops[i]),lab(stops[i+1])));
}
const min=Math.min(...tenMinute), max=Math.max(...tenMinute);
const mean=tenMinute.reduce((a,b)=>a+b,0)/tenMinute.length;
if (max-min > 0.00001) throw new Error(`10-minute ΔE spread too large: ${min}..${max}`);

const lastA=stops.at(-2), lastB=stops.at(-1);
const half=deltaE(lab(lastA),lab(lastB));
if (lastB[0]-lastA[0]!==5 || Math.abs(half/mean-0.5)>0.01) {
  throw new Error(`5-minute final step is not approximately half ΔE: ${half}`);
}

// Check the exact piecewise-OKLCH interpolation used by minutesToColor.
for (let i=0;i<stops.length-1;i++) {
  const a=stops[i], b=stops[i+1], dh=hueDelta(a[3],b[3]);
  for (let j=0;j<=100;j++) {
    const t=j/100;
    const L=a[1]+(b[1]-a[1])*t;
    const C=a[2]+(b[2]-a[2])*t;
    const h=a[3]+dh*t;
    const rgb=linearRgb(L,C,h);
    if (rgb.some(v => v < -1e-6 || v > 1+1e-6)) {
      throw new Error(`out of sRGB gamut near ${a[0]} at t=${t}`);
    }
  }
}

console.log(`OK: ${stops.length} OKLCH stops`);
console.log(`OK: L ${stops[0][1].toFixed(3)} -> ${stops.at(-1)[1].toFixed(3)} (strictly monotonic)`);
console.log(`OK: 10-minute ΔE mean=${mean.toFixed(6)} range=${min.toFixed(6)}..${max.toFixed(6)}`);
console.log(`OK: 5-minute ΔE=${half.toFixed(6)} (${(half/mean).toFixed(3)}x)`);
console.log('OK: all interpolated OKLCH colors are inside sRGB gamut');
