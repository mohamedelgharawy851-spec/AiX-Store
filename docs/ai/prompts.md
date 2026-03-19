# TinyLlama Prompts

## Rewrite Prompt

Version: `rewrite.v1`

Intent:
- rewrite only weak or empty user searches
- preserve category and product intent
- return strict JSON

Output contract:
```json
{
  "rewrites": [
    {
      "text": "gaming notebook",
      "strategy": "synonym",
      "must_terms": ["gaming", "notebook"],
      "optional_terms": ["laptop"],
      "broadness": "equivalent"
    }
  ]
}
```

Rules:
- no free-form prose
- no category drift
- max 3-5 rewrites
- no duplicate of the original query

## Category Judge Prompt

Version: `category-judge.v1`

Intent:
- judge only ambiguous products after the rules classifier runs
- choose from allowed AIXStore categories
- return strict JSON

Output contract:
```json
{
  "category_id": "electronics",
  "confidence": 7.8,
  "verdict": "accept",
  "reason": "Title and description strongly indicate an electronics device.",
  "used_candidates": ["electronics", "others"]
}
```

Rules:
- allowed verdicts: `accept`, `relabel`, `exclude`, `uncertain`
- if confidence is weak, return `others`
- do not invent new category ids
