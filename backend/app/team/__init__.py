"""Multi-agent orchestration ("team" mode).

  specialists.py   the seven agents: role, prompt, tools, whether they have side effects
  plan.py          task-graph parsing and validation (known agents, no cycles), scheduling waves
  browser.py       the Browser Agent's page model and tools (open, read, find, follow links)
  loop.py          one specialist's tool loop, ending in submit_result / submit_verdict with grounding checks
  orchestrator.py  plan → parallel delegation → conflict detection and adjudication → synthesis → verification
  store.py         PostgreSQL persistence (runs, tasks, events)
  manager.py       background runs, cancellation, restart recovery
"""
