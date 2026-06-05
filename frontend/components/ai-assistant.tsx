"use client";

/**
 * AI assistant — a slide-over chat drawer that talks to the backend /chat
 * endpoint. Streams Claude's response token-by-token, shows live tool-call
 * activity, and renders lightweight markdown. Opened from the top bar.
 */

import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { chatStream, type ChatMessage } from "@/lib/api";

const TOOL_LABELS: Record<string, string> = {
  list_stocks: "Listando acciones…",
  get_model: "Leyendo el modelo…",
  get_forecast: "Calculando pronóstico…",
  get_validation: "Validando fuera de muestra…",
  get_recent_predictions: "Revisando predicciones recientes…",
  list_variables: "Listando variables…",
  get_portfolios: "Leyendo portafolios…",
};

const SUGGESTIONS = [
  "¿Qué acciones cubre el motor?",
  "¿De verdad funciona? Sé honesto.",
  "Explícame cómo funciona el algoritmo, en simple.",
  "¿A dónde va el precio de NVDA esta semana?",
];

export function AiAssistant() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [activeTool, setActiveTool] = useState<string | null>(null);

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Focus the input on open; restore focus to the trigger on close.
  useEffect(() => {
    if (open) {
      const t = setTimeout(() => inputRef.current?.focus(), 120);
      return () => clearTimeout(t);
    }
    triggerRef.current?.focus();
  }, [open]);

  // ESC closes the drawer.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Auto-scroll to the newest content.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, activeTool]);

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
        // aborted or network error — leave whatever streamed so far
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

  const stop = () => abortRef.current?.abort();

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="group relative inline-flex h-9 items-center gap-1.5 rounded-full px-3.5 text-[13px] font-semibold text-[var(--color-text)] transition active:scale-[0.96]"
        style={{
          background:
            "linear-gradient(135deg, color-mix(in oklab, var(--color-green) 22%, transparent), color-mix(in oklab, var(--color-cyan) 18%, transparent))",
          boxShadow:
            "0 1px 0 0 color-mix(in oklab, var(--color-green) 35%, transparent) inset, 0 1px 8px -2px color-mix(in oklab, var(--color-green) 40%, transparent)",
        }}
      >
        <Sparkle />
        AI
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="fixed inset-0 z-50 flex justify-end"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <button
              type="button"
              aria-label="Cerrar asistente"
              onClick={() => setOpen(false)}
              className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
            />
            <motion.div
              role="dialog"
              aria-modal="true"
              aria-label="Asistente AI"
              className="relative flex h-full w-full max-w-[460px] flex-col border-l border-[var(--color-border)] bg-[var(--color-bg)] shadow-[0_0_60px_-12px_rgba(0,0,0,0.6)]"
              initial={{ x: 40, opacity: 0, filter: "blur(4px)" }}
              animate={{ x: 0, opacity: 1, filter: "blur(0px)" }}
              exit={{ x: 40, opacity: 0, filter: "blur(4px)" }}
              transition={{ type: "spring", stiffness: 320, damping: 32 }}
            >
              {/* Header */}
              <header className="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-3.5">
                <div className="flex items-center gap-2">
                  <span className="grid size-7 place-items-center rounded-full bg-[color-mix(in_oklab,var(--color-green)_18%,transparent)]">
                    <Sparkle />
                  </span>
                  <div className="leading-tight">
                    <div className="text-[13px] font-semibold text-[var(--color-text)]">
                      Asistente del motor
                    </div>
                    <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                      Datos en vivo · Claude
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  {messages.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setMessages([])}
                      className="rounded-md px-2 py-1 text-[11px] text-[var(--color-text3)] transition hover:bg-[var(--color-bg3)] hover:text-[var(--color-text2)]"
                    >
                      Limpiar
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    aria-label="Cerrar"
                    className="grid size-8 place-items-center rounded-md text-[var(--color-text3)] transition hover:bg-[var(--color-bg3)] hover:text-[var(--color-text)]"
                  >
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
                      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                    </svg>
                  </button>
                </div>
              </header>

              {/* Messages */}
              <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-4">
                {messages.length === 0 ? (
                  <Welcome onPick={send} />
                ) : (
                  <div className="flex flex-col gap-4">
                    {messages.map((m, i) => (
                      <Bubble
                        key={i}
                        role={m.role}
                        content={m.content}
                        streaming={
                          streaming && i === messages.length - 1 && m.role === "assistant"
                        }
                        activeTool={
                          i === messages.length - 1 && m.role === "assistant" ? activeTool : null
                        }
                      />
                    ))}
                  </div>
                )}
              </div>

              {/* Composer */}
              <div className="border-t border-[var(--color-border)] px-4 pb-4 pt-3">
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    send(input);
                  }}
                  className="flex items-end gap-2 rounded-2xl border border-[var(--color-border2)] bg-[var(--color-bg2)] p-2 focus-within:border-[var(--color-green)]/60"
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
                    className="max-h-32 flex-1 resize-none bg-transparent px-2 py-1.5 text-[13.5px] text-[var(--color-text)] outline-none placeholder:text-[var(--color-text3)]"
                  />
                  {streaming ? (
                    <button
                      type="button"
                      onClick={stop}
                      aria-label="Detener"
                      className="grid size-9 shrink-0 place-items-center rounded-xl bg-[var(--color-bg4)] text-[var(--color-text2)] transition hover:text-[var(--color-text)] active:scale-[0.94]"
                    >
                      <span className="size-3 rounded-[3px] bg-current" />
                    </button>
                  ) : (
                    <button
                      type="submit"
                      disabled={!input.trim()}
                      aria-label="Enviar"
                      className="grid size-9 shrink-0 place-items-center rounded-xl bg-[var(--color-green)] text-black transition active:scale-[0.94] disabled:opacity-40"
                    >
                      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
                        <path d="M8 13V3M4 7l4-4 4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </button>
                  )}
                </form>
                <p className="mt-2 px-1 text-[10.5px] text-[var(--color-text3)] text-pretty">
                  Puede equivocarse. Las cifras vienen de la base en vivo. No es asesoría
                  financiera.
                </p>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

function Welcome({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="flex h-full flex-col justify-center">
      <h2 className="text-balance text-[18px] font-semibold tracking-tight text-[var(--color-text)]">
        Pregúntame lo que sea sobre el motor.
      </h2>
      <p className="mt-1.5 text-[13px] text-[var(--color-text2)] text-pretty">
        Tengo acceso a los modelos, pronósticos, validaciones y datos en vivo. Puedo
        explicarte conceptos y darte las cifras reales.
      </p>
      <div className="mt-5 flex flex-col gap-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg2)] px-3.5 py-2.5 text-left text-[13px] text-[var(--color-text2)] transition hover:border-[var(--color-green)]/50 hover:text-[var(--color-text)] active:scale-[0.99]"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

function Bubble({
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
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-[var(--color-green)] px-3.5 py-2 text-[13.5px] text-black">
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {activeTool && (
        <div className="inline-flex w-fit items-center gap-2 rounded-full border border-[var(--color-border)] bg-[var(--color-bg2)] px-3 py-1 text-[11.5px] text-[var(--color-text2)]">
          <span className="size-1.5 animate-pulse rounded-full bg-[var(--color-cyan)]" />
          {TOOL_LABELS[activeTool] ?? "Consultando…"}
        </div>
      )}
      {content ? (
        <div className="max-w-[92%] text-[13.5px] leading-relaxed text-[var(--color-text)]">
          <Markdown text={content} />
          {streaming && <span className="ml-0.5 inline-block h-3.5 w-[2px] animate-pulse bg-[var(--color-green)] align-middle" />}
        </div>
      ) : (
        !activeTool && <Thinking />
      )}
    </div>
  );
}

function Thinking() {
  return (
    <div className="flex items-center gap-1.5 text-[var(--color-text3)]">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1.5 animate-bounce rounded-full bg-current"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  );
}

// ─── Minimal markdown renderer (bold, inline code, bullet/numbered lists) ─────

function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  const lines = text.split("\n");
  let list: { ordered: boolean; items: string[] } | null = null;

  const flush = (key: string) => {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    blocks.push(
      <Tag
        key={key}
        className={`my-1.5 flex flex-col gap-1 pl-1 ${list.ordered ? "[counter-reset:i]" : ""}`}
      >
        {list.items.map((it, i) => (
          <li key={i} className="flex gap-2">
            <span className="mt-[7px] shrink-0 text-[var(--color-text3)]">
              {list!.ordered ? `${i + 1}.` : "•"}
            </span>
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
          <p key={idx} className="mt-2 font-semibold text-[var(--color-text)]">
            {inline(heading[1])}
          </p>,
        );
      } else if (line.trim() === "") {
        blocks.push(<div key={idx} className="h-1.5" />);
      } else {
        blocks.push(
          <p key={idx} className="my-0.5">
            {inline(line)}
          </p>,
        );
      }
    }
  });
  flush("last");

  return <div>{blocks}</div>;
}

// Inline: **bold** and `code`.
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
          className="rounded bg-[var(--color-bg3)] px-1 py-0.5 font-mono text-[12px] text-[var(--color-text)]"
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

function Sparkle() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M8 1.5l1.4 3.6L13 6.5 9.4 7.9 8 11.5 6.6 7.9 3 6.5l3.6-1.4L8 1.5z"
        fill="var(--color-green)"
      />
      <path d="M13 10.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z" fill="var(--color-cyan)" />
    </svg>
  );
}
