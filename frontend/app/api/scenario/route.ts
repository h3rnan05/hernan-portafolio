/**
 * Calls Google Gemini to generate current macro scenario values + plain-language explanation.
 */

import { NextRequest, NextResponse } from "next/server";

const GEMINI_MODEL = "gemini-2.0-flash";
const GEMINI_URL = (key: string) =>
  `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent?key=${key}`;

function buildPrompt(lang: string): string {
  const today = new Date().toISOString().split("T")[0];
  const inLang = lang === "es" ? "en español" : "in English";
  return `Today is ${today}.

Analyze the CURRENT global economic situation and return a JSON object with these exact keys:

"values": object with log-return shocks based on recent real trends:
  CPI_YoY_US (range -0.02 to 0.02), EUR_USD (range -0.03 to 0.03),
  Brent_Crude (range -0.05 to 0.05), Gold_Spot (range -0.04 to 0.04),
  Unemployment_Rate_US (range -0.02 to 0.02), USD_CNY (range -0.02 to 0.02)

"scenario_name": short name for current situation ${inLang} (max 4 words)

"explanation": 3-4 sentences ${inLang} for someone who knows nothing about finance.
  Explain what is happening economically right now and WHY each variable is moving.
  Use very simple language. No jargon.

"variable_context": object mapping each variable ID to one plain sentence ${inLang}
  describing what that variable means and its current direction.

Return ONLY valid JSON, no markdown, no code blocks.`;
}

export async function GET(req: NextRequest) {
  const lang = req.nextUrl.searchParams.get("lang") ?? "es";
  const apiKey = process.env.GEMINI_API_KEY;

  if (!apiKey) {
    return NextResponse.json({ error: "GEMINI_API_KEY not set" }, { status: 500 });
  }

  try {
    const res = await fetch(GEMINI_URL(apiKey), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        contents: [{ parts: [{ text: buildPrompt(lang) }] }],
        generationConfig: { temperature: 0.4, maxOutputTokens: 1024 },
      }),
    });

    if (!res.ok) {
      const err = await res.text();
      console.error("[scenario] Gemini error:", res.status, err);
      return NextResponse.json({ error: `Gemini ${res.status}: ${err}` }, { status: 500 });
    }

    const raw = await res.json();
    const text: string = raw.candidates?.[0]?.content?.parts?.[0]?.text ?? "";
    const clean = text.replace(/```json\n?/g, "").replace(/```\n?/g, "").trim();
    const data = JSON.parse(clean);

    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
