---
name: design-bootstrap
description: Bootstrap the design workflow for a new project.
---

# Design Bootstrap

Create the first project-local design workspace, then let the generated files
carry the ongoing conventions. This is a bootstrap skill, not a recurring
dependency.

## Quick Start

From the target project root, run:

```bash
bash <skill-dir>/scripts/bootstrap-design.sh . <feature-slug> "<Feature title>"
```

Example:

```bash
bash <skill-dir>/scripts/bootstrap-design.sh . checkout-review "Checkout review"
```

The script creates:

```text
design/
  AGENTS.md
  dls.html
  <feature-slug>.html
  _render_common.sh
  render-png.sh
  render-gif.sh
  output/
```

If any target file already exists, stop and inspect the existing design
workspace instead of overwriting it.

## Workflow

1. Create or update `design/dls.html` first. Use it to settle the project theme,
   visual tokens, common UI elements, interaction states, and icon conventions.
2. Create feature mockups as `design/<feature>.html`, not phase folders by
   default. Split by product feature or workflow only when a file becomes too
   broad.
3. Keep feature mockups standalone HTML files unless the project already uses a
   build step for design artifacts.
4. Include the bottom beat bar in every feature mockup. Beats must load from
   `?beat=N`, update the URL on navigation, and reserve layout space so the bar
   does not cover the UI.
5. Use Lucide icons inline by default. Keep stroke width and icon sizing
   consistent; icon-only buttons need accessible labels and tooltips.
6. Render review artifacts with the generated scripts:

   ```bash
   design/render-png.sh design/<feature>.html --beat 1
   design/render-gif.sh design/<feature>.html --beats 1-4
   ```

7. After the initial bootstrap, follow `design/AGENTS.md` in the project. Do
   not re-run this skill unless the design workspace is missing or the user
   explicitly wants to regenerate it.

## DLS Contract

Make `dls.html` the source of truth for:

- Theme: color, typography, spacing, density, surfaces, borders, shadows.
- Buttons: confirm/primary, secondary, tertiary, destructive, disabled, loading.
- Components: modals, drawers, forms, inputs, tabs, toolbars, menus, empty
  states, alerts, tables/lists, and app shell patterns.
- Icon policy: Lucide inline by default, with consistent stroke width, size,
  labels, and tooltips for icon-only controls.
- Interaction states: hover, active, selected, focus, disabled, error, loading.
- Beat/navigation behavior expected in feature mockups.

When a feature mockup needs a new reusable pattern, add it to `dls.html` first
or in the same change.
