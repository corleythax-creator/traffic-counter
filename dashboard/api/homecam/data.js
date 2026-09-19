// Latest picture link + camera status, and (with ?events=1) the recent motion events.
import { configured, authed, supa, signMany, noStore } from "../../lib/homecam.js";

export default async function handler(req, res) {
  noStore(res);
  if (!configured()) return res.status(503).json({ error: "Not set up yet: add SUPABASE_SERVICE_KEY and HOMECAM_PASSWORD in Vercel." });
  if (!authed(req)) return res.status(401).json({ error: "Please sign in" });
  const cam = /^[a-z0-9-]{1,40}$/.test(req.query.cam || "") ? req.query.cam : "home";
  try {
    const [status] = await supa(`/rest/v1/homecam_status?camera=eq.${cam}&select=*`);
    const out = { now: new Date().toISOString(), status: status || null };
    const paths = [`latest/${cam}.jpg`];
    let events = [];
    if (req.query.events) {
      events = await supa(`/rest/v1/homecam_events?camera=eq.${cam}&select=*&order=start_at.desc&limit=60`);
      paths.push(...events.map(e => `events/${e.id}/thumb.jpg`));
    }
    const signed = await signMany(paths, 900);
    out.latest = status ? signed[`latest/${cam}.jpg`] || null : null;
    if (req.query.events) out.events = events.map(e => ({ ...e, thumb: signed[`events/${e.id}/thumb.jpg`] || null }));
    res.status(200).json(out);
  } catch (e) {
    res.status(502).json({ error: "Could not reach storage" });
  }
}
