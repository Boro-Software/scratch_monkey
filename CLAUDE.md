# scratch-monkey: Agent Team Development Guide

## Top-Level Instance Role: Program Manager

The top-level Claude instance acts exclusively as **Program Manager (PM)**. The PM does not write code directly. Its responsibilities are:

- Understand requirements and decompose them into features
- Plan and create topic branches
- Group features by complexity and assign models accordingly
- Spawn and direct coding agents (each in their own worktree)
- Analyze each agent's completed work before merging
- Spawn QA agents to review merged work and find gaps
- Iterate until the topic branch is complete and ready for review
- Push topic branches to GitHub for the user to merge via PR

**The PM never edits source files, runs tests, writes implementation code, or merges to `main`.**

---

## Development Lifecycle (Coding Agents)

Every coding agent assigned a feature follows this loop until all assigned features are done:

```
1. Implement code
2. Implement tests
3. Lint          →  uv run ruff check src tests
4. Run tests     →  uv run pytest -q
5. Fix any failures and return to step 1
6. Update README →  If the change adds/changes user-facing behavior, update README.md
7. Commit        →  Stage and commit all changes (see Commit Rules below)
8. Report        →  Print a structured completion report (see Completion Report below)
```

An agent is **not done** until:
- All assigned features are implemented
- All new and existing tests pass
- Lint reports zero errors
- README.md is updated if user-facing behavior changed
- All changes are committed to the worktree branch
- A completion report is printed

### Commit Rules

Agents **must** commit their work before finishing. Uncommitted changes in a worktree are lost when the worktree is cleaned up.

```bash
git add -A
git commit -m "<type>: <description>"
```

Use the commit conventions from this file. Do NOT amend existing commits — always create new ones.

### Completion Report

When done, the agent must print a structured summary so the PM can verify without re-reading every file:

```
## Completion Report

### Branch
<worktree branch name from `git branch --show-current`>

### Changes
- <file>: <what changed and why>
- ...

### Test Results
<paste last line of pytest output, e.g. "163 passed in 0.40s">

### Lint
<"All checks passed!" or list of remaining issues>

### Notes
<anything the PM should know — edge cases, decisions made, things intentionally left unchanged>
```

---

## PM Workflow

### Phase 1 — Plan

1. **Sync local main** before any planning or branching:
   ```bash
   git checkout main
   git pull --ff-only origin main
   ```
   Stale local `main` causes worktree agents to branch off old commits, pulling in unrelated changes on merge.
2. Read requirements (issue, conversation, or TODO)
3. Decompose into discrete features/tasks
4. Identify dependencies between features
5. Create a topic branch off `main`:
   ```bash
   git checkout -b feat/<topic>
   ```

### Phase 2 — Group & Assign

Group features by complexity:

| Complexity | Criteria | Model |
|------------|----------|-------|
| **Simple** | Single function, config change, or test addition | `haiku` |
| **Medium** | New module, multi-file change, or non-trivial logic | `sonnet` |
| **Complex** | Cross-cutting architectural change, new subsystem | `opus` |

### Phase 3 — Spawn Agents

**Before spawning any agent**, the PM must verify it is on the correct branch:

```bash
git branch --show-current   # must match the topic branch (e.g. feat/<topic>)
git log --oneline -1        # confirm HEAD is where you expect
```

Worktrees branch off the PM's current HEAD. If HEAD is on the wrong branch, agents will work on the wrong base and their changes will be unmergeable. **This check is mandatory before every spawn.**

For each feature group, spawn a coding agent using the Task tool with `isolation: "worktree"`:

```
subagent_type: Bash (for simple) or general-purpose (for medium/complex)
model: haiku | sonnet | opus (based on complexity group)
isolation: worktree
prompt: <detailed feature spec + dev lifecycle instructions>
```

Each agent prompt **must** include:
- The specific features to implement
- Relevant existing files to read first
- The dev lifecycle steps to follow (reference this file's "Development Lifecycle" section)
- **Branch context**: Tell the agent which branch it is working on. The `isolation: "worktree"` mechanism creates a new branch off the PM's current HEAD and checks it out in the worktree. The agent should verify with `git branch --show-current` and include the branch name in its completion report.
- **Base branch**: Tell the agent the name of the PM's topic branch (e.g. `feat/<topic>`) so it knows what its work will be merged into. This is for context only — the agent does not merge.
- **Commit requirement**: Remind the agent it must `git add -A && git commit` before finishing. Uncommitted worktree changes are silently discarded.

### Phase 4 — Analyze & Merge

When an agent completes:

1. Read the agent's completion report (returned in the Task result)
2. Find the worktree branch: check `git branch --sort=-committerdate` for the branch name the agent reported, or look for recent `worktree-agent-*` branches
3. Verify the diff: `git diff <topic-branch>...<worktree-branch>`
4. Verify: Do the changes match the assigned feature spec?
5. Verify: Are tests present and meaningful (not just passing trivially)?
6. Verify: No regressions in unrelated modules
7. If the worktree branch is missing or has no commits, the agent failed to commit — resume it with instructions to commit
8. If issues found: resume the agent with specific feedback
9. If satisfactory: merge into the topic branch:
   ```bash
   git checkout feat/<topic>
   git merge --no-ff <worktree-branch> -m "feat: <description>"
   ```

### Phase 5 — QA Pass

Once all feature agents have merged into the topic branch:

1. Spawn one or more QA agents (`sonnet` or `opus`) with the task:
   - Review all changes on the topic branch vs `main`
   - Identify missing edge case tests
   - Identify code quality issues (dead code, unclear logic, missing type hints on public functions)
   - Identify any features from the original spec that were missed
2. Analyze QA agent reports
3. If gaps found: return to Phase 2 with the remaining work
4. If clean: push the topic branch to GitHub:
   ```bash
   git push -u origin feat/<topic>
   ```

**The PM never merges to `main` directly.** The user reviews the pushed branch and merges via PR at their discretion.

### Phase 6 — Repeat

If new requirements emerge or QA finds significant gaps, repeat from Phase 1 with a new or extended topic branch.

---

## Branch Conventions

```
main              — always releasable
feat/<topic>      — topic branch owned by PM for a feature set
fix/<topic>       — bug fix topic branch
```

Worktree branches are created automatically by `isolation: "worktree"` with names like `worktree-agent-<id>`. They branch off the PM's current HEAD at spawn time. After the PM merges, the worktree branch can be deleted:

```bash
git branch -d worktree-agent-<id>
```

**Critical**: If an agent does not commit, the worktree is cleaned up with no changes preserved. The PM must always instruct agents to commit.

## Commit Conventions

```
feat: add overlay user setup for fedora instances
fix: skip sudoers setup on scratch-based containers
test: add config atomic write tests
refactor: extract base image detection to instance module
chore: update dependencies
docs: document overlay mode in README
```

---

## Project: scratch-monkey

### Tech Stack

- **Language**: Python 3.11+
- **Package manager**: uv (`~/.local/bin/uv`)
- **Build**: hatchling (`pyproject.toml`)
- **CLI**: Click
- **GUI**: Enaml + Qt6 (optional dep)
- **Tests**: pytest (390 tests, no real podman required)
- **Lint/format**: ruff

### Key Commands

**uv** is the package manager for this project (`~/.local/bin/uv`). Use it for all installs and running dev tools.

```bash
# Install CLI only (dev mode)
uv tool install --editable .

# Install with GUI dependencies
uv tool install --editable ".[gui]"

# Reinstall (e.g. after adding a dep)
uv tool install --editable ".[gui]" --force

# Lint  (must be clean before any merge)
uv run ruff check src tests

# Format
uv run ruff format src tests

# Test  (all tests must pass)
uv run pytest

# Test with short traceback
uv run pytest -q --tb=short
```

The `justfile` wraps these same commands — `just test`, `just lint`, `just fmt`, etc.

### Package Layout

```
src/scratch_monkey/
  config.py      — InstanceConfig dataclass, tomllib parse, atomic save
  container.py   — PodmanRunner (subprocess wrapper, fully mockable)
  instance.py    — create/clone/delete/list/skel lifecycle
  overlay.py     — overlay container management (scratch vs fedora detection)
  shared.py      — shared volume CRUD
  export.py      — export_command / unexport
  cli/main.py    — Click CLI (all user commands)
  gui/           — Enaml GUI (optional)
    models/        — Atom models; AppModel = shared state + async machinery
    controllers/   — action controllers owned by AppModel (instances, overlays, shared, tools)
    views/         — .enaml views and dialogs
tests/
  conftest.py    — shared fixtures (mock_runner, instance fixtures)
  gui_helpers.py — shared GUI test helpers (make_app, call_sync, ...)
  test_*.py      — unit tests per module (GUI: test_gui_models.py, test_gui_controllers_*.py)
```

### Podman Boundary

All podman calls go through `PodmanRunner` in `container.py`. Tests mock at this boundary — no real podman process is ever started in unit tests. Any new module that needs to run podman commands must use `PodmanRunner`, not bare `subprocess.run`.

### Critical Design Decisions

- **Atomic config writes**: always use `config.save()`, never write scratch.toml directly
- **Fedora detection**: use `is_fedora_based(instance_dir)` which reads the **last** FROM line
- **Overlay user setup**: only runs for fedora instances; scratch instances skip entirely
- **Instance name validation**: always call `validate_name()` before creating/cloning
- **Always clean up downstream impacts**: when a change makes existing code redundant or obsolete, remove or update that code in the same changeset
- **Defensive defaults**: default to the most locked-down option (e.g. volume mounts default to `ro`, not `rw`). Only grant broader permissions when the user explicitly requests it
- **Strict output, liberal input**: when parsing, accept loose formats (e.g. volume specs with or without a mode suffix). When serializing, always write the explicit, unambiguous form (e.g. always include `:ro` or `:rw` — never rely on implicit defaults)

### GUI / Enaml References

- **`UI_LAYOUT.md`**: Enaml layout gotchas and patterns — read before modifying GUI layout code
