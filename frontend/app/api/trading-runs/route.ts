import { NextResponse } from "next/server";

const BACKEND_URL   = process.env.NEXT_PUBLIC_API_URL   ?? "http://localhost:8000";
const ADMIN_TOKEN   = process.env.ADMIN_BEARER_TOKEN    ?? "";

export async function GET() {
  try {
    const res = await fetch(`${BACKEND_URL}/admin/trading-runs?limit=20`, {
      headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
      cache: "no-store",
    });
    if (!res.ok) return NextResponse.json([], { status: 200 });
    const runs = await res.json();
    return NextResponse.json(runs);
  } catch {
    return NextResponse.json([]);
  }
}
