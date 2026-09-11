# The headline drift guard

The title pipeline can give a headline that is not about the article. The drift guard finds these headlines and
sends them back to the model one time. This document tells you how the guard makes its decision, and where its
two numbers come from.

The language guard is different, and this document does not tell you about it.

## How the guard measures drift

The guard puts three texts into the same vector space with the embedding model from the `embedding:` settings:

- the article text,
- the original headline from the outlet, from the article metadata,
- the headline that the model made.

Then it calculates the cosine similarity of each headline to the article. A similarity is a number from 0 to 1.
A high number tells you that the headline and the article use related words and subjects.

The guard sends the headline back for one revision if one of these conditions is true:

- the generated headline is more than `drift_margin` below the original headline, or
- the generated headline is below `drift_floor`.

After the revision, the guard measures again. If the headline continues to drift, the pipeline keeps it and
writes a warning to the log. The guard does not refuse a headline, because the measure is not accurate
enough. Refer to **Known weaknesses**.

The guard does nothing if `embedding:` is null, or if the article has no original headline.

The model truncates the article at 512 tokens. So the guard compares the headlines to the start of the
article, which is the part that a headline usually tells about.

## The basis for the two numbers

The numbers come from a measurement on 2026-09-11 with the model `minishlab/potion-multilingual-128M`. The
measurement used three sets of data:

| Set | Size | Description |
| --- | --- | --- |
| Unrelated headlines | 1440 | Each article with 15 headlines of different articles from the same outlet |
| Detail sentences | 281 | Each article with 3 sentences from the middle of its own text |
| Real headlines | 54 | Headlines that the pipeline made in test runs, before any revision |

The unrelated headlines are the failure that the guard must find. The real headlines are the headlines that the
guard must not touch. The detail sentences show a limit of the method.

### `drift_margin`, default 0.1

The margin is the distance below the original headline that the guard permits.

| Margin | Unrelated headlines found | Real headlines sent back |
| --- | --- | --- |
| 0.08 | 92 % | 4 of 43 |
| 0.10 | 89 % | 4 of 43 |
| 0.12 | 87 % | 3 of 43 |
| 0.15 | 82 % | 2 of 43 |
| 0.20 | 73 % | 1 of 43 |

The results between 0.08 and 0.12 are almost equal. So the default is 0.1, the middle of that range. Values
above 0.15 find too few unrelated headlines.

A headline that the guard sends back is not always bad. It costs one more request to the model.

### `drift_floor`, default 0.4

The margin compares to the original headline. If the original headline is weak, for example a headline that
holds back the news to make you click, an unrelated headline can be as near to the article as the original.
The margin cannot see this. Approximately one unrelated headline in ten was of this type.

The floor is an absolute limit for the generated headline, and it finds these headlines:

| Set | Similarity to the article |
| --- | --- |
| Unrelated headlines | below 0.46 in 9 of 10 examples |
| Real headlines | above 0.54 in 9 of 10 examples |

The value 0.4 is between these two groups. With the floor, the guard finds 96 % of the unrelated headlines
instead of 89 %, and it sends back no more real headlines than before.

## Known weaknesses

**The original headline is the reference.** A static embedding model gives a high similarity for shared words.
So a headline of a tabloid that repeats the words of the article can get a higher similarity than a better
headline that uses different words. One example in the measurement: the original headline got 0.57, and a more
accurate new headline got 0.39, two times. This is the reason that the guard permits a headline that continues
to drift.

**The guard does not find a headline that tells about a detail.** A sentence from the middle of the article is
approximately as near to the article as the original headline. Only 26 % of the detail sentences went above the
margin of 0.1. So a headline that is correct, but tells about a small part of the article, can go through the guard.

## How to tune the numbers for your deployment

The numbers above are correct for the default embedding model, and for Finnish, Swedish and English news. A
different model gives different similarities.

Set `DEBUG=true` and do a run. The pipeline writes both similarities for each article to the log:

```json
{"event": "Headline similarity to the article", "original": 0.49, "generated": 0.64, "margin": 0.1, "floor": 0.4}
```

Collect these lines for approximately 50 articles. Then:

- Make the margin larger if many good headlines go back for a revision.
- Make the floor smaller if your articles have low similarities for all headlines.

The `review_title` span holds the same values, with the attributes `drift.original`, `drift.generated`,
`drift.margin` and `drift.floor`.
