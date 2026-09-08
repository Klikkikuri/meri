# Luotsi 🧭

Luotsi collects reader feedback on Klikkikuri's generated titles and makes it safe to show to a language model.

Readers rate a title (good / bad / suggestion) and can add a free-text comment. The submissions land in a Google
Sheet. Luotsi reads that sheet (or a local CSV export), normalizes the rows, and passes them through a chain of
guardrails that removes prompt injection attempts, redacts personal data, and limits length and language.

The default chain holds the injection guard, which needs an embedding model and a trained artifact to run at all.

## Use

```python
from luotsi import Luotsi, LuotsiSettings

settings = LuotsiSettings(sources=[{"type": "csv", "path": "/app/instance/feedback.csv"}])
feedback = Luotsi(settings).get_feedback()
```

Luotsi is a library with no command line of its own. The maintainer commands live in Meri, under
`meri feedback`, so that paths are read from configuration rather than retyped.

Luotsi never imports Meri and carries no LLM client. That is now a property of the code rather than a rule to
remember: nothing in this package talks to a language model. The one command that needs one, exemplar
translation, runs as a Meri pipeline.

## The injection guard

The guard has one tier. It compares the message against centroids trained from the labeled exemplar files in
`luotsi/guards/data/`, so it catches rewordings of the attack catalog and not only its exact phrasings. It needs
both an embedding model and a trained artifact.

**The trained artifact is not shipped with this package.** It is bound to the embedding model that produced it,
so each deployment trains its own. Meri owns that command, because it is where the paths are configured:

```bash
meri feedback download-model    # once, into the directory Meri resolves `embedding_model` to
meri feedback train-guard       # writes the configured `vectors` path
```

Without a model or an artifact the guard refuses to be built, and the run fails at start. There is no reduced
mode to fall back to, so a deployment that cannot run the guard says so in its configuration: it lists
`guardrails` explicitly and leaves `injection` out. That includes `embedding_model: null`. The default chain
holds the guard, so the default chain needs both. A broken deployment must not degrade quietly.

The exemplar files DO ship: they are this package's domain knowledge and the tuning surface. Training is
deterministic and prints a report — the clusters it found, the injection centroids that a benign centroid sits
close enough to veto, and a self-check that re-classifies every training line. Read the report before deploying
an artifact; it is where a blind spot becomes visible.

Add to `__label__injection` when a new attack pattern appears. Add to `__label__benign` when a real reader is
dropped: the benign class is a veto, so one well-chosen hard negative restores a whole neighbourhood. As real
feedback accrues, PII-scrubbed reader messages make better hard negatives than authored ones. Never commit raw
reader messages.

`meri feedback translate-exemplars` grows the data into a new language:

```bash
meri feedback translate-exemplars exemplars.en.txt exemplars.sv.txt --to Swedish
```

It runs as Meri's `feedback_translate` pipeline, so the model and its credentials come from Meri's `llm:` and
`pipelines:` configuration. The label of each line is shown to the model as ground truth for what the line is,
because an attack translated into polite prose is a weaker exemplar; the label itself is re-attached from the
source, never read back from the answer. It is a maintainer tool: run it, read the result, commit it.
Deployments never translate.

## Personal data

Luotsi holds feedback in memory only. It does not write comment text to logs and does not persist it. The
spreadsheet behind the feedback form is the system of record — retention and erasure requests are handled there,
not in this package.

## Future work

`packages/luotsi/TODO.md` describes a tiered guardrail design. Tier 2, the semantic guardrail, is delivered by the
nearest-centroid injection guard. The Bayesian spam tier and an LLM-judge guard are out of scope: the containment
layers around the prompt (untrusted-data framing, escaping, length and count caps, per-article blast radius) are
the real backstop, and the centroid guard is precision-first by design.
