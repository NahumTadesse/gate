# CLAUDE.md

## Definition of done

A change is done only when all three pass:

- `uv run pytest`
- `uv run ruff check`
- `uv run mypy src tests`

Never commit with any of the three failing.

## Commit messages

Never add Co-Authored-By trailers or any other AI attribution to commit messages.
