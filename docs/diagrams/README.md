# Diagrams

These are drawn in the Fluxary diagram language. Each `*.diagram.json` is the source, and the
`.svg` and `.dark.svg` beside it are rendered from it. Don't edit the SVGs by hand: change the
document and render it again.

The renderer lives in `fluxary-os/diagrams/` and needs Bun:

```sh
cd ~/code/fluxary-os
bun diagrams/render.ts check ~/code/cpa-quota-router/docs/diagrams/*.diagram.json   # validate
bun diagrams/render.ts ~/code/cpa-quota-router/docs/diagrams/*.diagram.json         # write both SVGs
```

| Document | Shows |
| --- | --- |
| `round-robin-vs-soonest-reset` | Before and after: where new sessions go |
| `how-it-fits` | The router next to the proxy, and what each talks to |
| `request-routing` | What the proxy does with each request |
| `router-tick` | One run of the router |
| `ranking` | How one account is placed |
| `week-ahead` | The pool on 30 October 2026, played forward |
