/**
 * Calls Claude to generate current macro scenario values + plain-language explanation.
 */

import { NextRequest, NextResponse } from "next/server";

const ANTHROPIC_API = "https://api.anthropic.com/v1/messages";

const SYSTEM = `You are a financial educator. Given the current world economic situation,
you generate realistic macro variable shock values (as log-returns, small decimals) and
explain them in plain language for someone learning about finance.
Always respond with valid JSON only, no markdown code blocks.`;

function userPrompt(lang: string) {
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

Return ONLY valid JSON, no markdown.`;
}

export async function GET(req: NextRequest) {
  const lang = req.nextUrl.searchParams.get("lang") ?? "es";
  const apiKey = process.env.ANTHROPIC_API_KEY;

  if (!apiKey) {
    return NextResponse.json({ error: "ANTHROPIC_API_KEY not set" }, { status: 500 });
  }

  try {
    const res = await fetch(ANTHROPIC_API, {
      method: "POST",
      headers: {
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 1024,
        system: SYSTEM,
        messages: [{ role: "user", content: userPrompt(lang) }],
      }),
    });

    if (!res.ok) {
      const err = await res.text();
      return NextResponse.json({ error: err }, { status: 500 });
    }

    const raw = await res.json();
    const text: string = raw.content?.[0]?.text ?? "";
    const clean = text.replace(/```json\n?/g, "").replace(/```\n?/g, "").trim();
    const data = JSON.parse(clean);

    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
