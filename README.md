# Misleading tweets report

Daily report of the tweets with the most traction that a Community Note rated helpful as misleading,
grouped into narratives (recurring claims). Published as a static page on GitHub Pages.

Pipeline (`python -m pipeline.main`): download the public Community Notes dump -> keep misleading notes
rated helpful in the last 7 days -> rank tweets by a notes-based proxy score -> cluster into narratives with an
LLM (OpenRouter) -> write `data/snapshots/<date>.json` and `site/index.html`.

The proxy score is not engagement: the dump has no views or likes. Adding the X API for real numbers is a future step.

## Run locally
    python -m venv .venv && .venv/bin/pip install -r requirements.txt
    export OPENROUTER_API_KEY=...        # optional; without it the report is a flat tweet ranking
    .venv/bin/python -m pipeline.main    # downloads ~720 MB on first run into ./work
    open site/index.html

Tuning lives in `config.yaml` (window, score weights, LLM model, report sizes).

## Deploy
1. Push to a public GitHub repo.
2. Settings -> Pages -> Source: GitHub Actions.
3. Settings -> Secrets -> Actions: add `OPENROUTER_API_KEY`.
4. Run the `daily-report` workflow once manually; it then runs daily at 07:00 UTC. State is stored on the `data` branch.
