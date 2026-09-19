<div align="center">
  <h1>
    <img src="portia/ui/assets/cute-portia.png" alt="portia logo" width="180" align="middle">
    portia
  </h1>
  <p><strong>Start catching the bugs in your data.</strong></p>

  <p>
    <img alt="python 3.11+" src="https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white">
    <img alt="engine: DuckDB" src="https://img.shields.io/badge/engine-DuckDB-FFF000?logo=duckdb&logoColor=black">
    <img alt="warehouse: Snowflake, BigQuery" src="https://img.shields.io/badge/warehouse-Snowflake%20%C2%B7%20BigQuery-29B5E8">
    <img alt="copilot: Claude or a local model" src="https://img.shields.io/badge/copilot-Claude%20or%20local-D97757">
  </p>

  <sub>Named after <a href="https://en.wikipedia.org/wiki/Portia_(spider)"><em>Portia</em></a>, the jumping
  spider that hunts other spiders.</sub>
</div>

---

portia is a copilot for data work. Connect it to Snowflake, BigQuery or a folder of CSV and
Parquet files. It profiles every table, answers your questions about them, draws charts, and builds
the tables you ask for one decision at a time. Each decision is recorded in a YAML spec and compiled
to dbt-shaped SQL that runs without portia.

The model never reads your data. It has no filesystem and no shell. Every number it tells you was
computed by deterministic code, so it works on tables too big to open and every claim it makes can
be checked.

## Install

If an AI agent is installing this for you, point it at [`INSTALL.md`](INSTALL.md). It covers the
app and the Claude Code plugin, step by step, with a check after each step.

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
git clone https://github.com/Jad1908/portia.git
cd portia
uv sync --extra ui --extra agent --extra graph      # add --extra snowflake or --extra bigquery
docker compose up -d neo4j
```

Neo4j holds the knowledge graph, which is where column lineage and measured overlaps live. The
copilot reads it to work out which table to look at.

The copilot runs on the [Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview), which
installs Anthropic's Claude Code and runs it unmodified. Set `ANTHROPIC_API_KEY`, or use a
[local model](#local-models). portia has no sign-in of its own and never reads, stores or forwards
a credential, so usage is billed to your own Anthropic account. Neo4j needs a password:

```bash
export NEO4J_PASSWORD=portia-dev
```

portia is an independent project and is not affiliated with Anthropic.

## Quick start

```bash
uv run python -m portia.ui
```

1. Pick a project directory and write a few lines about what the project is.
2. Point it at your data: a warehouse connection, or a folder in the project. Press Index.
3. Ask. "What do we have here." "Is `order_id` really unique." "Show me revenue by month."
4. Ask it to build. Every step stops on screen with its measured result before it is written.
5. Press Build. The pipeline lands in `models/` as one `.sql` per table.

To try it on data with known problems, `python -m devtools.demodata` writes a five-table demo
project to `sandbox/demo` and `devtools/demodata/DEMO.md` says what is planted in it.

## Features

- **Snowflake, BigQuery and local files.** Tables are read where they are. On a warehouse, queries run under
  your role and nothing is pulled down. On disk, nothing is copied.
- **Profiling and join diagnosis.** Null rates, distinct counts, key coverage and fan-out, measured
  before any join is run.
- **Charts in the conversation.** Ask to be shown something and the chart opens in a tab beside the
  chat. On a model that takes images, the copilot can look at the chart your window painted, to
  check that it reads before telling you it does. The picture is never saved, and no number or
  decision comes from it.
- **Specs and compiled SQL.** A spec holds the decision, the rationale and the predicted outcome.
  Re-running it reports drift. `build --check` fails CI when a `.sql` no longer matches its spec.
- **A gate on zeros.** An empty result, a non-unique grain or a column that came out all null is
  refused, never silently written.
- **Knowledge graph.** Column lineage across the pipeline, and how much two columns actually
  overlap. Browse it at `http://localhost:7474`.
- **A journal and a log.** Every chat, indexing job and question asked of the data stays in the
  project.

## Inside Claude Code

portia's tools also run in your own Claude Code session, with no window. Install the two
commands the plugin starts, then the plugin:

```bash
uv tool install "portia[agent,ui,graph] @ git+https://github.com/Jad1908/portia"
claude plugin marketplace add Jad1908/portia
claude plugin install portia@portia
```

Start a new Claude Code session afterwards. [`INSTALL.md`](INSTALL.md) has the checks, the
warehouse setup and what to do when something goes wrong.

Claude then profiles, queries, charts and records steps through portia, and a hook holds a reply
until it has reviewed what it asked, which is how findings get kept. Charts go to the window:
`portia ui --project .` opens it and it follows the session within two seconds. With
the window closed Claude says a chart is waiting.

The difference from the app is one you should know. The app's copilot has no file access, so it
cannot read raw data. Claude Code does have file access. The plugin refuses its file tools on
indexed data and on portia's own files, and a shell command is not refused. See
[`plugin/README.md`](plugin/README.md).

## Local models

The copilot also runs on a model served on your machine by [Ollama](https://ollama.com) or
llama.cpp's `llama-server`. Pick the provider beside the model in the composer, or pass
`--provider` to `cli.chat`. The instructions alone are about 15,000 tokens, so serve at least a
32K context. portia checks before sending and refuses with both numbers if it is smaller.

```bash
OLLAMA_CONTEXT_LENGTH=32768 ollama serve                   # then: ollama pull qwen3:8b
llama-server -hf <org/repo:quant> -c 32768 -np 1 --cache-reuse 256 --port 8081
python -m portia.cli.models list                           # every provider and its models
python -m portia.cli.models check --provider ollama qwen3:8b
```

The window can start `llama-server` for you. On a 16 GB laptop an 8B model takes three to four
minutes on a cold first turn.

## Command line

The same engine, without the window.

```bash
python -m portia.cli.index data              # profile files into the catalog
python -m portia.cli.connect add prod ...    # add a warehouse connection
python -m portia.cli.chat ask "..."          # one exchange with the copilot
python -m portia.cli.run specs/x.yaml        # execute one spec
python -m portia.cli.build [--check]         # compile every spec to models/*.sql
python -m portia.cli.journal list --spec x   # what was asked on the way to one model
python -m portia.cli.history                 # replay past chats
python -m portia.cli.knowledge --write       # rebuild the knowledge graph
```

## Development

```bash
uv sync --extra dev --extra agent --extra ui
uv run pytest
uv run pre-commit install
```

[GRAPH_SCHEMA.md](public_docs/GRAPH_SCHEMA.md) describes what you will find in the knowledge graph, with
Cypher recipes. See [CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue.

## License

[AGPL 3.0](LICENSE). Use it and change it freely, at home or at work. If you distribute it or
run it as a service for others, your version must be published under the same license. The name
"portia" and the logo are not part of the license.
