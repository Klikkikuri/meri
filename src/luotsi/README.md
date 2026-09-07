# Luotsi 🧭

Luotsi collects reader feedback on Klikkikuri's generated titles and makes it safe to show to a language model.

Readers rate a title (good / bad / suggestion) and can add a free-text comment. The submissions land in a Google
Sheet. Luotsi reads that sheet (or a local CSV export), normalizes the rows, and passes them through a chain of
guardrails that removes prompt injection attempts, redacts personal data, and limits length and language.

## Use

```python
from luotsi import Luotsi, LuotsiSettings

settings = LuotsiSettings(sources=[{"type": "csv", "path": "/app/instance/feedback.csv"}])
feedback = Luotsi(settings).get_feedback()
```

## Tuning the injection guard

The guard has two tiers. The blocklist matches a short list of literal phrases that carry no reading other than
an instruction to the model. The centroid tier — active when `embedding_model` is set — compares the message
against centroids trained from the labeled exemplar files in `luotsi/guards/data/`.

The tuning surface is the data, not the code:

```bash
luotsi train-guard luotsi/guards/data/vectors.potion-multilingual-128M.json \
  --data luotsi/guards/data/exemplars.en.txt \
  --data luotsi/guards/data/exemplars.fi.txt \
  --model /app/instance/potion-multilingual-128M
```

Training is deterministic and prints a report: the clusters it found, the injection centroids that a benign
centroid sits close enough to veto, and a self-check that re-classifies every training line. Read the report
before committing an artifact — it is where a blind spot becomes visible.

Add to `__label__injection` when a new attack pattern appears. Add to `__label__benign` when a real reader is
dropped: the benign class is a veto, so one well-chosen hard negative restores a whole neighbourhood. As real
feedback accrues, PII-scrubbed reader messages make better hard negatives than authored ones. Never commit raw
reader messages.

`luotsi translate-exemplars` grows the data into a new language. It is a maintainer tool: run it, read the
result, commit it. Deployments never translate.

## Personal data

Luotsi holds feedback in memory only. It does not write comment text to logs and does not persist it. The
spreadsheet behind the feedback form is the system of record — retention and erasure requests are handled there,
not in this package.

## Future work

`packages/luotsi/TODO.md` describes a tiered guardrail design. Tier 2, the semantic guardrail, is delivered by the
nearest-centroid injection guard. The Bayesian spam tier and an LLM-judge guard are out of scope: the containment
layers around the prompt (untrusted-data framing, escaping, length and count caps, per-article blast radius) are
the real backstop, and the centroid guard is precision-first by design.
