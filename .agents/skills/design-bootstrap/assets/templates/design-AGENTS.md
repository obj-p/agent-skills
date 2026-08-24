# Design Mockups

This directory contains agent-created HTML mockups. The generated project
conventions here are the source of truth after bootstrapping; do not re-run the
bootstrap skill unless this workspace is missing or the user asks to regenerate
it.

## Files

- `dls.html`: design language system. Settle the visual theme, tokens, common UI
  elements, component states, modal patterns, and icon conventions here first.
- `<feature>.html`: standalone interactive mockups for product behavior. Name by
  feature or workflow, not by phase.
- `render-png.sh`: render one beat or state to `design/output`.
- `render-gif.sh`: render a beat sequence to `design/output`.
- `output/`: generated review artifacts.

## DLS Rules

- Update `dls.html` before or alongside any feature mockup that introduces a new
  reusable visual pattern.
- Define theme tokens for color, typography, spacing, surfaces, borders,
  shadows, focus, disabled, loading, and error states.
- Include button variants: confirm/primary, secondary, tertiary, destructive,
  disabled, and loading.
- Include reusable patterns for modals, drawers, forms, inputs, tabs, toolbars,
  menus, alerts, empty states, lists/tables, and app shell layout when relevant.
- Use Lucide icons inline by default. Keep stroke width and sizing consistent.
  Icon-only buttons need accessible labels and tooltips.

## Feature Mockups

- Keep feature mockups standalone HTML unless this project explicitly adopts a
  build step for design artifacts.
- Use `?beat=N` for every reviewable state. Loading a URL with a beat must show
  that exact state without manual setup.
- Include the standard fixed bottom beat bar in every feature mockup. It must
  show beat number/title, provide previous/next controls, update the URL, and
  avoid covering content.
- Use realistic app surfaces and workflow states. Do not create marketing or
  landing pages unless the user asks for one.
- Keep text inside buttons, panels, and controls from overflowing at mobile and
  desktop sizes.

## Rendering

Render a still:

```bash
design/render-png.sh design/<feature>.html --beat 1
```

Render a beat sequence:

```bash
design/render-gif.sh design/<feature>.html --beats 1-4
```

Set `CHROME_BIN` if Chrome is not in a standard location. `render-gif.sh`
requires `ffmpeg`.
