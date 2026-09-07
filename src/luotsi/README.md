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

## Personal data

Luotsi holds feedback in memory only. It does not write comment text to logs and does not persist it. The
spreadsheet behind the feedback form is the system of record — retention and erasure requests are handled there,
not in this package.

## Future work

`packages/luotsi/TODO.md` describes a tiered guardrail design. Tier 2, the semantic guardrail, is delivered by the
nearest-centroid injection guard. The Bayesian spam tier and an LLM-judge guard are out of scope: the containment
layers around the prompt (untrusted-data framing, escaping, length and count caps, per-article blast radius) are
the real backstop, and the centroid guard is precision-first by design.
