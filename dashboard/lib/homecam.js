// Shared helpers for the private home-camera page (/home.html).
// Needs Vercel env vars: SUPABASE_URL, SUPABASE_SERVICE_KEY (secret), HOMECAM_PASSWORD.
import crypto from "node:crypto";

const URL_ = () => (process.env.SUPABASE_URL || "").replace(/\/$/, "");
const KEY = () => process.env.SUPABASE_SERVICE_KEY || "";
export const BUCKET = "homecam";
const COOKIE = "hc";
const DAYS = 30;

export function configured() {
  return Boolean(URL_() && KEY() && process.env.HOMECAM_PASSWORD);
}

// ---- session cookie: "<expiry>.<hmac>", signed with the service key + password ----
const secret = () => crypto.createHash("sha256").update(KEY() + "|" + (process.env.HOMECAM_PASSWORD || "")).digest();
const sign = exp => crypto.createHmac("sha256", secret()).update("homecam:" + exp).digest("base64url");

export function passwordOk(pw) {
  const a = crypto.createHash("sha256").update(String(pw || "")).digest();
  const b = crypto.createHash("sha256").update(process.env.HOMECAM_PASSWORD || "").digest();
  return crypto.timingSafeEqual(a, b) && Boolean(process.env.HOMECAM_PASSWORD);
}

export function sessionCookie() {
  const exp = Math.floor(Date.now() / 1000) + DAYS * 86400;
  return `${COOKIE}=${exp}.${sign(exp)}; Path=/; Max-Age=${DAYS * 86400}; HttpOnly; Secure; SameSite=Lax`;
}
export const clearCookie = `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;

export function authed(req) {
  const m = (req.headers.cookie || "").match(/(?:^|;\s*)hc=(\d+)\.([A-Za-z0-9_-]+)/);
  if (!m || +m[1] < Date.now() / 1000) return false;
  const good = Buffer.from(sign(m[1])), got = Buffer.from(m[2]);
  return good.length === got.length && crypto.timingSafeEqual(good, got);
}

// ---- Supabase ----
export async function supa(path, { method = "GET", body, prefer } = {}) {
  const r = await fetch(URL_() + path, {
    method,
    headers: { apikey: KEY(), Authorization: `Bearer ${KEY()}`, "Content-Type": "application/json", ...(prefer ? { Prefer: prefer } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!r.ok) throw new Error(`Supabase ${r.status}`);
  return r.status === 204 ? null : r.json();
}

// Signed, time-limited links so the browser loads pictures straight from private storage
export async function signMany(paths, expiresIn = 600) {
  if (!paths.length) return {};
  const out = await supa(`/storage/v1/object/sign/${BUCKET}`, { method: "POST", body: { expiresIn, paths } });
  const map = {};
  for (const x of out) if (x.signedURL) map[x.path] = URL_() + "/storage/v1" + x.signedURL;
  return map;
}

export function noStore(res) {
  res.setHeader("Cache-Control", "private, no-store");
  res.setHeader("X-Robots-Tag", "noindex");
}
