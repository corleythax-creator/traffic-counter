// Per-camera detail (down to the minute) for camera.html. Calls a read-only
// Supabase function with the public (publishable) key.
const WINDOWS = new Set(["1h", "6h", "24h", "7d"]);

export default async function handler(req, res) {
  const cam = String(req.query.cam || "");
  if (!/^[a-z0-9-]{1,40}$/.test(cam)) {
    res.status(400).json({ error: "Unknown camera" });
    return;
  }
  const w = WINDOWS.has(req.query.w) ? req.query.w : "1h";
  try {
    const r = await fetch(`${process.env.SUPABASE_URL}/rest/v1/rpc/traffic_camera`, {
      method: "POST",
      headers: { apikey: process.env.SUPABASE_PUBLISHABLE_KEY, "Content-Type": "application/json" },
      body: JSON.stringify({ cam, win: w }),
    });
    if (!r.ok) {
      res.status(502).json({ error: `Supabase returned ${r.status}` });
      return;
    }
    res.setHeader("Cache-Control", "public, s-maxage=30, stale-while-revalidate=60");
    res.status(200).json(await r.json());
  } catch (e) {
    res.status(500).json({ error: "Could not reach the database" });
  }
}
