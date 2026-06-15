"use client";

import { useEffect, useRef, useState } from "react";

type Quote = { symbol: string; price: number; change: number };

function fmt(n: number, d = 2) {
  return n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function TickerTape() {
  const [quotes, setQuotes] = useState<Quote[]>([]);

  useEffect(() => {
    const load = () =>
      fetch("/api/ticker")
        .then((r) => r.json())
        .then((d) => d.quotes && setQuotes(d.quotes))
        .catch(() => {});
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  if (quotes.length === 0) {
    // Static fallback while loading
    return (
      <span className="text-[10px] font-medium opacity-70 tracking-widest">
        ALPACA · SUPABASE · RENDER
      </span>
    );
  }

  // Duplicate so the scroll loops seamlessly
  const items = [...quotes, ...quotes];

  return (
    <div className="flex-1 overflow-hidden mx-6">
      <div
        className="flex items-center gap-6 whitespace-nowrap"
        style={{
          animation: "ticker-scroll 35s linear infinite",
          width: "max-content",
        }}
      >
        {items.map((q, i) => {
          const up = q.change >= 0;
          return (
            <span key={i} className="inline-flex items-center gap-2 text-[11px] font-semibold tracking-wide">
              <span className="text-black font-bold">{q.symbol}</span>
              <span className="text-black opacity-75">${fmt(q.price)}</span>
              <span
                className="inline-flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] font-bold"
                style={{
                  background: up ? "#006400" : "#8b0000",
                  color: "#ffffff",
                }}
              >
                {up ? "▲" : "▼"} {fmt(Math.abs(q.change), 2)}%
              </span>
              <span className="text-black opacity-25 ml-1">|</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}
