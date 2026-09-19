// Signed links to every picture in one motion event, for playback.
import { configured, authed, supa, signMany, noStore } from "../../lib/homecam.js";

export default async function handler(req, res) {
  noStore(res);
  if (!configured()) return res.status(503).json({ error: "Not set up yet" });
  if (!authed(req)) return res.status(401).json({ error: "Please sign in" });
  const id = String(req.query.id || "");
  if (!/^[a-z0-9-]{1,40}\/\d{4}-\d{2}-\d{2}\/\d{6}$/.test(id)) return res.status(400).json({ error: "Bad event id" });
  try {
    const [ev] = await supa(`/rest/v1/homecam_events?id=eq.${encodeURIComponent(id)}&select=*`);
    if (!ev) return res.status(404).json({ error: "Event not found" });
    const paths = Array.from({ length: ev.frames }, (_, i) => `events/${id}/${String(i + 1).padStart(5, "0")}.jpg`);
    const signed = await signMany(paths, 3600);
    res.status(200).json({ event: ev, frames: paths.map(p => signed[p]).filter(Boolean) });
  } catch (e) {
    res.status(502).json({ error: "Could not reach storage" });
  }
}
