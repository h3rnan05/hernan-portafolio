import { NextResponse } from "next/server";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const days      = searchParams.get("days") ?? "90";
  const minSignal = searchParams.get("min_signal") ?? "0";
  try {
    const res = await fetch(
      `${BACKEND_URL}/predictions/accuracy?days=${days}&min_signal=${minSignal}`,
      { cache: "no-store" }
    );
    if (!res.ok) return NextResponse.json([], { status: 200 });
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json([]);
  }
}
