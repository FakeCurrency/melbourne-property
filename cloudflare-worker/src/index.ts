/**
 * Melbourne Property — AI Ask proxy.
 *
 * A tiny Cloudflare Worker that keeps the Anthropic API key off the static
 * site. The browser sends the user's question plus a compact digest of the
 * suburbs that already matched the client-side filters; Claude writes a short,
 * grounded recommendation. The worker never exposes the key and only answers
 * requests from ALLOWED_ORIGIN.
 */
import Anthropic from "@anthropic-ai/sdk";

interface Env {
  ANTHROPIC_API_KEY: string;
  ALLOWED_ORIGIN: string;
}

const MAX_QUESTION = 500;
const MAX_CONTEXT = 8000;

const SYSTEM = `You are the AI assistant for "Melbourne Property", a map that scores every
Greater Melbourne suburb 0-100 for Liveability and Development potential from public
government data (crime, SEIFA, prices, rents, yield, schools, trains, zoning, hazards).

You are given the user's question and a JSON digest of the suburbs that already matched
their filters, ranked by the relevant score. Answer in 2-4 short paragraphs:
- Recommend 2-4 suburbs from the digest ONLY (never invent suburbs or numbers).
- Explain the trade-offs between them in plain English using the digest's figures.
- If the digest is empty or thin, say so and suggest how to relax the search.
- End with a one-line reminder that this is data exploration, not financial advice.`;

function cors(origin: string): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
  };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const headers = { ...cors(env.ALLOWED_ORIGIN), "Content-Type": "application/json" };

    if (request.method === "OPTIONS") return new Response(null, { headers });
    if (request.method !== "POST")
      return new Response(JSON.stringify({ error: "POST only" }), { status: 405, headers });

    const origin = request.headers.get("Origin");
    if (origin && origin !== env.ALLOWED_ORIGIN)
      return new Response(JSON.stringify({ error: "origin not allowed" }), { status: 403, headers });

    let body: { question?: string; context?: unknown };
    try {
      body = await request.json();
    } catch {
      return new Response(JSON.stringify({ error: "invalid JSON" }), { status: 400, headers });
    }
    const question = (body.question || "").toString().slice(0, MAX_QUESTION).trim();
    const context = JSON.stringify(body.context ?? []).slice(0, MAX_CONTEXT);
    if (!question)
      return new Response(JSON.stringify({ error: "question required" }), { status: 400, headers });

    const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY });
    try {
      const msg = await client.messages.create({
        model: "claude-opus-4-8",
        max_tokens: 1024,
        thinking: { type: "adaptive" },
        system: SYSTEM,
        messages: [{
          role: "user",
          content: `Question: ${question}\n\nMatched-suburb digest (JSON):\n${context}`,
        }],
      });
      const text = msg.content
        .filter((b): b is Anthropic.TextBlock => b.type === "text")
        .map(b => b.text).join("\n");
      return new Response(JSON.stringify({ answer: text }), { headers });
    } catch (err) {
      const detail = err instanceof Error ? err.message : "upstream error";
      return new Response(JSON.stringify({ error: detail }), { status: 502, headers });
    }
  },
};
