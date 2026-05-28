// =============================================================================
// File: coming-soon-section.tsx
// Version: 1
// Path: ay_platform_ui/components/coming-soon-section.tsx
// Description: Placeholder section body — renders the section's label
//              + description + a "coming in Phase X" pointer. Removed
//              as each section gets its real implementation.
// =============================================================================

interface Props {
  label: string;
  description?: string;
  phaseTag: string;
  bullets?: string[];
}

export function ComingSoonSection({ label, description, phaseTag, bullets }: Props) {
  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">{label}</h2>
        {description ? <p className="mt-1 text-sm text-neutral-500">{description}</p> : null}
      </header>

      <div
        className="mt-8 rounded-lg border border-dashed border-neutral-300 p-8 text-center"
        data-testid="coming-soon-panel"
      >
        <p className="text-sm font-medium text-neutral-700">
          This section lands in <span className="font-mono">{phaseTag}</span>.
        </p>
        {bullets && bullets.length > 0 ? (
          <ul className="mx-auto mt-4 max-w-md list-disc pl-5 text-left text-sm text-neutral-500">
            {bullets.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </main>
  );
}
