---
name: product-story
description: Review, question or update microIndia's product story (vision, personas, core loops for the brand product and the Local finder, promises, roadmap) with the product-engineer agent. Use when the user wants to think about the product, change direction, add a feature idea, or asks "what are we building and for whom".
argument-hint: [topic or new idea]
---

# /product-story $ARGUMENTS

1. Spawn the `product-engineer` agent. Give it:
   - the user's topic or idea, verbatim,
   - an instruction to read `docs/product/STORY.md`, the last 10 entries of `docs/product/DECISIONS.md`, and `docs/STATE.md`.
2. Ask it to:
   - say plainly how the idea fits or changes the story (personas, loop, promises, roadmap position),
   - name the smallest useful version and what the data can support today,
   - list any questions only the owner can answer,
   - update STORY.md and add a DECISIONS.md entry **only** for points the user has actually decided.
3. Relay the result to the user in a few lines. If there are open questions, ask them with AskUserQuestion.
4. If the story changed, run `/sync-governing`.
