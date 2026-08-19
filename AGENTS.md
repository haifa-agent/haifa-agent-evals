# Haifa Agent Evals development guide

This repository evaluates coding agents; it is not a product regression-test framework.

- Keep the executable flow small: config -> Harbor -> CSV -> Markdown.
- Reuse Harbor tasks, trials, verifiers, and raw results. Do not add another harness or database.
- Keep credentials in environment variables. Never write secrets, full provider responses, or reasoning traces to reports.
- Python business logic belongs under `src/`; PowerShell and POSIX shell files are thin argument-forwarding wrappers.
- Run the focused tests and `uv run pytest` before committing.
- Commit messages are English. Do not modify the sibling `haifa-agent` repository from this repository.
