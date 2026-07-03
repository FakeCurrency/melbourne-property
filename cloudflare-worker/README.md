# AI Ask proxy (Cloudflare Worker)

The static site can't hold an API key, so AI answers go through this tiny
Worker. It receives the user's question plus the suburbs that already matched
their Ask filters, asks Claude (`claude-opus-4-8`) for a short grounded
recommendation, and returns plain text. The key never leaves Cloudflare.

**The site works fully without this** — the built-in Ask feature is pure
client-side. This adds an optional natural-language summary on top.

## Setup (one-time, ~10 minutes)

1. Create a free [Cloudflare account](https://dash.cloudflare.com/sign-up) and an
   [Anthropic API key](https://console.anthropic.com/).
2. ```bash
   cd cloudflare-worker
   npm install
   npx wrangler login
   npx wrangler secret put ANTHROPIC_API_KEY   # paste your key
   npx wrangler deploy                          # prints https://melb-property-ask.<you>.workers.dev
   ```
3. Wire the site to it:
   - in `public/js/app.js`, set `AI_ENDPOINT` to the printed URL;
   - in `public/index.html`, add that origin to the CSP `connect-src` list.
4. Push — the Ask modal now shows an **AI summary** button.

## Notes

- `ALLOWED_ORIGIN` in `wrangler.toml` locks CORS to the GitHub Pages origin;
  change it if you fork.
- Costs are yours: each answer is one Claude call (~1k output tokens).
  Cloudflare's free tier covers the Worker itself.
- The Worker truncates questions to 500 chars and context to 8 kB, and only
  answers from the digest the client sends — it never sees your whole dataset.
