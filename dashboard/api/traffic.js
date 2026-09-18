// Returns aggregated traffic counts for the dashboard. Calls a read-only
// Supabase function with the public (publishable) key; raw tables stay private.
const WINDOWS = new Set(["6h", "24h", "7d"]);

export default async function handler(req, res) {
  const w = WINDOWS.has(req.query.w) ? req.query.w : "6h";
  try {
    const r = await fetch(`${process.env.SUPABASE_URL}/rest/v1/rpc/traffic_dashboard`, {
      method: "POST",
      headers: {
        apikey: process.env.SUPABASE_PUBLISHABLE_KEY,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ win: w }),
    });
    if (!r.ok) {
      res.status(502).json({ error: `Supabase returned ${r.status}` });
      return;
    }
    // Cache at Vercel's edge for a minute so many viewers share one database query
    res.setHeader("Cache-Control", "public, s-maxage=60, stale-while-revalidate=120");
    res.status(200).json(await r.json());
  } catch (e) {
    res.status(500).json({ error: "Could not reach the database" });
  }
}
