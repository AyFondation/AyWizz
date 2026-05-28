// =============================================================================
// File: postcss.config.mjs
// Version: 1
// Path: ay_platform_ui/postcss.config.mjs
// Description: PostCSS plugins. Tailwind v4 wires the whole engine as a
//              single PostCSS plugin — no separate `tailwind.config.*` is
//              needed when using the CSS-first configuration in globals.css.
// =============================================================================

export default {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};
