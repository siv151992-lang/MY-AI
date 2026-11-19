"""Memory architecture.

Tiers, from most to least volatile:

  1. Working memory      the recent turns of a chat, verbatim, sized to a token budget (context.py)
  2. Conversation state  structured goals / decisions / facts per chat (agent/reasoning/context.py)
  3. Rolling summary     prose summary of everything that has scrolled out of the working window
  4. Episodic segments   summaries of each compacted chunk of a chat, embedded with pgvector so details
                         from far back can be recalled when they become relevant again
  5. Long-term memory    durable facts about the user. Extracted candidates wait for the user's approval
                         (configurable); every memory can be inspected, corrected, exported and deleted.

Modules:
  privacy.py     per-user privacy settings, sensitivity classification, admission rules, redaction
  scoring.py     importance scoring, decay/reinforcement, hybrid (vector + keyword) rank fusion
  extraction.py  LLM-based candidate extraction with category, importance and correction detection
  context.py     token budgets, compaction planning, segment and rolling summaries
  store.py       PostgreSQL + pgvector persistence
  service.py     the public API used by the chat service, tools and HTTP routes
"""
