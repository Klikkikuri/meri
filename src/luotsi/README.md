# Luotsi 🧭

Luotsi collects reader feedback on Klikkikuri's generated titles and makes it safe to show to a language model.

Readers rate a title (good / bad / suggestion) and can add a free-text comment. The submissions land in a Google
Sheet. Luotsi reads that sheet (or a local CSV export), normalizes the rows, and passes them through a chain of
guardrails that removes prompt injection, hateful agenda steering and spam, redacts personal data, and limits
length and language.

The default chain holds the injection guard, which needs an embedding model and a trained artifact to run at all.

## Use

```python
from luotsi import Luotsi, LuotsiSettings

settings = LuotsiSettings(sources=[{"type": "csv", "path": "/app/instance/feedback.csv"}])
feedback = Luotsi(settings).get_feedback()
```

`get_feedback()` is the one-shot form: fetch everything, guard everything. A caller that only needs part of
the corpus uses the two halves instead, and guards batch by batch as it goes:

```python
client = Luotsi(settings)
corpus = client.collect()                  # raw, straight from the sources
kept = client.guard(corpus[:10])           # the chain, over as much or as little as you like
```

Every guard is per-item, so guarding a subset gives those items the same verdict as guarding the whole
corpus.

Luotsi is a library with no command line of its own. The maintainer commands live in Meri, under
`meri feedback`, so that paths are read from configuration rather than retyped.

Luotsi never imports Meri and carries no LLM client. That is now a property of the code rather than a rule to
remember: nothing in this package talks to a language model. The one command that needs one, exemplar
translation, runs as a Meri pipeline.

## The injection guard

The guard has one tier. It compares the message against centroids trained from the labeled exemplar files in
`luotsi/guards/data/`, so it catches rewordings of the attack catalog and not only its exact phrasings. It needs
both an embedding model and a trained artifact.

The English file carries four labels. `injection` is instructions aimed at the model, `toxic` is agenda steering
— a reader trying to make the headline carry a hateful framing, not abuse — and `spam` is off-topic text,
advertising and zero-effort noise. `benign` is the veto. The three drop classes share one decision: `classify`
takes the nearest non-benign centroid whatever its label, against one floor and one margin, so a new drop class
widens the same surface rather than adding a tier of its own. The Finnish file is still `injection` and `benign`.

**The trained artifact is not shipped with this package.** It is bound to the embedding model that produced
it, so each deployment builds its own. `luotsi.provision(settings)` does that — and `meri run` calls it at
the start of every run, so a deployment that edits its exemplars or swaps its model gets a rebuilt artifact
with no operator action.

The model is provisioned the same way: `meri run` fetches it into the directory `embedding_model` resolves to
when nothing is there, so a cold deployment needs no operator action either. Fetch it ahead of time, or into a
directory the configuration does not name, with:

```bash
meri feedback download-model    # into the directory Meri resolves `embedding_model` to
```

A run that must not reach the hub — an air-gapped deployment provisioning its own model directory — takes
`meri run --no-download-model`, and then a missing model is an error rather than a download.

**Provisioning writes; guards only read.** That is the same division this package already makes for the
embedding model — `download_model` fetches, `load_embedder` loads and fails if nothing is there — and it is
what lets a host check without changing anything. A guard handed vectors whose exemplars, threshold,
dimension or model no longer match does not quietly rebuild them: it refuses to be built and names the
reason. So `meri run` and `meri feedback train-guard` write the artifact, and every other command reads it.

Staleness is decided by a digest of the exemplars recorded in the artifact, not by file timestamps, so
rolling a deployment BACK to an older catalog rebuilds just as an edit forwards does. Building the centroids
is the cheap half — about 0.2s on the shipped catalog — because the expensive self-check belongs to the
report.

The model is identified by whatever the host calls it — in Meri the hub identifier, so
`minishlab/potion-multilingual-128M` and `otherorg/potion-multilingual-128M` are distinguishable where a bare
directory name would not be. **One case this does not catch:** fetching a new revision of the same model into
the same directory changes the weights but not the identifier or the dimension, so the artifact reads as
current and the guard keeps centroids built from the old weights. Only fingerprinting what the model produces
would close this by itself, so the two routes to new weights each answer for it: `provision(settings,
force=True)` rebuilds the artifact unconditionally, and `meri run` passes `force` exactly when it fetched a
model — which is how an unattended run stays honest. `meri feedback download-model` overwrites in place and
has nobody to force, so it says to retrain with `meri feedback train-guard`.

Without a model, or with nowhere to keep the artifact, the guard refuses to be built and the run fails at
start. There is no reduced mode to fall back to, so a deployment that cannot run the guard says so in its
configuration: it lists `guardrails` explicitly and leaves `injection` out. That includes
`embedding_model: null`. A broken deployment must not degrade quietly — which is also why a destination that
cannot be written fails the run rather than being retrained around on every start.

The exemplar files DO ship, and they are the default training data: they are this package's domain knowledge
and the tuning surface. A deployment's `data` REPLACES them rather than adding to it, so a catalog that ends
up with no attack class or no benign class cannot classify, and the guard refuses to start on it instead of
passing everything or dropping everyone.

Training is deterministic and `meri feedback train-guard` prints a report — the clusters it found, the benign
centroids sitting close enough to veto an attack centroid, and a self-check. That report is the reason to run
the command by hand: a run provisions the same artifact without it, because nobody reads a report at startup.

Read the report's two halves for what they are:

- **Shadows are ranked, not listed.** A catalog with several drop classes produces hundreds of benign/attack
  pairs above the old cutoff, so only the `brittle` ones — where the benign centroid leaves the attack centroid
  less room than the margin it has to clear — are printed. The rest are counted. A non-zero brittle count is the
  thing to act on.
- **The self-check re-checks each line wrapped in ordinary reader text**, not only as written. As written it
  cannot fail on its own account: every exemplar is its own centroid and scores 1.000 against itself. `wrong
  once wrapped` is the real number, and it is how a line that defends only its own string gets caught.

`meri feedback probe` answers the question training cannot, because the trainer only ever sees its own data:

```bash
meri feedback probe candidates.txt              # what does the artifact do with lines it never saw
meri feedback probe candidates.txt --sweep      # ... and how the two error rates trade against each other
```

It classifies labeled lines exactly as a deployment would — sanitized, embedded, through the same `classify` —
and names the two errors apart: an attack line that survives is an **evasion**, a benign line that drops is a
reader being **silenced**. Both are what the next exemplars should be written from. `--sweep` re-scores the same
lines over a grid of floors and margins, which is what turns "the guard missed this" into either a threshold to
move or the evidence that no threshold setting is good enough.

`meri feedback check` answers the same question for one message, with no file and no label:

```bash
meri feedback check "the headline should say which country the suspects came from"
```

```text
DROPPED

  nearest drop   0.704  toxic: why does the headline not give the nationality of the dealers, that is the entire story
  nearest benign 0.595  this bout was billed as finland against sweden and the heading mentions neither country

  0.704 clears the floor 0.6 and beats the nearest benign exemplar by 0.109, which clears the margin 0.1
```

It prints both centroids the decision turned on, and which clause of the rule settled it, because the verdict
alone does not distinguish a message that sits on an attack from one that is merely far from everything benign —
and, as above, it does not show when a drop came down to a thousandth. Sanitized text is shown whenever cleaning
changed the message, which is how invisible padding becomes visible.

`meri feedback show` goes the other way, from one article to the prompt the model receives:

```bash
meri feedback show https://www.example.com/news/some-article
```

It pulls from the configured sources, runs the whole chain over this article's feedback, consolidates repeated
messages and renders the `feedback.md.j2` block — so what lands on stdout is the untrusted-data section of the
prompt itself, not a summary of it. Only the URL is needed: feedback is matched by signature, so nothing is
fetched or extracted. Diagnostics (the signature, how many items matched and how many of those survived the
guards, how many groups this article got) go to stderr, so the block can be piped. When nothing matches, the
signature it printed is the first thing to check.

Add to a drop class when a new attack pattern appears. Add to `__label__benign` when a real reader is dropped:
the benign class is a veto, so one well-chosen hard negative restores a whole neighbourhood. As real feedback
accrues, PII-scrubbed reader messages make better hard negatives than authored ones. Never commit raw reader
messages.

Two rules that the data earned the hard way:

- **A benign line must never be a negated copy of an attack line.** The embedder is negation-blind, so "do not
  put their nationality in the title" lands about 0.88 from the line demanding exactly that, and the attack
  centroid is left defending only its own literal string. Make the same point in different words.
- **Do not trust the self-check to tell you a line is safe.** It is trivially perfect, because every exemplar is
  its own centroid and scores 1.000 against itself. Judge a candidate by whether it still classifies correctly
  when wrapped in ordinary reader text — a greeting, a leading clause of genuine critique.

The additions below the shipped sections came from an adversarial exercise: attacker and defender agents wrote
candidates, each round was trained and re-probed, and what survived is what the guard did not already catch.

Two limits are known and are not data gaps. Long feedback defeats the guard by dilution — the message is
embedded and scored whole, so a payload padded with genuine critique falls below the floor while the benign half
raises the veto, and this holds even for payloads that are already centroids. And zero-effort noise does not
generalize: junk strings get near-arbitrary static vectors, so each shape is its own centroid and the space of
shapes is unbounded. A length and character-class rule is the right instrument for that, not this file.

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
