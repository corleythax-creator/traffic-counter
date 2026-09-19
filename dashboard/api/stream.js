// HLS (HTTP Live Streaming) proxy for the live-video camera, so the dashboard can
// play it in any browser.
//
// Why proxy at all: hls.js fetches the playlist and every segment with XHR, which
// needs CORS (cross-origin resource sharing) headers from the origin server. MDOT
// (Mississippi Department of Transportation) serves the stream for its own map and
// sends none, so Chrome and Firefox refuse it. Serving it from this site makes it
// same-origin and the question disappears. Safari would play the MDOT URL directly,
// but it goes through here too so there is one code path.
//
// This is deliberately NOT a general proxy: the camera must be in CAMS below, and a
// requested path must be a single bare filename inside that camera's own stream
// directory. Nothing else can be fetched through it.
const CAMS = {
  "university-e-ms7": ["streamingjxn4", "060205"],
};

const NAME = /^[A-Za-z0-9._-]{1,120}$/;          // one filename, no slashes, no ".."
const QUERY = /^[A-Za-z0-9._=&%+-]{0,200}$/;      // Wowza sometimes appends a session id

const TYPES = [
  [/\.m3u8$/i, "application/vnd.apple.mpegurl"],
  [/\.ts$/i, "video/mp2t"],
  [/\.(m4s|mp4)$/i, "video/mp4"],
  [/\.aac$/i, "audio/aac"],
];
const typeFor = name => (TYPES.find(([re]) => re.test(name)) || [, "application/octet-stream"])[1];

const base = (host, stream) => `https://${host}.mdottraffic.com/rtplive/${stream}.stream/`;

// Turn one URI from a playlist into a link back through this endpoint. Anything that
// does not resolve to a plain filename under this camera's directory is left alone
// rather than proxied, so a rewritten playlist can never point somewhere else.
function proxyUri(uri, cam, baseUrl) {
  let resolved;
  try { resolved = new URL(uri, baseUrl); } catch (_) { return null; }
  if (!resolved.href.startsWith(baseUrl)) return null;
  const rest = resolved.href.slice(baseUrl.length);
  const [name, query = ""] = rest.split("?");
  if (!NAME.test(name) || !QUERY.test(query)) return null;
  return `/api/stream?cam=${encodeURIComponent(cam)}&path=${encodeURIComponent(name + (query ? "?" + query : ""))}`;
}

// Rewrite every URI in a playlist (segments, nested variant playlists, and the URI=""
// attributes on EXT-X-KEY / EXT-X-MAP) to come back through this endpoint.
export function rewritePlaylist(text, cam, baseUrl) {
  return text.split(/\r?\n/).map(line => {
    const t = line.trim();
    if (!t) return line;
    if (t.startsWith("#")) {
      return line.replace(/URI="([^"]*)"/g, (whole, uri) => {
        const p = proxyUri(uri, cam, baseUrl);
        return p ? `URI="${p}"` : whole;
      });
    }
    return proxyUri(t, cam, baseUrl) || line;
  }).join("\n");
}

export default async function handler(req, res) {
  const camId = String(req.query.cam || "");
  const cam = CAMS[camId];
  if (!cam) { res.status(404).end(); return; }
  const [host, stream] = cam;
  const baseUrl = base(host, stream);

  const raw = req.query.path == null ? "" : String(req.query.path);
  const [name, query = ""] = raw.split("?");
  if (raw && (!NAME.test(name) || !QUERY.test(query))) { res.status(400).end(); return; }
  const target = raw ? baseUrl + name + (query ? "?" + query : "") : baseUrl + "playlist.m3u8";
  const asPlaylist = !raw || /\.m3u8$/i.test(name);

  try {
    const r = await fetch(target, {
      headers: { "User-Agent": "Mozilla/5.0", Referer: `https://${host}.mdottraffic.com/` },
    });
    if (!r.ok) { res.status(502).end(); return; }

    if (asPlaylist) {
      const text = await r.text();
      if (!text.includes("#EXTM3U")) { res.status(502).end(); return; }
      // A live playlist is rewritten every few seconds; caching it would freeze playback
      res.setHeader("Cache-Control", "no-store");
      res.setHeader("Content-Type", "application/vnd.apple.mpegurl");
      res.status(200).send(rewritePlaylist(text, camId, baseUrl));
      return;
    }
    // Segments never change once published, so they are safe to cache briefly
    res.setHeader("Cache-Control", "public, max-age=30");
    res.setHeader("Content-Type", typeFor(name));
    res.status(200).send(Buffer.from(await r.arrayBuffer()));
  } catch (e) {
    res.status(502).end();
  }
}
