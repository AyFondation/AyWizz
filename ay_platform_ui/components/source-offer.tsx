// =============================================================================
// File: source-offer.tsx
// Version: 1
// Path: ay_platform_ui/components/source-offer.tsx
// Description: The AGPL §13 source offer, rendered on every page.
//
//              WHY THIS COMPONENT EXISTS. The platform is
//              AGPL-3.0-or-later, and §13 ("Remote Network Interaction")
//              is the clause that makes that choice mean anything: it is
//              what reaches a HOSTED deployment, where nothing is
//              distributed and GPLv2's copyleft would never trigger. The
//              obligation it creates is not satisfied by shipping a LICENSE
//              file — the running service must "prominently offer" every
//              user interacting with it over a network an opportunity to
//              receive the Corresponding Source.
//
//              So this is the operative half of the licence, and it binds
//              THIS deployment as much as anyone else's.
//
//              WHY NOT IN <BuildStamp>. That component sits in the navbar
//              and returns null until the config bootstrap is `ready`, so
//              it renders nothing on `/login` — which is exactly where an
//              unauthenticated network user arrives. §13 covers "all users",
//              not just authenticated ones, so the offer has to live in the
//              ROOT layout and depend on nothing.
//
//              The URL is the repository already declared in both tier
//              images' OCI labels (`org.opencontainers.image.source`), so
//              there is one source of truth for it rather than a second
//              string to drift.
//
//              A FORK MUST UPDATE IT. If you modify the platform and deploy
//              it, this link SHALL point at YOUR modified source, not at
//              upstream — §13 asks for the Corresponding Source of the
//              version actually running. Override it with
//              `NEXT_PUBLIC_SOURCE_URL` at build time rather than editing
//              this file, so the obligation survives a merge.
// =============================================================================

const UPSTREAM_SOURCE_URL = "https://github.com/AyFondation/AyWizz";

/** Where this running build's Corresponding Source can be obtained. */
export const SOURCE_URL = process.env.NEXT_PUBLIC_SOURCE_URL ?? UPSTREAM_SOURCE_URL;

export function SourceOffer() {
  return (
    <a
      href={SOURCE_URL}
      target="_blank"
      rel="noreferrer noopener license"
      data-testid="agpl-source-offer"
      className="fixed bottom-1 left-2 z-50 font-mono text-[10px] leading-none text-neutral-400 underline decoration-dotted hover:text-neutral-600 focus-visible:outline-2 focus-visible:outline-offset-2"
      title="This service runs AGPL-3.0-or-later software. Source available here (AGPL §13)."
    >
      AGPL-3.0 source
    </a>
  );
}
