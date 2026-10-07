# Running SWE-bench in Docker with a Custom OpenAI-Compatible Endpoint

# trace-simulations

## Conda environment (set this up first)

The helper scripts in this folder (`pick_instances.py`, `build_telemetry.py`) run in a
dedicated Anaconda environment called **`trace-sims`**, kept separate from the SWE-bench
harness itself (which runs inside the Docker container built in step 2). Dependencies are
pinned in [`requirements.txt`](./requirements.txt).

Create it once:

```bash
conda create -y -n trace-sims python=3.11
conda activate trace-sims
pip install -r trace-simulations/requirements.txt
```

Then run the helper scripts with that environment active:

```bash
conda activate trace-sims
python trace-simulations/pick_instances.py        # choose instances by difficulty
python trace-simulations/build_telemetry.py ...    # build graph-friendly telemetry
```

What's in it and why:

| Package | Used by | Purpose |
| --- | --- | --- |
| `datasets` | `pick_instances.py` | Read SWE-bench Verified and its `difficulty` field |
| `pandas` | README analysis snippets | Load `events.jsonl` into a DataFrame |
| `networkx` | README analysis snippets | Load `graph.graphml` for graph analysis |

`build_telemetry.py` itself uses only the Python standard library, so once the env exists
it needs nothing else. (Verified with Python 3.11: `datasets` 5.0.1, `pandas` 3.0.6,
`networkx` 3.6.1.)

> **Scope:** this env is only for the analysis/helper scripts. The actual `swebench infer`
> and `swebench eval` commands run inside the Docker container, not here.

---

This is a friendly, copy-paste guide for running a small SWE-bench study end to end:

1. Build a Docker container that has SWE-bench installed.
2. Point SWE-bench's inference at a **custom OpenAI-compatible endpoint** (here: ASU CreateAI).
3. Generate model patches for **4 instances, one per difficulty label** (`<15 min fix`,
   `15 min - 1 hour`, `1-4 hours`, `>4 hours`), then grade them.
4. Capture **detailed agentic telemetry** of every step the agent takes and store it in
   an easy-to-access, graph-friendly layout.
5. Use the resulting trajectories + patches + telemetry for a human evaluation study.

Two small helper scripts live next to this README:

- `pick_instances.py` — selects Verified instances by difficulty tier.
- `build_telemetry.py` — turns the agent's raw trajectories into graph-ready telemetry.

> **Why a container?** SWE-bench itself still needs Docker to build each task's test
> environment. The container we build here is a *control* container: it holds the
> SWE-bench CLI and your Python env, and it talks to the Docker daemon on your host to
> build/run the per-task images. This keeps your host Python clean and makes the setup
> reproducible.

---

## 0. What you actually need (the honest requirements)

The repo's README warns about **120 GB**. That number is **disk space, not RAM**.

| Resource | Full benchmark (2,294 tasks) | This 4-instance study |
| --- | --- | --- |
| Free disk | ~120 GB | ~20–40 GB is plenty |
| RAM | 16 GB recommended | 16 GB is comfortable; 8 GB works for 4 tasks |
| CPU | 8+ cores | 2–4 cores is fine |
| Docker | required | required |

Because we only run 4 instances, you do **not** need 120 GB. Keep `--cache_level env`
(the default) and few workers, and the transient disk use stays small.

You will need:

- **Docker** installed and running ([install guide](https://docs.docker.com/engine/install/)).
- A **CreateAI token** (used as the API key). See your CreateAI Token Details page.
- This SWE-bench checkout (the folder two levels up from this file).

---

## 1. Set up your endpoint credentials

SWE-bench runs inference through [mini-SWE-agent](https://github.com/SWE-agent/Mini-SWE-Agent),
which uses [litellm](https://docs.litellm.ai/) under the hood. litellm talks to any
OpenAI-compatible gateway when you:

- prefix the model name with `openai/`, and
- set `OPENAI_API_BASE` to the gateway URL and `OPENAI_API_KEY` to your token.

CreateAI's base URL already ends in `/v1`, and litellm's OpenAI client appends
`/chat/completions` for you, so **do not** add anything after `/v1`.

Create a file named `.env` in the repo root (one level up from this folder). It is
already gitignored, so your token won't be committed:

```bash
# ---- CreateAI (OpenAI-compatible) ----
# Pick the environment base URL you were given:
#   Production: https://api-main.aiml.asu.edu/v1
#   Beta:       https://api-main-beta.aiml.asu.edu/v1
#   POC:        https://api-main-poc.aiml.asu.edu/v1
OPENAI_API_BASE=https://api-main-poc.aiml.asu.edu/v1
OPENAI_API_KEY=YOUR_CREATEAI_TOKEN
```

The model name you'll pass to SWE-bench is the CreateAI model in `provider/model_name`
form, with litellm's `openai/` prefix in front. For example, CreateAI's `openai/gpt4o`
becomes **`openai/openai/gpt4o`** to litellm (first `openai/` = "use the OpenAI-style
client", second = CreateAI's provider path).

> **Service-token shortcut:** if you have a CreateAI *service* token, you can use the
> model name `defaults` to use your project's configured model. With litellm that is
> `openai/defaults`.

Common CreateAI model names (from the API docs):

| What you want | litellm model string |
| --- | --- |
| GPT-4o | `openai/openai/gpt4o` |
| GPT-4.1 | `openai/openai/gpt4_1` |
| GPT-5 Mini | `openai/openai/gpt5_mini` |
| Claude 4 Sonnet | `openai/aws/claude4_sonnet` |
| Project default (service token) | `openai/defaults` |

### Quick sanity check (optional but recommended)

Before building anything, confirm your token + base URL actually work. Pick the version
that matches your shell.

**PowerShell (Windows / Kiro shell).** Windows' `curl` is an alias for
`Invoke-WebRequest`, which does *not* accept `curl`'s `-X`/`-H`/`-d` flags, and bash's
`\` line-continuations and `$VAR` syntax don't apply. Use `Invoke-RestMethod` instead:

```powershell
# set these for the session (or read them from your .env, see below)
$base = "https://api-main-poc.aiml.asu.edu/v1"
$key  = "YOUR_CREATEAI_TOKEN"

$body = @{
    model      = "openai/gpt4o"
    messages   = @(@{ role = "user"; content = "ping" })
    max_tokens = 16
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri "$base/chat/completions" `
    -Headers @{ Authorization = "Bearer $key" } `
    -ContentType "application/json" `
    -Body $body
```

To pull the values straight from the `.env` file instead of pasting them:

```powershell
Get-Content .env | Where-Object { $_ -match '^\s*OPENAI_API_(BASE|KEY)=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    Set-Item "env:$($name.Trim())" $value.Trim()
}
$base = $env:OPENAI_API_BASE
$key  = $env:OPENAI_API_KEY
```

If the call succeeds you'll get an object with a `choices` property.

**bash / zsh (Linux, macOS, or inside the container).** Here real `curl` is available
and the `.env` values can be exported first:

```bash
set -a; source <(tr -d '\r' < .env); set +a
curl -X POST "$OPENAI_API_BASE/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt4o","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
```

A JSON response with a `choices` array means you're good to go.

> If you want `curl`'s exact behavior in PowerShell, call the real binary explicitly as
> `curl.exe` (it ships with Windows 10+). Then the bash flags work, but you still need
> PowerShell line-continuation (backtick `` ` ``) and `$env:VAR` for variables.

---

## 2. Build the SWE-bench control container

Create a `Dockerfile` inside this `trace-simulations/` folder:

```dockerfile
# trace-simulations/Dockerfile
FROM python:3.11-slim

# Docker CLI + git are needed so SWE-bench can drive the host Docker daemon and
# check out task repos. Use `docker-cli` (just the client), NOT `docker.io`: on Debian 13
# (trixie, which python:3.11-slim now tracks) `docker.io` no longer ships the `docker`
# binary at /usr/bin/docker, so the harness fails with "No such file or directory: 'docker'".
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates docker-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/SWE-bench

# Copy the repo in and install SWE-bench + mini-SWE-agent (the inference agent).
COPY . /opt/SWE-bench
RUN pip install --no-cache-dir -e . \
    && pip install --no-cache-dir mini-swe-agent

# mini-SWE-agent runs inference; the harness drives Docker on the host.
CMD ["bash"]
```

Build it from the **repo root** (so the whole checkout is in the build context):

```bash
# from the SWE-bench repo root (one level above this folder)
docker build -f trace-simulations/Dockerfile -t swebench-runner .
```

Then start it, mounting the host Docker socket and a logs folder so results land back
on your machine:

```bash
docker run -it --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(pwd)/trace-simulations/logs:/opt/SWE-bench/logs" \
  --env-file .env \
  swebench-runner
```

> **Windows (Docker Desktop):** use `-v //var/run/docker.sock:/var/run/docker.sock` and
> run the command from PowerShell with `${PWD}` instead of `$(pwd)`. Make sure Docker
> Desktop's disk image size (Settings → Resources) is large enough for a few task
> images.

Everything from here runs **inside** that container shell.

---

## 3. Pick 4 instances, one per difficulty label

We use **SWE-bench Verified** because every task was confirmed solvable by professional
engineers, so a "wrong" model patch is genuinely wrong, not an artifact of a bad task.
Verified also ships an **expert-annotated `difficulty`** field with four natural labels,
estimating how long a human engineer would need. We pick **one instance per label** so
the study covers the full range:

#### Initial AI Selections

| `difficulty` label | Instance ID | Repo | Why this one |
| --- | --- | --- | --- |
| `<15 min fix` | `django__django-11099` | django/django | Two-line username-validator regex fix; a rater verifies it in a minute. |
| `15 min - 1 hour` | `astropy__astropy-14995` | astropy/astropy | Focused mask-propagation bug; a moderate, single-area change. |
| `1-4 hours` | `django__django-13401` | django/django | Multi-method change with real design nuance; a substantial but bounded fix. |
| `>4 hours` | `sympy__sympy-18835` | sympy/sympy | Deep, multi-step reasoning across the codebase; the kind of task models usually fail. |

#### Varun's Selections

<15 min fix: `scikit-learn__scikit-learn-13496`
15 min - 1 hour: `django__django-14238`
1-4 hours: `astropy__astropy-14369`
>4 hours: `pydata__xarray-6992`

#### Disclaimer

These IDs are convenient defaults, but difficulty labels and solvability shift between
dataset revisions, so **don't just trust this list** — confirm against the dataset you
actually loaded. `pick_instances.py` does exactly that: it reads the `difficulty` field
and prints a real candidate for each of the four labels.

Run it in the `trace-sims` env (see the top of this README):

```bash
conda activate trace-sims

# one instance per difficulty label, plus a ready-to-paste --filter regex
python trace-simulations/pick_instances.py

# more choices, or a single label
python trace-simulations/pick_instances.py --per-tier 5
python trace-simulations/pick_instances.py --tier ">4 hours"

# just how many tasks sit in each label
python trace-simulations/pick_instances.py --count-only
```

The rest of this guide uses the four recommended IDs above. If you pick different ones,
substitute them everywhere (or copy the `--filter` regex the script prints).

> **Why one per label?** A human study is far more informative when it spans the whole
> difficulty range: the `<15 min` task shows whether the agent is clean and minimal on a
> trivial fix, the middle two probe real debugging and design judgment, and the `>4 hours`
> task exposes where and how the agent breaks down. The telemetry in step 7 makes those
> failure modes visible.

---

## 4. Verify your setup with the gold patches

Before spending tokens, confirm the Docker harness can build and grade these tasks
using the reference ("gold") patches. This also pre-builds the task images.

```bash
swebench eval verified --gold \
  -i django__django-11099 \
  -i astropy__astropy-14995 \
  -i django__django-13401 \
  -i sympy__sympy-18835 \
  --run-id gold-check \
  -j 2
```

If all four report **resolved**, your Docker + harness setup is correct.

### Varun's selections

The four instances chosen for this study, one per difficulty label:

| `difficulty` label | Instance ID | Repo |
| --- | --- | --- |
| `<15 min fix` | `scikit-learn__scikit-learn-13496` | scikit-learn/scikit-learn |
| `15 min - 1 hour` | `django__django-14238` | django/django |
| `1-4 hours` | `astropy__astropy-14369` | astropy/astropy |
| `>4 hours` | `pydata__xarray-6992` | pydata/xarray |

```bash
swebench eval verified --gold \
  -i scikit-learn__scikit-learn-13496 \
  -i django__django-14238 \
  -i astropy__astropy-14369 \
  -i pydata__xarray-6992 \
  --run-id gold-check-varun \
  -j 2
```

> On Apple Silicon / ARM, add `--task-repo ./swe-bench-tasks` after cloning the task
> repo locally (`git clone --depth 1 https://github.com/SWE-bench/swe-bench-tasks.git`),
> so images build locally with Buildx.

---

## 5. Generate model patches via the custom endpoint

Now run inference with mini-SWE-agent against your CreateAI endpoint. The `.env` you
created is loaded automatically.

> **Register your model's price for custom CreateAI models.** After each model call,
> mini-SWE-agent asks litellm to compute the dollar cost from its built-in price table.
> Custom CreateAI models (e.g. `openai/gpt5_6_luna`) aren't in that table, so the lookup
> throws and mini treats it as fatal — every instance dies with
> `RuntimeError: ... This model isn't mapped yet`, *even though the model call itself
> succeeded*. The fix below points `LITELLM_MODEL_REGISTRY_PATH` at
> [`model_prices.json`](./model_prices.json), which registers your model's per-token
> rates so cost tracking produces **real numbers**. Keep that file updated with the
> model's actual pricing (litellm fields are per-token: `$0.20/M` → `input_cost_per_token:
> 2e-7`, `$1.20/M` → `output_cost_per_token: 1.2e-6`).
>
> Quick fallback: if you don't care about cost, `export MSWEA_COST_TRACKING=ignore_errors`
> instead, which skips the cost step (reported as `0.0`). Models already in litellm's
> table (like `openai/gpt4o`) need neither.

Mount this folder this way to avoid any headaches:

```bash
docker run -it --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(pwd)/trace-simulations:/opt/SWE-bench/trace-simulations" \
  -v "$(pwd)/trace-simulations/logs:/opt/SWE-bench/logs" \
  --env-file .env \
  swebench-runner
```

```bash
export LITELLM_MODEL_REGISTRY_PATH=/opt/SWE-bench/trace-simulations/model_prices.json

swebench infer verified \
  -m openai/openai/gpt5_6_luna \
  --run-id createai-gpt5_6_luna \
  -o /opt/SWE-bench/logs/preds \
  -w 1 \
  --filter "scikit-learn__scikit-learn-13496|django__django-14238|astropy__astropy-14369|pydata__xarray-6992"
```

What each part does:

- `export LITELLM_MODEL_REGISTRY_PATH=...` — registers your model's price (see the box
  above) so litellm can track cost instead of erroring out.
- `verified` — the dataset (SWE-bench Verified).
- `-m openai/openai/gpt5_6_luna` — litellm `openai/` prefix + CreateAI's
  `openai/gpt5_6_luna`. Swap in any model from the table in step 1 (e.g. `openai/defaults`
  for a service token); if it's a custom model, add its price to `model_prices.json`.
- `-o .../logs/preds` — where `preds.json` + per-task trajectories are written.
- `-w 1` — one task at a time (bump up if your endpoint allows concurrency).
- `--filter` takes a regex, so we OR the four instance IDs together to run just those.

Tip: add `--dry-run` to see the exact command without executing it.

This writes an output directory (`logs/preds/createai-gpt4o/`) that is the raw material
for both grading and telemetry:

```
logs/preds/createai-gpt4o/
├── preds.json                                 # instance_id -> {model_name_or_path, instance_id, model_patch}
├── minisweagent.log                           # run-wide log of the whole batch
├── exit_statuses_<timestamp>.yaml             # per-instance exit status
├── django__django-11099/
│   └── django__django-11099.traj.json         # full trajectory: every message + action + cost
├── astropy__astropy-14995/
│   └── astropy__astropy-14995.traj.json
├── django__django-13401/
│   └── django__django-13401.traj.json
└── sympy__sympy-18835/
    └── sympy__sympy-18835.traj.json
```

Each `*.traj.json` is the complete agentic record for one instance: the system prompt,
the task, every assistant turn (with the shell commands it ran), every observation, the
final patch, and run stats like token cost and API-call count. Step 7 turns these into
graph-friendly telemetry.

---

## 6. Grade the model's patches

Point the evaluator at the predictions file from step 5:

```bash
swebench eval verified \
  -p /opt/SWE-bench/logs/preds/gpt5_6_luna/preds.json \
  --run-id createai-gpt5_6_luna \
  -i astropy__astropy-14369 \
  -i django__django-14238 \
  -i pydata__xarray-6992 \
  -i scikit-learn__scikit-learn-13496 \
  -j 2
```

Results land in `logs/evaluation/createai-gpt5_6_luna/results.json` (resolved vs.
unresolved per instance), and because you mounted `logs/`, they're on your host too. Keep
this path handy — step 7 folds the resolved/unresolved verdict into the telemetry.

> **Caching note:** results are cached by `run_id` + `instance_id`. If you change the
> model or the patches, use a **new** `--run-id`, or the harness will reuse old results.

---

## 7. Build detailed agentic telemetry (for graph visualization)

The raw `*.traj.json` files hold everything, but they're awkward to analyze or graph
directly. `build_telemetry.py` normalizes them into a stable, documented schema that
downstream researchers can load straight into graph tools. Run it in the `trace-sims`
env (see the top of this README):

```bash
conda activate trace-sims
python trace-simulations/build_telemetry.py \
  --preds   trace-simulations/logs/preds/gpt5_6_luna \
  --out     trace-simulations/logs/telemetry/gpt5_6_luna \
  --results trace-simulations/logs/evaluation/createai-gpt5_6_luna/results.json
```

`--preds` is the mini-SWE-agent output dir from step 5 (it holds `preds.json` and the
per-instance `*.traj.json` files). `--results` is optional; include it to fold the
resolved/unresolved verdict from step 6 into each instance's record.

> **Note on the trajectory format.** mini-SWE-agent v2 (what the container ships) uses
> OpenAI tool-calling, so an `assistant` turn issues a shell command as a tool call and
> the output comes back as a `tool`-role message. `build_telemetry.py` understands this —
> it treats `tool` messages as observations and links each command to its output. (The
> cost fields are populated because you registered the model price in step 5; without
> that they'd be `0.0`.)

### What it produces

Everything lands under one easy-to-access directory, one folder per instance plus a
run-level manifest. This is the real output from the `gpt5_6_luna` run:

```
trace-simulations/logs/telemetry/gpt5_6_luna/
├── manifest.json                       # one row per instance: ids, model, exit_status,
│                                        # cost, step/action counts, resolved verdict, paths
├── astropy__astropy-14369/
│   ├── events.jsonl                     # one JSON object per agent step, in order
│   ├── graph.json                       # {"nodes": [...], "edges": [...]} directed graph
│   └── graph.graphml                    # same graph as GraphML (Gephi / Cytoscape / networkx)
├── django__django-14238/
│   └── ...
├── pydata__xarray-6992/
│   └── ...
└── scikit-learn__scikit-learn-13496/
    └── ...
```

A `manifest.json` row from that run looks like this (one per instance):

```json
{
  "instance_id": "astropy__astropy-14369",
  "model_name_or_path": "openai/openai/gpt5_6_luna",
  "exit_status": "Submitted",
  "resolved": true,
  "n_steps": 28,
  "n_actions": 12,
  "api_calls": 14,
  "instance_cost": 0.0535394,
  "mini_version": "2.4.6",
  "trajectory_format": "mini-swe-agent-1.1",
  "artifacts": { "events": "...", "graph_json": "...", "graphml": "..." }
}
```

### The graph model (what downstream researchers get)

Each trajectory becomes a **directed graph** of the agent's loop:

- **Nodes** are agent steps. Every node carries `step_index`, `role`
  (`system` / `user` / `assistant` / `tool` / `exit`), `kind` (`system` / `observation` /
  `thought` / `action` / `exit`), a truncated `text` preview, `full_text_len`, the shell
  `actions` for action steps, `n_actions`, `cost`, `returncode` (for tool observations),
  and `is_submission`.
- **Edges** come in two types:
  - `next` — step *N* → step *N+1*, the temporal flow of the whole run.
  - `acts_on` — an `action` step → the `tool` observation it produced, linking a command
    to its result.

That's enough to render the full think → act → observe loop as a graph and to compute
derived metrics: steps-to-solution, action counts, cost per step, where a run stalls or
loops, and how the shape differs across difficulty labels. In the `gpt5_6_luna` run the
graphs ranged from 22 steps / 10 actions (`pydata__xarray-6992`) to 30 steps / 16 actions
(`scikit-learn__scikit-learn-13496`).

### Loading the graph

Any GraphML-aware tool opens `graph.graphml` directly — [Gephi](https://gephi.org/),
[Cytoscape](https://cytoscape.org/), or networkx:

```python
import networkx as nx
g = nx.read_graphml(
    "trace-simulations/logs/telemetry/gpt5_6_luna/astropy__astropy-14369/graph.graphml"
)
print(g.number_of_nodes(), g.number_of_edges())   # e.g. 28 38
```

Prefer your own pipeline? `graph.json` is a plain nodes/edges document, and
`events.jsonl` is a flat per-step log that drops straight into pandas:

```python
import pandas as pd
events = pd.read_json(
    "trace-simulations/logs/telemetry/gpt5_6_luna/astropy__astropy-14369/events.jsonl",
    lines=True,
)
events.groupby("kind")["cost"].sum()   # cost by step kind
```

> **Where telemetry lives:** by default under `logs/telemetry/<run-id>/`, which is on
> the host via the volume mount from step 2. Point `--out` anywhere you like (a shared
> drive, a dataset staging folder) to make it even easier for collaborators to grab.

---

## 8. What you hand to human raters

For each of the 4 instances (`scikit-learn__scikit-learn-13496`, `django__django-14238`,
`astropy__astropy-14369`, `pydata__xarray-6992`), give raters:

1. **The issue text** — from the dataset (`problem_statement` field).
2. **The model's patch** — from `preds.json` (the `model_patch` field for that instance).
3. **The telemetry** — `events.jsonl` (readable step-by-step log) and `graph.graphml` /
   `graph.json` (for visualizing the agent's path), from step 7.
4. **The gold patch** — the reference fix (from the dataset's `patch` field).
5. **The automated verdict** — resolved/unresolved, surfaced in `manifest.json`.

Because the four tasks span the full difficulty range (`<15 min fix` → `>4 hours`), a
useful rubric looks at both outcome and process: *Is the patch correct? Is it minimal?
Is the reasoning in the trajectory sound? Where did the agent spend its steps?* The graph
telemetry makes that last question concrete — raters can line up the per-instance step and
action counts (e.g. the harder `scikit-learn` task took 30 steps / 16 actions vs. 22 / 10
for `pydata__xarray`) and literally see where a run looped or stalled.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `401 / 403` from the endpoint | Token wrong or expired, or wrong environment base URL. Re-check `.env`. |
| litellm says model not found | Make sure the model is double-prefixed: `openai/<provider>/<name>` (e.g. `openai/openai/gpt4o`). |
| `Cannot connect to the Docker daemon` | The socket mount is missing. Re-add `-v /var/run/docker.sock:/var/run/docker.sock`. |
| `FileNotFoundError: ... 'docker'` inside the container | The `docker` client isn't on PATH. Make sure the Dockerfile installs `docker-cli` (not `docker.io`) and rebuild the image. |
| `RuntimeError: ... This model isn't mapped yet` during `infer` | litellm can't price your custom model. Add its rates to `model_prices.json` and `export LITELLM_MODEL_REGISTRY_PATH=...` (or `export MSWEA_COST_TRACKING=ignore_errors` to skip cost). The model call actually worked; only cost tracking failed. |
| Out of disk during eval | Keep `--cache_level env`, lower `-j`, or run `docker system prune -a` between runs. |
| URL has `/v1/v1/...` in errors | You added a path after `/v1`. `OPENAI_API_BASE` must end at `/v1` with nothing after it. |
| Results don't update after a change | Use a fresh `--run-id` (results are cached per run_id + instance_id). |
| `build_telemetry.py` finds no trajectories | Point `--preds` at the inference output dir (the one holding `preds.json` and the per-instance folders), and make sure inference finished. |
| `pick_instances.py` / `build_telemetry.py` import error | Activate the `trace-sims` env first (`conda activate trace-sims`); it has `datasets`, `pandas`, and `networkx`. |

---

## Command cheat sheet

```bash
# pick one instance per difficulty label + print a --filter regex
python trace-simulations/pick_instances.py

# verify harness + Docker with gold patches
swebench eval verified --gold -i django__django-11099 -i astropy__astropy-14995 -i django__django-13401 -i sympy__sympy-18835 -r gold-check -j 2

# generate patches via CreateAI (register the model's price for real cost tracking)
export LITELLM_MODEL_REGISTRY_PATH=/opt/SWE-bench/trace-simulations/model_prices.json
swebench infer verified -m openai/openai/gpt5_6_luna -r createai-gpt5_6_luna -o logs/preds -w 1 \
  -- --filter "scikit-learn__scikit-learn-13496|django__django-14238|astropy__astropy-14369|pydata__xarray-6992"

# grade the generated patches
swebench eval verified -p logs/preds/gpt5_6_luna/preds.json -r createai-gpt5_6_luna \
  -i scikit-learn__scikit-learn-13496 -i django__django-14238 -i astropy__astropy-14369 -i pydata__xarray-6992 -j 2

# build graph-friendly telemetry from the trajectories (run in the trace-sims env)
python trace-simulations/build_telemetry.py --preds trace-simulations/logs/preds/gpt5_6_luna \
  --out trace-simulations/logs/telemetry/gpt5_6_luna \
  --results trace-simulations/logs/evaluation/createai-gpt5_6_luna/results.json

# re-grade saved logs without rebuilding containers
swebench report createai-gpt5_6_luna -d verified
```
