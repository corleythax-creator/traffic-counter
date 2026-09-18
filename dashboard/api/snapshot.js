// Latest camera snapshot for the dashboard thumbnails, proxied from MDOT
// (Mississippi Department of Transportation) so browsers load it from this site.
const CAMS = {
  "lakeland-treetops":    ["streamingjxn2", "011404"],
  "lakeland-n-airport":   ["streamingjxn2", "010102"],
  "jackson-e-fraternity": ["streamingjxn4", "060106"],
  "jackson-w-fraternity": ["streamingjxn4", "060105"],
  "university-w-lamar":   ["streamingjxn4", "060204"],
  "lamar-n-university":   ["streamingjxn4", "060202"],
  "university-e-ms7":     ["streamingjxn4", "060205"],
};

export default async function handler(req, res) {
  const cam = CAMS[String(req.query.cam || "")];
  if (!cam) {
    res.status(404).end();
    return;
  }
  const [host, stream] = cam;
  const url = `https://${host}.mdottraffic.com/thumbnail?application=rtplive&streamname=${stream}.stream` +
              `&size=352x240&format=jpg&fitmode=stretch&t=${Date.now()}`;
  try {
    const r = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0" } });
    const buf = Buffer.from(await r.arrayBuffer());
    if (!r.ok || buf.length < 1000) {
      res.status(502).end();
      return;
    }
    // Share one fetch per camera per 15 seconds across all viewers
    res.setHeader("Cache-Control", "public, s-maxage=15, stale-while-revalidate=30");
    res.setHeader("Content-Type", "image/jpeg");
    res.status(200).send(buf);
  } catch (e) {
    res.status(502).end();
  }
}
