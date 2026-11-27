# Results page (draft)

A TechEmpower-style results page for this repo. Local draft only: it is not
part of crocket.dev, and nothing here has been pushed or deployed.

`style.css`, `theme.js`, and `favicon.svg` are a snapshot of the crocket.dev
look. `bench.css` holds the results-page layout.

## Rebuild from results

    python3 site/build.py

Reads `results/run-*/raw/steps.jsonl` plus idle and cold-start samples, then
writes `data.json` and a pre-rendered `index.html`. A later run is merged in
and wins for the same framework and test. `render.js` also fetches `data.json`,
so replacing that file and refreshing is enough if the test tabs already exist.

## Preview

    python3 -m http.server 8765 --directory $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/site
