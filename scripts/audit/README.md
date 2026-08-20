# Browser audit — the product as an HR specialist meets it

`hr-journey.mjs` drives a real browser against real servers and checks the
things a recruiter would notice, in the order they would notice them: can I
sign up, does it tell me what it understood before spending money, what happens
to the broken file in my folder, can I see why this candidate lost, can I
disagree, can my colleague join, can a rival see my jobs.

It is not a substitute for `pytest` — it is the layer those tests cannot reach.
Everything it caught on its first run was invisible to a passing unit suite: a
Celery task that had lost its decorator (so Stage 5 never ran), an upload list
that stopped refreshing the moment the workers were quick, a candidate whose
surname is also a province, and a manipulation flag that deep analysis quietly
deleted.

## Running it

Bring the stack up with the offline models, so no API key or token is spent:

```bash
export SENTHIRE_FAKE_MODELS=1          # stand-in models; runs are stamped "demo"
export SENTHIRE_STORAGE_BACKEND=local  # no S3 needed
export SENTHIRE_DATABASE_URL=...       # a scratch database, migrated and seeded

uvicorn senthire.api.app:app --port 8000
celery -A senthire.workers.celery_app.celery_app worker -Q parse,screen,poll,mail
npm --prefix web run build && npm --prefix web start

CV_DIR=sample-cvs python scripts/audit/make_sample_cvs.py
CV_DIR=sample-cvs OUT_DIR=/tmp/audit node scripts/audit/hr-journey.mjs
```

Use a **fresh database** each time: the audit signs up, uses its quota and
leaves a workspace behind, and a half-used one makes failures ambiguous.

It prints a pass/fail line per expectation, writes screenshots and
`hr-report.json` to `OUT_DIR`, and exits non-zero if anything failed or the
browser console showed an error the product did not intend.
