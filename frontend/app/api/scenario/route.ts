/**
 * Calls OpenAI GPT-4o-mini to generate current macro scenario values + plain-language explanation.
 */

import { NextRequest, NextResponse } from "next/server";

const OPENAI_URL = "https://api.openai.com/v1/chat/completions";

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
  const apiKey = process.env.OPENAI_API_KEY;

  if (!apiKey) {
    return NextResponse.json({ error: "OPENAI_API_KEY not set" }, { status: 500 });
  }

  try {
    const res = await fetch(OPENAI_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model: "gpt-4o-mini",
        temperature: 0.4,
        max_tokens: 1024,
        messages: [
          {
            role: "system",
            content: "You are a financial educator. Always respond with valid JSON only, no markdown.",
          },
          { role: "user", content: buildPrompt(lang) },
        ],
      }),
    });

    if (!res.ok) {
      const err = await res.text();
      console.error("[scenario] OpenAI error:", res.status, err);
      return NextResponse.json({ error: `OpenAI ${res.status}: ${err}` }, { status: 500 });
    }

    const raw = await res.json();
    const text: string = raw.choices?.[0]?.message?.content ?? "";
    const clean = text.replace(/```json\n?/g, "").replace(/```\n?/g, "").trim();
    const data = JSON.parse(clean);

    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
