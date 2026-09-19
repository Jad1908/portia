# Installing portia: instructions for an agent

You are an AI coding agent and a person has asked you to install portia. Follow this file.
A person can follow it too. Every command here is meant to be run as written, and each step
says how to check it worked before you move on.

Two rules hold for the whole install.

- **Never ask for a password, a token or a key, and never accept one pasted into the chat.**
  A secret typed into a conversation is in its transcript. portia is built so that it never
  needs one from you, and step 4 shows how a warehouse is reached without it.
- **Ask before you change a file that is not portia's.** That includes the user's shell
  profile, their Claude Code settings and their warehouse connection files.

## 1. Ask which version they want

There are two, and they can live side by side.

| | The plugin | The app |
|---|---|---|
| What it is | portia's tools inside the user's own Claude Code session | portia's own window, with its own copilot |
| Pick it when | they already work in Claude Code and want portia there | they want the three-pane window, or no Claude Code |
| Where it runs | any folder that holds their data | a clone of this repository |
| Who drives the model | Claude Code, on the user's own sign-in | the Claude Agent SDK, with `ANTHROPIC_API_KEY` or a local model |
| Can the model read raw data files | its file tools are refused on indexed data; a shell command is not | no, it has no file access at all |

If they do not say, ask. If they say "inside Claude Code", "as a plugin" or "headless", it is
the plugin. If they say "the app", "the UI" or "the window", it is the app.

Then ask where their data is, because it decides one install option:

- files on disk, CSV or Parquet: nothing extra
- Snowflake: the `snowflake` extra
- BigQuery: the `bigquery` extra

## 2. Check what is already there

```bash
uv --version          # required by both versions
claude --version      # required by the plugin only
docker --version      # optional: only the knowledge graph needs it
```

If `uv` is missing, stop and ask the user to install it from <https://docs.astral.sh/uv/>, or
ask their permission to run its installer. Python itself is not a prerequisite: `uv` fetches
3.11 or newer by itself.

## 3a. The plugin

**Install the commands.** Add `snowflake` or `bigquery` inside the brackets if step 1 said so.

```bash
uv tool install "portia[agent,ui,graph] @ git+https://github.com/Jad1908/portia"
```

This is a large install, about 450 MB, because it includes the window and the warehouse
drivers. It puts three commands on the PATH. Check all three:

```bash
portia --help         # lists: ui, build, connect, index, journal, ...
portia-mcp --help     # the tool server Claude Code will start
portia-hook --help    # the two rules Claude Code will run
```

If a command is not found, the tool folder is not on the PATH. `uv tool dir --bin` prints
the folder. `uv tool update-shell` adds it to the shell profile: that edits the user's
profile, so ask first, and tell them to open a new terminal afterwards.

**Add the plugin to Claude Code.** These are shell commands. You do not need the user to
type anything inside Claude Code.

```bash
claude plugin marketplace add Jad1908/portia
claude plugin install portia@portia
claude plugin list    # expect: portia@portia, enabled
```

**Tell the user to restart Claude Code**, or to start a new session. A running session does
not pick a new plugin up. In the new session `/mcp` should show `portia` as connected.

**Two settings to mention.**

- On Haiku, Claude Code must be started with `ENABLE_TOOL_SEARCH=false`. Claude Code loads a
  plugin's tools on demand by default, and Haiku finds portia's tools that way and never
  calls them. Sonnet and Opus work as they are.
- The first time each portia tool is used Claude Code asks permission. The read-only tools
  are safe to allow always. The four that write (`record_step`, `record_finding`,
  `set_interpretation`, `set_group`) are worth leaving on "ask", because each one changes a
  file the user will review.

## 3b. The app

```bash
git clone https://github.com/Jad1908/portia.git
cd portia
uv sync --extra ui --extra agent --extra graph    # add --extra snowflake or --extra bigquery
```

The knowledge graph is optional and needs Docker. Without it everything else works and one
tool, `graph_lookup`, says the graph is unavailable.

```bash
export NEO4J_PASSWORD=portia-dev
docker compose up -d neo4j
```

The app's copilot needs a model. Ask the user which they use, and do not ask for the key
itself: they set `ANTHROPIC_API_KEY` in their own shell, or they pick a local model in the
window (Ollama or llama.cpp, see the README's "Local models").

Start it and check it answers:

```bash
uv run portia ui --no-show &
sleep 6 && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/    # expect 200
```

Then stop that background process and tell the user to run `uv run portia ui` themselves.
If port 8080 is taken, add `--port 8090`.

Inside the clone every command below is `uv run portia ...`. With the plugin it is `portia ...`.

## 4. Connect the data

**Files.** Nothing to connect. portia reads CSV and Parquet in place, from inside the
project folder. Data that lives elsewhere is copied in with `portia import_data`.

**A warehouse.** portia signs in with what is already on the user's machine, and these
commands print no secret:

```bash
portia connect suggest                     # what their own tools already describe
portia connect add <name> --auth <how>     # save one for portia
portia connect test <name>                 # open a session and say who you are
```

- **Snowflake.** Prefer `--auth file`: it signs in the way the user's own
  `connections.toml` entry says, by the entry's name, and portia is handed no secret.
  `--auth browser` is their company sign-in in a browser window. `--auth password` and
  `--auth token` need something typed and only work in the app's window, never from
  Claude Code.
- **BigQuery.** The default uses the Google Cloud sign-in already on the machine
  (`gcloud auth application-default login`, which the user runs themselves).

If `connect suggest` prints a line starting with `note:`, read it to the user. The two it
knows are both about Snowflake's file. A section written `[connections.name]` is
`config.toml`'s form, and in `connections.toml` it has to be `[name]`. And a file other
users can read needs `chmod 0600`. Both are the user's file, so ask before you change it.

Tell the user which role the connection signs in with, which `connect test` prints. On a
warehouse the role is the only limit on what a session can do.

## 5. Make the first project

Run this in the folder that holds the data. For the app, it can be any folder, and the
window can do the same thing with buttons.

portia needs a few sentences about the project before it reads any data. **Ask the user
for them and pass their words**: the domain and the goal, what they produce and at what
grain, and roughly what data they have. Do not write this yourself from the file names.

```bash
portia index <file, folder or glob> --init "<their description>" --no-interpret
```

`--no-interpret` measures each file and makes no model call. On a warehouse the project is
pointed at the connection and tables are brought in by name, as metadata, with no scan:

```bash
portia index --init "<their description>"    # no files to index: this only describes the project
portia connect use <name>
portia connect browse <name> [DATABASE[.SCHEMA]]
portia connect scope DATABASE.SCHEMA.TABLE [more tables]
```

A profile of a warehouse table is a full scan and costs the user money. Never start one
without asking, and name the table when you ask.

## 6. Check the whole thing

| Check | Expect |
|---|---|
| `portia --help` | a list of commands |
| `ls .portia/sources` in the project | one file per source |
| plugin: `claude plugin list` | `portia@portia`, enabled |
| plugin: `/mcp` in a new Claude Code session | `portia` connected |
| plugin: ask "what data do we have here?" | Claude loads the `portia` skill and calls `get_context` first |
| app: `curl` on the window's address | `200` |

With the plugin, a chart Claude draws goes to portia's window. `portia ui --project .`
opens it, and with the window shut Claude says the chart is waiting.

## 7. When something goes wrong

- **`portia: command not found`.** The tool folder is not on the PATH. See step 3a.
- **Claude never calls a portia tool and reaches for the shell.** The model is Haiku. Start
  Claude Code with `ENABLE_TOOL_SEARCH=false`.
- **Claude says nothing is indexed, and `.portia/sources` has files.** The install is older
  than the fix for it. `uv tool upgrade portia`.
- **`graph_lookup` says the graph is not reachable.** Neo4j is not running or
  `NEO4J_PASSWORD` is not set. Everything else works without it.
- **A tool is refused with "signs in with a password".** That connection needs something
  typed and Claude Code has nowhere to type it. Use `--auth file` or `--auth browser`.
- **`Invalid connection_name` from Snowflake.** The entry's header is in `config.toml`'s
  form. `portia connect suggest` says so.
- **A long profile is cut off.** Claude Code gives up on a tool after a time limit.
  `MCP_TOOL_TIMEOUT`, in milliseconds, raises it.

## 8. Removing it

```bash
claude plugin uninstall portia@portia
claude plugin marketplace remove portia
uv tool uninstall portia
```

A project's `.portia/` folder, its `specs/`, `models/`, `findings/` and `figures/` are the
user's work. Leave them unless they ask.
