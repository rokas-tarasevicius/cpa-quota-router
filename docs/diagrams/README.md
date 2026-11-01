# Diagrams

Each `*.diagram.json` here is the source of a diagram, and the `.svg` and `.dark.svg` beside it are
rendered from it, one for light mode and one for dark. The docs show whichever matches the
reader's theme with a `<picture>` element:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/ranking.dark.svg">
  <img alt="How the router places one account" src="diagrams/ranking.svg" width="720">
</picture>
```

Don't edit the SVGs by hand. The documents are plain JSON that says what kind of diagram it is
(`steps`, `sequence`, `tree`, `grid`, `compare`, `timeline`), what the things are and how they
connect; the renderer decides the drawing. It isn't published yet, so if a diagram needs to
change, edit its `.diagram.json` and say so in your pull request: the maintainer re-renders the
SVGs before merging.

| Document | Shows | Used in |
| --- | --- | --- |
| `round-robin-vs-soonest-reset` | Before and after: where new sessions go | README |
| `how-it-fits` | The router next to the proxy, and what each talks to | README, How it fits |
| `setup` | The five steps of an install | README, Running it |
| `request-routing` | What the proxy does with each request | How it works |
| `router-tick` | One run of the router | How it works |
| `ranking` | How one account is placed, with or without a 5-hour limit | How it works |
| `week-ahead` | The pool on 30 October 2026, played forward | How it works |
| `running-out` | What happens when the top account runs out mid-session | How it works |
