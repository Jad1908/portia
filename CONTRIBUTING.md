# Contributing

Bug reports and ideas are welcome as GitHub issues. The most useful report names the data shape
that broke something: row counts, column types, and what the copilot said against what was true.

Pull requests are not accepted yet. The project has one copyright holder and a contributor
agreement has to exist before that changes. If you have a fix, describe it in an issue.

```bash
uv sync --extra dev --extra agent --extra ui
uv run pytest
uv run pre-commit install
```

Never include real data, real schema names or credentials in an issue.
