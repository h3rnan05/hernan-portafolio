"use client";

/**
 * /assistant — a full page (real route, like /models) hosting the AI chat.
 * Streams Claude's answer token-by-token, shows live tool-call activity, and
 * renders lightweight markdown. Lives inside the normal app shell (the top nav
 * stays); the page fills the viewport below it with a pinned composer.
 */

import { motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { chatStream, type ChatMessage } from "@/lib/api";

const TOOL_LABELS: Record<string, string> = {
  list_stocks: "Listando acciones",
  get_model: "Leyendo el modelo",
  get_forecast: "Calculando el pronóstico",
  get_validation: "Validando fuera de muestra",
  get_recent_predictions: "Revisando predicciones recientes",
  list_variables: "Listando variables",
  get_portfolios: "Leyendo portafolios",
};

const SUGGESTIONS: { q: string; hint: string; icon: React.ReactNode }[] = [
  { q: "¿Qué acciones cubre el motor?", hint: "Las acciones y sus modelos", icon: <IconGrid /> },
  { q: "¿De verdad funciona? Sé honesto.", hint: "El rendimiento fuera de muestra", icon: <IconTarget /> },
  { q: "Explícame el algoritmo en simple.", hint: "Cómo predice el motor", icon: <IconBook /> },
  { q: "¿A dónde va NVDA esta semana?", hint: "Pronóstico con banda de confianza", icon: <IconTrend /> },
];

export default function AssistantPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [activeTool, setActiveTool] = useState<string | null>(null);

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Auto-scroll to newest content.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, activeTool]);

  // Auto-grow the textarea.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || streaming) return;

      const history: ChatMessage[] = [...messages, { role: "user", content: trimmed }];
      setMessages([...history, { role: "assistant", content: "" }]);
      setInput("");
      setStreaming(true);
      setActiveTool(null);

      const ctrl = new AbortController();
      abortRef.current = ctrl;
      let assistant = "";
      const commit = () =>
        setMessages((m) => {
          const copy = [...m];
          copy[copy.length - 1] = { role: "assistant", content: assistant };
          return copy;
        });

      try {
        for await (const ev of chatStream(history, ctrl.signal)) {
          if (ev.type === "text") {
            assistant += ev.text;
            setActiveTool(null);
            commit();
          } else if (ev.type === "tool") {
            setActiveTool(ev.name);
          } else if (ev.type === "error") {
            assistant += (assistant ? "\n\n" : "") + `⚠️ ${ev.message}`;
            commit();
          } else if (ev.type === "done") {
            break;
          }
        }
      } catch {
        // aborted or network error — keep whatever streamed
      } finally {
        if (!assistant) {
          assistant = "⚠️ No se pudo obtener respuesta. Intenta de nuevo.";
          commit();
        }
        setStreaming(false);
        setActiveTool(null);
        abortRef.current = null;
      }
    },
    [messages, streaming],
  );

  const hasConversation = messages.length > 0;

  return (
    <div className="relative flex h-[calc(100dvh-3.5rem)] flex-col overflow-hidden">
      {/* Ambient glow */}
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div
          className="absolute -top-48 left-1/2 size-[680px] -translate-x-1/2 rounded-full"
          style={{ background: "radial-gradient(closest-side, color-mix(in oklab, var(--color-green) 11%, transparent), transparent)" }}
        />
        <div
          className="absolute -bottom-52 right-[-12%] size-[560px] rounded-full"
          style={{ background: "radial-gradient(closest-side, color-mix(in oklab, var(--color-cyan) 8%, transparent), transparent)" }}
        />
      </div>

      {/* Conversation */}
      <div ref={scrollRef} className="relative z-10 flex-1 overflow-y-auto">
        {!hasConversation ? (
          <Welcome onPick={send} />
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-7 px-4 py-8 sm:px-6">
            {messages.map((m, i) => (
              <Message
                key={i}
                role={m.role}
                content={m.content}
                streaming={streaming && i === messages.length - 1 && m.role === "assistant"}
                activeTool={i === messages.length - 1 && m.role === "assistant" ? activeTool : null}
              />
            ))}
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="relative z-10 shrink-0 px-4 pb-5 pt-2 sm:px-6">
        <div className="mx-auto max-w-3xl">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
            className="group flex items-end gap-2 rounded-[18px] border border-[var(--color-border2)] bg-[var(--color-bg2)] p-2 shadow-[var(--shadow-2)] transition focus-within:border-[color-mix(in_oklab,var(--color-green)_55%,transparent)] focus-within:shadow-[0_0_0_4px_color-mix(in_oklab,var(--color-green)_12%,transparent)]"
          >
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
              rows={1}
              placeholder="Pregunta sobre el motor, las acciones, los pronósticos…"
              className="max-h-[200px] flex-1 resize-none self-center bg-transparent px-2.5 py-2 text-[14px] leading-relaxed text-[var(--color-text)] outline-none placeholder:text-[var(--color-text3)]"
            />
            {streaming ? (
              <button
                type="button"
                onClick={() => abortRef.current?.abort()}
                aria-label="Detener"
                className="grid size-10 shrink-0 place-items-center rounded-[13px] bg-[var(--color-bg4)] text-[var(--color-text2)] transition hover:text-[var(--color-text)] active:scale-[0.93]"
              >
                <span className="size-3 rounded-[3px] bg-current" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                aria-label="Enviar"
                className="grid size-10 shrink-0 place-items-center rounded-[13px] bg-[var(--color-green)] text-black shadow-[0_2px_10px_-2px_color-mix(in_oklab,var(--color-green)_60%,transparent)] transition active:scale-[0.93] disabled:opacity-30 disabled:shadow-none"
              >
                <IconSend />
              </button>
            )}
          </form>
          <div className="mt-2 flex items-center justify-between gap-3 px-1.5">
            <p className="text-[10.5px] text-[var(--color-text3)] text-pretty">
              Las cifras vienen de la base en vivo. Puede equivocarse — no es asesoría financiera.
            </p>
            <p className="hidden shrink-0 text-[10.5px] text-[var(--color-text3)] sm:block">
              <kbd className="rounded bg-[var(--color-bg3)] px-1 py-0.5 font-mono text-[10px]">⏎</kbd> enviar
              <span className="mx-1">·</span>
              <kbd className="rounded bg-[var(--color-bg3)] px-1 py-0.5 font-mono text-[10px]">⇧⏎</kbd> nueva línea
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function Welcome({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="mx-auto flex min-h-full max-w-3xl flex-col justify-center px-5 py-10">
      <motion.div
        initial={{ opacity: 0, y: 14, filter: "blur(6px)" }}
        animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
        transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
      >
        <span className="grid size-12 place-items-center rounded-2xl bg-[color-mix(in_oklab,var(--color-green)_14%,transparent)] ring-1 ring-[color-mix(in_oklab,var(--color-green)_28%,transparent)]">
          <span className="scale-[1.4]">
            <Sparkle />
          </span>
        </span>
        <h1 className="mt-5 text-balance text-[28px] font-semibold leading-tight tracking-tight text-[var(--color-text)] sm:text-[32px]">
          Pregúntame lo que sea
          <br />
          sobre el motor.
        </h1>
        <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-[var(--color-text2)] text-pretty">
          Tengo acceso en vivo a los modelos, pronósticos, validaciones fuera de muestra y
          datos del mercado. Te explico los conceptos en simple y te doy las cifras reales —
          sin inventar nada.
        </p>
      </motion.div>

      <div className="mt-8 grid gap-3 sm:grid-cols-2">
        {SUGGESTIONS.map((s, i) => (
          <motion.button
            key={s.q}
            type="button"
            onClick={() => onPick(s.q)}
            initial={{ opacity: 0, y: 12, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            transition={{ duration: 0.35, delay: 0.08 + i * 0.06, ease: [0.22, 1, 0.36, 1] }}
            className="group flex items-start gap-3 rounded-[16px] border border-[var(--color-border)] bg-[var(--color-bg2)] p-4 text-left shadow-[var(--shadow-1)] transition hover:-translate-y-0.5 hover:border-[color-mix(in_oklab,var(--color-green)_45%,transparent)] hover:shadow-[var(--shadow-2)] active:scale-[0.99]"
          >
            <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-[11px] bg-[var(--color-bg4)] text-[var(--color-text2)] transition group-hover:bg-[color-mix(in_oklab,var(--color-green)_16%,transparent)] group-hover:text-[var(--color-green)]">
              {s.icon}
            </span>
            <span className="min-w-0">
              <span className="block text-[13.5px] font-medium text-[var(--color-text)] text-pretty">
                {s.q}
              </span>
              <span className="mt-0.5 block text-[11.5px] text-[var(--color-text3)]">{s.hint}</span>
            </span>
          </motion.button>
        ))}
      </div>
    </div>
  );
}

function Message({
  role,
  content,
  streaming,
  activeTool,
}: {
  role: "user" | "assistant";
  content: string;
  streaming: boolean;
  activeTool: string | null;
}) {
  if (role === "user") {
    return (
      <motion.div
        initial={{ opacity: 0, y: 10, filter: "blur(3px)" }}
        animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
        transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
        className="flex justify-end"
      >
        <div className="max-w-[85%] whitespace-pre-wrap rounded-[18px] rounded-br-md bg-[color-mix(in_oklab,var(--color-green)_16%,var(--color-bg3))] px-4 py-2.5 text-[14px] leading-relaxed text-[var(--color-text)] ring-1 ring-[color-mix(in_oklab,var(--color-green)_20%,transparent)]">
          {content}
        </div>
      </motion.div>
    );
  }
  return (
    <motion.div
      initial={{ opacity: 0, y: 12, filter: "blur(4px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="flex gap-3"
    >
      <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-[color-mix(in_oklab,var(--color-green)_14%,transparent)] ring-1 ring-[color-mix(in_oklab,var(--color-green)_24%,transparent)]">
        <Sparkle small />
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        {activeTool && (
          <div className="mb-2 inline-flex items-center gap-2 overflow-hidden rounded-full border border-[var(--color-border2)] bg-[var(--color-bg2)] px-3 py-1 text-[12px] text-[var(--color-text2)]">
            <span className="size-1.5 animate-pulse rounded-full bg-[var(--color-cyan)]" />
            <span className="shimmer">{TOOL_LABELS[activeTool] ?? "Consultando"}…</span>
          </div>
        )}
        {content ? (
          <div className="text-[14px] leading-[1.7] text-[var(--color-text)]">
            <Markdown text={content} />
            {streaming && (
              <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-[var(--color-green)] align-text-bottom" />
            )}
          </div>
        ) : (
          !activeTool && <Thinking />
        )}
      </div>
    </motion.div>
  );
}

function Thinking() {
  return (
    <div className="flex items-center gap-1.5 pt-1 text-[var(--color-text3)]">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-2 animate-bounce rounded-full bg-current"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  );
}

// ─── Minimal markdown (bold, inline code, bullet/numbered lists, headings) ────

function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  const lines = text.split("\n");
  let list: { ordered: boolean; items: string[] } | null = null;

  const flush = (key: string) => {
    if (!list) return;
    const cur = list;
    const Tag = cur.ordered ? "ol" : "ul";
    blocks.push(
      <Tag key={key} className="my-2 flex flex-col gap-1.5">
        {cur.items.map((it, i) => (
          <li key={i} className="flex gap-2.5">
            {cur.ordered ? (
              <span className="shrink-0 tabular-nums text-[var(--color-text3)]">{i + 1}.</span>
            ) : (
              <span className="mt-[9px] size-1.5 shrink-0 rounded-full bg-[var(--color-green)]/70" />
            )}
            <span>{inline(it)}</span>
          </li>
        ))}
      </Tag>,
    );
    list = null;
  };

  lines.forEach((raw, idx) => {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+\.\s+(.*)$/);
    const heading = line.match(/^#{1,4}\s+(.*)$/);

    if (bullet) {
      if (!list || list.ordered) flush(`l${idx}`);
      list = list ?? { ordered: false, items: [] };
      list.items.push(bullet[1]);
    } else if (numbered) {
      if (!list || !list.ordered) flush(`l${idx}`);
      list = list ?? { ordered: true, items: [] };
      list.items.push(numbered[1]);
    } else {
      flush(`l${idx}`);
      if (heading) {
        blocks.push(
          <p key={idx} className="mb-1 mt-3 text-[15px] font-semibold text-[var(--color-text)]">
            {inline(heading[1])}
          </p>,
        );
      } else if (line.trim() === "") {
        blocks.push(<div key={idx} className="h-2" />);
      } else {
        blocks.push(
          <p key={idx} className="my-1">
            {inline(line)}
          </p>,
        );
      }
    }
  });
  flush("last");

  return <div>{blocks}</div>;
}

function inline(text: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[2] !== undefined) {
      out.push(
        <strong key={k++} className="font-semibold tabular-nums text-[var(--color-text)]">
          {m[2]}
        </strong>,
      );
    } else if (m[3] !== undefined) {
      out.push(
        <code
          key={k++}
          className="rounded bg-[var(--color-bg3)] px-1.5 py-0.5 font-mono text-[12.5px] text-[var(--color-cyan)]"
        >
          {m[3]}
        </code>,
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// ─── Icons ────────────────────────────────────────────────────────────────────

function Sparkle({ small }: { small?: boolean }) {
  const s = small ? 12 : 14;
  return (
    <svg width={s} height={s} viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M8 1.5l1.4 3.6L13 6.5 9.4 7.9 8 11.5 6.6 7.9 3 6.5l3.6-1.4L8 1.5z" fill="var(--color-green)" />
      <path d="M13 10.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6.6-1.6z" fill="var(--color-cyan)" />
    </svg>
  );
}
function IconSend() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M8 13V3M4 7l4-4 4 4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function IconGrid() {
  return (
    <svg width="17" height="17" viewBox="0 0 18 18" fill="none" aria-hidden>
      <rect x="2.5" y="2.5" width="5" height="5" rx="1.2" stroke="currentColor" strokeWidth="1.4" />
      <rect x="10.5" y="2.5" width="5" height="5" rx="1.2" stroke="currentColor" strokeWidth="1.4" />
      <rect x="2.5" y="10.5" width="5" height="5" rx="1.2" stroke="currentColor" strokeWidth="1.4" />
      <rect x="10.5" y="10.5" width="5" height="5" rx="1.2" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}
function IconTarget() {
  return (
    <svg width="17" height="17" viewBox="0 0 18 18" fill="none" aria-hidden>
      <circle cx="9" cy="9" r="6.5" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="9" cy="9" r="3" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="9" cy="9" r="0.6" fill="currentColor" />
    </svg>
  );
}
function IconBook() {
  return (
    <svg width="17" height="17" viewBox="0 0 18 18" fill="none" aria-hidden>
      <path d="M9 4.2c-1.4-1-3.4-1.2-5-0.8v9c1.6-0.4 3.6-0.2 5 0.8 1.4-1 3.4-1.2 5-0.8v-9c-1.6-0.4-3.6-0.2-5 0.8z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M9 4.2v9.8" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}
function IconTrend() {
  return (
    <svg width="17" height="17" viewBox="0 0 18 18" fill="none" aria-hidden>
      <path d="M2.5 12.5l4-4 3 3 5.5-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M11.5 5.5H15V9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
