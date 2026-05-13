import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-md px-6 py-24 text-center">
      <div className="mb-2 text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
        404
      </div>
      <h1 className="text-2xl font-semibold tracking-tight">Not found</h1>
      <p className="mt-2 text-[13px] text-[var(--color-text2)]">
        That page, ticker, or variable doesn&rsquo;t exist in this engine.
      </p>
      <Link
        href="/"
        className="mt-6 inline-flex h-9 items-center rounded-[8px] bg-[var(--color-bg3)] px-4 text-[12.5px] font-medium hover:bg-[var(--color-bg4)]"
      >
        Back to overview
      </Link>
    </div>
  );
}
