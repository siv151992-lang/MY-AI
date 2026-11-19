"""Coding workspace: projects stored as git repositories, edited by the user and the AI.

  fs.py         path-safe file access (no escaping the project, no touching .git)
  git.py        hardened git wrapper: status, diff, log, commit, branches, checkpoints, clone, push
  runner.py     runs commands in the sandbox against a copy of the project and applies the changes
  index.py      codebase understanding: repo map (symbols per file) and code search
  approvals.py  approval gate for destructive or publishing actions requested by the AI
  tools.py      the coding agent's tools
  store.py      PostgreSQL persistence (workspaces, activity log)
  service.py    workspace lifecycle and the coding chat turn
"""
