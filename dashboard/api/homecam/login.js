import { configured, passwordOk, sessionCookie, clearCookie, noStore } from "../../lib/homecam.js";

export default async function handler(req, res) {
  noStore(res);
  if (!configured()) return res.status(503).json({ error: "Not set up yet: add SUPABASE_SERVICE_KEY and HOMECAM_PASSWORD in Vercel." });
  if (req.method === "DELETE") {
    res.setHeader("Set-Cookie", clearCookie);
    return res.status(200).json({ ok: true });
  }
  if (req.method !== "POST") return res.status(405).end();
  const body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : (req.body || {});
  if (!passwordOk(body.password)) {
    await new Promise(r => setTimeout(r, 800));   // slow down guessing
    return res.status(401).json({ error: "Wrong password" });
  }
  res.setHeader("Set-Cookie", sessionCookie());
  res.status(200).json({ ok: true });
}
