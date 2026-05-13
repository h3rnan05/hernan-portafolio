"use client";

/**
 * Absolute last-resort error boundary. Fires only if the root layout itself
 * crashes (rare). Has to render its own <html> + <body> because layout never
 * mounted.
 */

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          background: "#08090d",
          color: "#e8eaf6",
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
          padding: "4rem 2rem",
          textAlign: "center",
          margin: 0,
        }}
      >
        <div style={{ maxWidth: 480, margin: "0 auto" }}>
          <h1 style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>
            Critical error
          </h1>
          <p style={{ color: "#9fa8c7", fontSize: 14, marginTop: 12 }}>
            The application couldn&rsquo;t start. Try refreshing the page.
          </p>
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: 24,
              padding: "10px 20px",
              background: "#00e5ff",
              color: "#08090d",
              border: 0,
              borderRadius: 8,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Try again
          </button>
          {error.message && (
            <pre
              style={{
                marginTop: 24,
                padding: 12,
                background: "#131520",
                borderRadius: 8,
                fontSize: 11,
                color: "#4a5068",
                textAlign: "left",
                overflow: "auto",
              }}
            >
              {error.message}
            </pre>
          )}
        </div>
      </body>
    </html>
  );
}
