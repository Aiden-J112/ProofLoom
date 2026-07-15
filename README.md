# ProofLoom

ProofLoom is a local-first, evidence-grounded AI learning workspace. It turns
trusted Markdown into a human-reviewed Query Graph whose relationships trace
back to located Source Fragments.

ProofLoom v0.1 provides the complete Build workflow and a lightweight Explore
workflow. The Assertion Ledger and its Review Events are authoritative; the
Query Graph is a projection containing only current accepted assertions.

## Requirements

- Python 3.11 or newer
- A local web browser

The bundled synthetic workflow uses no network service and requires no API key.

## Install and start from a clean checkout

PowerShell or Command Prompt (the first command assumes `python` is Python 3.11+):

```console
python -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\python.exe -m proofloom.app --browse-root .
```

POSIX shells:

```console
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m proofloom.app --browse-root .
```

Open `http://127.0.0.1:8000`. Stop the server with `Ctrl+C`.

The corresponding installed console commands are
`.venv\Scripts\proofloom.exe --browse-root .` on Windows and
`.venv/bin/proofloom --browse-root .` on POSIX.

When ProofLoom is already installed and its scripts directory is on `PATH`, the
short equivalent is:

```console
proofloom --browse-root .
```

ProofLoom binds only to a loopback address. `--browse-root` limits every project
and source path available to the local interface. Use `--port PORT` to select a
different port.

For editable development installs, use `python -m pip install -e .`.

## Run the original two-document synthetic workflow

The repository includes two small original documents in
`examples/synthetic-workflow/` and matching offline extraction recipes in the
installed package data. The workflow is deterministic apart from generated
local IDs and timestamps.

1. Create an empty `demo-project` directory beneath the checkout.
2. Start ProofLoom with the venv interpreter command from the install section:
   `.venv\Scripts\python.exe -m proofloom.app --browse-root .` on Windows or
   `.venv/bin/python -m proofloom.app --browse-root .` on POSIX.
3. In **Create a Knowledge Project**, choose `demo-project`, name it, and create
   the project.
4. Import the `examples/synthetic-workflow` directory. The project page displays
   located Source Fragments from both `inspection.md` and `safety.md`.
5. Open **Review Entity Dictionary**. Submit and accept these controlled entities:

   | Name | Type |
   | --- | --- |
   | Inspector | Component |
   | Inspection Report | Artifact |
   | Safety Gate | Component |
   | Risky Command | Artifact |

6. Open **Extract Candidate Assertions** and select **Run offline synthetic
   fixture extraction**. Both candidates must show `valid` in Validation output.
7. Read each proposed relationship beside its evidence, then select **Accept**
   for both candidates.
8. Select **Project and explore graph**. Explore the two relationships and use
   each **Trace evidence** link to view its Assertion ID, accepted status, source
   file, heading path, and original passage.
9. In another terminal, run the public release-integrity check:

   ```console
   .venv\Scripts\python.exe -m proofloom.app check demo-project
   ```

   On POSIX use `.venv/bin/python -m proofloom.app check demo-project`. The
   installed entry points are `.venv\Scripts\proofloom.exe check demo-project`
   on Windows and `.venv/bin/proofloom check demo-project` on POSIX.

The project writes governed local state beneath `demo-project/.proofloom/` and
adds `.proofloom/` to that project's `.gitignore`. Generated Knowledge Project
data is user-local and is not part of the repository.

## Interface flow

Build:

```text
Create/open Knowledge Project
  -> import UTF-8 Markdown
  -> review the closed Entity Dictionary
  -> extract candidates with fixture or configured API
  -> inspect validation and located evidence
  -> accept, reject, replace, or mark needs domain review
  -> project the Query Graph
```

Explore:

```text
Filter entities/relationships
  -> select a displayed edge
  -> resolve assertion_id through the Assertion Ledger
  -> inspect the current accepted state and Source Fragment passage
```

Changing an imported passage marks its old Source Fragment `changed`. An
accepted assertion referencing changed or missing evidence becomes `stale` and
is withdrawn from the next Query Graph projection. Rejected, replaced,
unreviewed, and `needs_domain_review` assertions are also excluded.

## Release-integrity command

```console
proofloom check PROJECT_PATH
```

The command checks the persisted graph that users can explore. It does not trust
edge fields as proof. For every edge, it independently verifies that:

- `assertion_id` resolves exactly once in the Assertion Ledger;
- Review Events currently make that assertion accepted, not rejected, replaced,
  stale, or awaiting domain review;
- the assertion still passes schema, dictionary, predicate, and evidence checks;
- the edge endpoints and relationship match the governed assertion; and
- every primary and supporting Evidence Reference resolves to a located current
  Source Fragment with a source file, heading path, and passage.

The command exits non-zero and identifies the failing edge when integrity is not
established. Project the graph in the UI before running it.

## Optional OpenAI-compatible extraction

Fixture extraction is the default reproducible demonstration. API extraction is
optional and reads configuration only from the server process environment:

- `PROOFLOOM_OPENAI_API_KEY` (required)
- `PROOFLOOM_OPENAI_MODEL` (required)
- `PROOFLOOM_OPENAI_ENDPOINT` (optional complete chat-completions URL; defaults
  to `https://api.openai.com/v1/chat/completions`)
- `PROOFLOOM_OPENAI_PROVIDER` (optional provenance label; defaults to `openai`)

`PROOFLOOM_OPENAI_BASE_URL` remains supported when `PROOFLOOM_OPENAI_ENDPOINT`
is unset; ProofLoom appends `/chat/completions`. The key is sent only in the
Authorization request header and is not stored in project files or rendered in
the UI. Plain HTTP endpoints are allowed only on localhost or a loopback address.

## Development checks

With the checkout installed (or with `PYTHONPATH=src`):

```console
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Schemas and offline fixtures are package data declared in `pyproject.toml`, so
they remain available when ProofLoom is installed outside the checkout.

## Scope and release decisions

v0.1 intentionally excludes authentication, cloud hosting, multi-user roles,
non-Markdown import, Neo4j, general plugin infrastructure, and learning or Q&A
agents. Code, schemas, prompts, and original/synthetic examples may be public;
third-party tutorial source material and user-local project data are not shipped.

ProofLoom is a working name. License selection and name, repository, package,
and trademark availability remain owner decisions required before a formal
public release.
