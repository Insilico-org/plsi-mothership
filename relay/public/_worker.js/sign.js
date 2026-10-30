// Encoding, HMAC and constant-time comparison for codes, tokens and hashed keys.

export const enc = new TextEncoder();
export const now = () => Math.floor(Date.now() / 1000);

export function same(a, b) {
  const x = enc.encode(String(a));
  const y = enc.encode(String(b));
  if (x.byteLength !== y.byteLength) return false;
  let out = 0;
  for (let i = 0; i < x.byteLength; i++) out |= x[i] ^ y[i];
  return out === 0;
}

export function b64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function unb64url(s) {
  const bin = atob(s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (s.length % 4)) % 4));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

export const random = (n) => b64url(crypto.getRandomValues(new Uint8Array(n)));

async function mac(secret, msg) {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', key, enc.encode(msg)));
}

export const hmac = async (secret, msg) => b64url(await mac(secret, msg));

export async function sha256Hex(bytes) {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return [...digest].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// A version-4-shaped UUID derived from an HMAC: the same message always gives the same id.
export async function hmacUuid(secret, msg) {
  const b = (await mac(secret, msg)).slice(0, 16);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
