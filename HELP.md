# scratch-monkey: How It Works

## Introduction

scratch-monkey is a dev container manager built on rootless Podman, designed
for Fedora Atomic / ostree systems (Silverblue, Kinoite, Bazzite, etc.) where
the host OS is immutable.

Instead of layering packages onto the host with `rpm-ostree`, scratch-monkey
lets you spin up lightweight, disposable dev environments as Podman containers.
Each environment — called an **instance** — gets its own home directory,
Dockerfile, and TOML config file. You can enter and exit instances in
seconds, customize them freely, and throw them away without affecting your host.

Two base image types are available:

- **Scratch** instances bind-mount your host `/usr` and `/etc` read-only into
  an empty container — near-zero build time, instant startup, access to all
  host-installed tools.
- **Fedora** instances use a full `fedora:latest` image — self-contained,
  with their own `dnf`, isolated from the host.

Additional features include persistent overlay containers, shared volumes
for inter-instance communication, GPU/device passthrough, command export
to make container tools available on the host PATH, and an optional Qt GUI.

### Coding agent sandbox

A primary use case for scratch-monkey is providing isolated dev environments
for AI coding agents (Claude Code, Aider, Copilot Workspace, etc.). Running
an agent inside a scratch-monkey instance means it can install packages, modify
system files, and experiment freely without any risk to your host OS.

This is especially useful with flags like `--dangerously-skip-permissions` that
give an agent full autonomy — inside a scratch-monkey container, "dangerous"
operations like `rm -rf /`, `dnf remove --all`, or writing to `/etc` are
fully contained. The worst case is a `scratch-monkey reset` or
`scratch-monkey delete` to start fresh.

```mermaid
graph LR
    AGENT["Coding Agent<br/>(--dangerously-skip-permissions)"] --> CONTAINER["scratch-monkey instance"]
    CONTAINER -->|"isolated"| HOST["Host OS<br/>(untouched)"]
    CONTAINER -->|"mount rw"| HOME["instance home/<br/>(disposable)"]
```

> **Not a security sandbox.** scratch-monkey is designed for *developer
> convenience*, not for adversarial containment. It uses rootless Podman with
> `--network=host`, `--security-opt label=disable`, and bind-mounted host
> paths. These are reasonable defaults for dev work but are **not appropriate
> for malware analysis, reverse engineering untrusted binaries, or any
> security research where containment is critical**. The project has not been
> designed or audited for that purpose, and some design choices (host
> networking, SELinux disabled, host filesystem mounts) actively work against
> it. For that kind of work, use purpose-built isolation tools (VMs, gVisor,
> dedicated sandboxing frameworks).

---

## Table of Contents

- [Coding Agent Sandbox](#coding-agent-sandbox)
- [Installation](#installation)
- [Core Concepts](#core-concepts)
- [Base Image Architecture](#base-image-architecture)
- [Instance Lifecycle](#instance-lifecycle)
- [Container Runtime](#container-runtime)
- [Overlay Mode](#overlay-mode)
- [Shared Volumes](#shared-volumes)
- [Command Export](#command-export)
- [GPU and Device Passthrough](#gpu-and-device-passthrough)
- [Configuration Reference](#configuration-reference)
- [GUI](#gui)
- [CLI Reference](#cli-reference)

---

## Installation

### Prerequisites

- **Podman** (rootless) — comes pre-installed on Fedora Atomic desktops
- **Python 3.11+**
- **uv** (Python package manager) — the install script will offer to install
  it if missing

### Install from source

Clone the repository and run the install script:

```bash
git clone https://github.com/djoyce/scratch-monkey.git
cd scratch-monkey
./install.sh
```

The install script:

```mermaid
graph TD
    A["./install.sh"] --> B{"uv installed?"}
    B -->|No| C["Prompt to install uv<br/>(curl from astral.sh)"]
    C --> D["uv tool install --editable ."]
    B -->|Yes| D
    D --> E["Check ~/.local/bin in PATH"]
    E --> F["Create ~/.config/scratch-monkey/"]
    F --> G["Done!"]
```

1. Checks for `uv` and offers to install it if missing
2. Runs `uv tool install --editable .` to install the CLI
3. Warns if `~/.local/bin` is not in your `PATH`
4. Creates the config directory at `~/.config/scratch-monkey/`

### Install with GUI

Pass `--gui` to include the Qt6/Enaml GUI dependencies:

```bash
./install.sh --gui
```

Or manually:

```bash
uv tool install --editable ".[gui]"
```

### Manual install (without install.sh)

If you already have `uv`:

```bash
git clone https://github.com/djoyce/scratch-monkey.git
cd scratch-monkey

# CLI only
uv tool install --editable .

# With GUI
uv tool install --editable ".[gui]"
```

### Verify installation

```bash
scratch-monkey --help
```

### Quick start

```bash
# Create a scratch-based instance
scratch-monkey create myproject

# Or a fedora-based instance with your shell configs
scratch-monkey create myproject --fedora --skel

# Enter the instance
scratch-monkey enter myproject
```

---

## Core Concepts

scratch-monkey manages **instances** — named dev environments backed by Podman
containers. Each instance has its own home directory, Dockerfile, config, and
optional persistent overlay.

```mermaid
graph TD
    SM[scratch-monkey] --> S[Scratch Instances]
    SM --> F[Fedora Instances]

    S --> S1["Host /usr, /etc mounted read-only"]
    S --> S2["Near-zero image size"]
    S --> S3["Instant startup"]

    F --> F1["Self-contained fedora:latest"]
    F --> F2["Own package manager"]
    F --> F3["Slower build, more flexible"]
```

There are two fundamentally different base image types:

| | Scratch | Fedora |
|--|---------|--------|
| Base | Empty (`FROM scratch`) | `FROM fedora:latest` |
| Host mounts | `/usr`, `/etc`, `/var/usrlocal`, `/var/opt` (ro) + tmpfs `/tmp` | None |
| Package manager | Host's (via bind mount) | Container's own `dnf` |
| Image size | ~KB (just symlinks) | ~500MB+ |
| `RUN` in Dockerfile | No (no shell) | Yes |
| User setup in overlay | Skipped (host `/etc` has it) | `useradd` + sudoers created |
| Best for | Quick envs using host tools | Isolated envs needing own packages |

---

## Base Image Architecture

### Scratch base image

The scratch image is built with a multi-stage Dockerfile. A Fedora builder
creates a minimal rootfs with symlinks that match Fedora's ostree layout,
then copies it into an empty `FROM scratch` image.

```mermaid
graph TD
    subgraph stage1["Stage 1: fedora:latest (builder)"]
        B1["mkdir /rootfs/var/home"]
        B2["mkdir /rootfs/var/opt"]
        B3["mkdir /rootfs/var/usrlocal"]
        B4["mkdir /rootfs/usr"]
        B5["Symlinks:<br/>/bin → usr/bin<br/>/sbin → usr/sbin<br/>/lib → usr/lib<br/>/lib64 → usr/lib64<br/>/opt → var/opt<br/>/home → var/home"]
    end

    stage1 -->|"COPY --from=builder"| stage2

    subgraph stage2["Stage 2: FROM scratch"]
        R1["/usr/ (empty dir)"]
        R2["/var/home/ (empty dir)"]
        R3["/var/opt/ (empty dir)"]
        R4["/var/usrlocal/ (empty dir)"]
        R5["+ symlinks from stage 1"]
        R6["No shell. No binaries. No OS."]
    end
```

At runtime, the host filesystem fills these empty directories:

```mermaid
graph LR
    subgraph host["Host (read-only)"]
        H1["/usr"]
        H2["/etc"]
        H3["/var/usrlocal"]
        H4["/var/opt"]
    end

    subgraph container["Scratch Container"]
        C1["/usr/"]
        C2["/etc/"]
        C3["/usr/local/"]
        C4["/var/opt/"]
        C5["/home/user/"]
        C6["/tmp/ (tmpfs)"]
    end

    H1 -->|mount ro| C1
    H2 -->|mount ro| C2
    H3 -->|mount ro| C3
    H4 -->|mount ro| C4

    HOME["instance home/ (read-write)"] -->|mount rw| C5
    TMPFS["(in-memory tmpfs)"] -->|"--tmpfs"| C6
```

### Fedora base image

Just `FROM fedora:latest`. A complete OS. Only the instance home directory
is mounted from the host.

```mermaid
graph LR
    IMG["fedora:latest image layers"] -->|provides| C1["/usr/"]
    IMG -->|provides| C2["/etc/"]
    HOME["instance home/ (read-write)"] -->|mount rw| C3["/home/user/"]
```

### Instance image layering

When you customize an instance's Dockerfile and run `build-instance`,
a new image layer is added on top of the base:

```mermaid
graph TD
    IL["Instance layer<br/>(user's custom Dockerfile)"]
    BI["Base image<br/>(scratch or fedora)"]
    IL --- BI

    CMD["scratch-monkey build-instance myproject"] -.->|creates| IL
```

For scratch instances, the Dockerfile can't use `RUN` (no shell).
Use multi-stage builds to compile tools and `COPY` them in:

```dockerfile
FROM golang:latest AS builder
RUN go install github.com/some/tool@latest

FROM scratch_monkey
COPY --from=builder /go/bin/tool /usr/local/bin/tool
```

For fedora instances, everything works normally:

```dockerfile
FROM scratch_monkey_fedora
RUN dnf install -y git vim neovim
```

---

## Instance Lifecycle

### Directory structure

Each instance lives at `~/scratch-monkey/<name>/`:

```
~/scratch-monkey/
  myproject/
    home/           # Container home dir (mounted rw at /home/$USER)
    Dockerfile      # Extends base image, customizable
    scratch.toml    # Instance configuration
    .env            # Environment secrets (KEY=VALUE, one per line)
  another/
    home/
    Dockerfile
    scratch.toml
    .env
  .shared/          # Hidden dir for shared volumes
    comms/
    data/
```

### Create

```
scratch-monkey create myproject [--fedora] [--skel]
```

```mermaid
graph TD
    A["validate name"] --> B["Create directory:<br/>~/scratch-monkey/myproject/"]
    B --> C["Create subdirectory: home/"]
    C --> D["Copy scratch.toml.default → scratch.toml"]
    D --> E["Generate Dockerfile<br/>(scratch or fedora template)"]
    E --> F["Create empty .env file"]
    F --> G{"--skel?"}
    G -->|yes| H["Copy /etc/skel dotfiles into home/"]
    G -->|no| I["Done"]
    H --> I
```

### Clone

```
scratch-monkey clone myproject myproject-copy
```

```mermaid
graph TD
    A["Validate dest name"] --> B["Create dest directory + home/"]
    B --> C["Copy source Dockerfile,<br/>scratch.toml, .env"]
    C --> D["Clear overlay_id in dest config<br/>(prevent shared overlay)"]
    D --> E["Tag source image for dest<br/>(if image exists)"]
    E --> F["home/ starts fresh (not copied)"]
```

### Delete

```
scratch-monkey delete myproject [--yes]
```

```mermaid
graph TD
    A["Remove overlay container (if any)"] --> B["Remove podman image (if any)"]
    B --> C["Remove instance directory tree"]
```

### Rename

```
scratch-monkey rename old-name new-name
```

Renames the directory and re-tags the image. The overlay_id (if set)
is decoupled from the instance name, so overlays survive renames.

---

## Container Runtime

When you run `scratch-monkey enter myproject`, this is what happens:

```mermaid
graph TD
    subgraph always["Always set"]
        A1["--rm -it (interactive, remove on exit)"]
        A2["--security-opt label=disable"]
        A3["--network=host"]
        A4["--userns=keep-id (rootless UID mapping)"]
        A5["--hostname instance.hostname"]
        A6["-e HOME, USER, SCRATCH_INSTANCE"]
        A7["--workdir /home/user"]
    end

    subgraph mounts["Mounts"]
        direction LR
        subgraph scratch_mounts["Scratch"]
            M1["/usr:/usr:ro"]
            M2["/etc:/etc:ro"]
            M3["/var/usrlocal:...:ro"]
            M4["/var/opt:/var/opt:ro"]
            M8["--tmpfs /tmp"]
            M9["--tmpfs /root"]
        end
        subgraph fedora_mounts["Fedora"]
            M5["(none — self-contained)"]
        end
    end

    subgraph common["Common mounts"]
        M6["instance home/ → /home/user"]
        M7["--env-file .env (if exists)"]
    end

    subgraph optional["Optional (from scratch.toml)"]
        O1["wayland socket (wayland = true)"]
        O2["SSH_AUTH_SOCK (ssh = true)"]
        O3["host:container volumes"]
        O4["KEY=value env vars"]
        O5["shared:/shared/x"]
        O6["--device /dev/... (gpu/devices)"]
        O7["podman.sock + CONTAINER_HOST/DOCKER_HOST (podman = true)"]
    end

    always --> mounts
    mounts --> common
    common --> optional
```

---

## Overlay Mode

Overlay mode creates a persistent daemon container that survives between
sessions. Changes made inside the container (package installs, config edits
outside home/) persist without rebuilding the image.

### Without overlay (default)

```mermaid
graph LR
    A["scratch-monkey enter myproject"] --> B["podman run --rm -it"]
    B --> C["Work inside container"]
    C --> D["Exit"]
    D --> E["Container destroyed"]
    E --> F["Only home/ persists<br/>(bind mount)"]
```

Every `enter` starts fresh from the image. Anything written outside
the home directory is lost.

### With overlay

```toml
# scratch.toml
overlay = true
```

```mermaid
graph TD
    ENTER["scratch-monkey enter myproject"] --> CHECK{"Overlay container<br/>exists?"}

    CHECK -->|No| CREATE["Create daemon container:<br/>podman run -d --name sm-a1b2c3d4<br/>... sleep infinity"]
    CREATE --> FEDORA{"Fedora-based?"}
    FEDORA -->|Yes| SETUP["Setup user: useradd, sudoers"]
    FEDORA -->|No| EXEC
    SETUP --> EXEC["podman exec -it sm-a1b2c3d4 /bin/bash"]

    CHECK -->|Yes| RUNNING{"Running?"}
    RUNNING -->|No| START["podman start sm-a1b2c3d4"]
    START --> EXEC
    RUNNING -->|Yes| EXEC

    EXEC --> SESSION["Interactive session<br/>All changes persist in container"]
```

```mermaid
graph TD
    subgraph image["Image Layers"]
        IL["Instance layer"]
        BI["Base image"]
        IL --- BI
    end

    image -->|"used as base"| overlay

    subgraph overlay["Overlay Container"]
        WL["Writable layer<br/>(dnf installs, config changes, etc.)"]
        CF["Container filesystem<br/>persists across runs"]
        WL --- CF
    end
```

### Overlay user setup (fedora only)

When a fedora overlay container starts for the first time, scratch-monkey
sets up the user inside it:

1. `dnf install -y sudo` (if not present)
2. `useradd -u <host_uid> -M -s /bin/bash <username>`
3. `echo "<user> ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/<user>`

This is skipped for scratch instances because host `/etc` is bind-mounted
read-only — the host user and sudo config are already visible.

### Root entry

When entering with `--root`, the workdir is set to `/root`. Scratch images
don't create `/root`, so scratch-monkey ensures it exists:

- **Non-overlay mode**: `/root` is mounted as a tmpfs (in-memory, ephemeral).
  It exists for the lifetime of the container and is discarded on exit.
- **Overlay mode**: `/root` is created via `mkdir -p /root` in the container's
  writable layer on first root entry. It persists across container restarts
  and survives `stop`/`start` cycles. Only `reset` removes it (along with
  all other writable-layer changes).

Fedora instances already have `/root` in their base image, so the mkdir
is a no-op.

### Reset

```
scratch-monkey reset myproject
```

Removes the overlay container, discarding all changes outside home/.
Next `enter` creates a fresh overlay from the image.

### Overlay ID

Each overlay container gets a unique ID (e.g., `sm-a1b2c3d4`) stored in
`scratch.toml` as `overlay_id`. This decouples the container name from the
instance name, so renames don't break overlays. Cloning clears the
overlay_id to prevent two instances sharing one container.

---

## Shared Volumes

Shared volumes let multiple instances access the same host directory.
Useful for IPC via files, sockets, FIFOs, or shared databases.

```
~/scratch-monkey/.shared/
  comms/          <-- shared volume "comms"
  data/           <-- shared volume "data"
```

```
scratch-monkey share create comms
scratch-monkey share add comms agent1
scratch-monkey share add comms agent2
```

```mermaid
graph TD
    subgraph agent1["agent1 container"]
        A1["/shared/comms/"]
    end

    subgraph agent2["agent2 container"]
        A2["/shared/comms/"]
    end

    HOST["Host: ~/.../shared/comms"]

    A1 <-->|mount| HOST
    A2 <-->|mount| HOST
```

Mode control in `scratch.toml`:

```toml
shared = ["comms", "data:ro"]   # comms is rw, data is read-only
```

Deleting a shared volume automatically removes it from all instance configs.

---

## Command Export

Export makes a command from inside an instance available on your host PATH:

```
scratch-monkey export myproject /usr/bin/rg
```

This creates `~/.local/bin/rg` — a wrapper script with three execution paths:

```mermaid
graph TD
    INVOKE["~/.local/bin/rg is invoked"] --> INSIDE{"Already inside<br/>myproject?<br/>(SCRATCH_INSTANCE == name)"}

    INSIDE -->|Yes| DIRECT["exec /usr/bin/rg directly"]

    INSIDE -->|No| OVERLAY{"Overlay<br/>running?"}

    OVERLAY -->|Yes| EXEC["podman exec<br/>into overlay<br/>run /usr/bin/rg"]

    OVERLAY -->|No| RUN["podman run --rm<br/>one-shot container<br/>run /usr/bin/rg"]
```

Remove with `scratch-monkey unexport rg`.

---

## GPU and Device Passthrough

### GPU auto-detection

When `gpu = true` in `scratch.toml`, scratch-monkey scans for:

| Device | Purpose |
|--------|---------|
| `/dev/dri` | DRM (Intel, AMD, generic GPU) |
| `/dev/kfd` | AMD ROCm compute |
| `/dev/nvidia*` | NVIDIA GPUs |

Each detected device is passed with `--device`.

### Extra devices

For other hardware (webcams, USB devices, etc.):

```toml
devices = ["/dev/video0", "/dev/bus/usb"]
```

---

## Configuration Reference

All fields in `scratch.toml` are optional.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cmd` | string | `/bin/bash` | Shell command to run on entry |
| `wayland` | bool | `false` | Forward Wayland display socket |
| `ssh` | bool | `false` | Forward SSH agent socket |
| `podman` | bool | `false` | Share the host rootless podman socket (grants host-level container access) |
| `home` | string | `""` | Override home dir (empty = instance `home/`) |
| `overlay` | bool | `false` | Enable persistent overlay container |
| `gpu` | bool | `false` | Auto-detect and pass through GPU devices |
| `volumes` | list | `[]` | Extra bind mounts (`host:container:mode`); mode defaults to `ro` if omitted |
| `env` | list | `[]` | Extra environment variables (`KEY=value`) |
| `shared` | list | `[]` | Shared volume names (`name` or `name:ro`) |
| `devices` | list | `[]` | Extra device paths to pass through |
| `overlay_id` | string | `""` | Auto-generated overlay container ID |

### Environment inside the container

These are always set:

| Variable | Value |
|----------|-------|
| `HOME` | `/home/$USER` (or `/root` with `--root`) |
| `USER` | Host username |
| `SCRATCH_INSTANCE` | Instance name |

Conditionally set:

| Variable | When |
|----------|------|
| `WAYLAND_DISPLAY` | `wayland = true` |
| `XDG_RUNTIME_DIR` | `wayland = true` |
| `SSH_AUTH_SOCK` | `ssh = true` |
| `CONTAINER_HOST` | `podman = true` (socket present) |
| `DOCKER_HOST` | `podman = true` (socket present) |

Plus any variables from `.env` and the `env` config list.

---

## GUI

Install with GUI dependencies:

```bash
uv tool install --editable ".[gui]"
scratch-monkey gui
```

The GUI provides a graphical interface to all instance management features.

```mermaid
graph TD
    subgraph window["scratch-monkey GUI"]
        direction LR
        subgraph left["Instance List"]
            I1["● fedora-dev [⧉][✎][✗]"]
            I2["● myproject [⧉][✎][✗]"]
            I3["○ tools [⧉][✎][✗]"]
            NI["[+ New Instance]"]
        end

        subgraph right["Instance Detail"]
            TITLE["fedora-dev<br/>Base: scratch_monkey_fedora"]
            PATHS["[❐][⌘] Path: ~/scratch-monkey/fedora-dev<br/>[⌂][⌘] Home: ~/scratch-monkey/fedora-dev/home"]

            subgraph actions["Actions"]
                ROW1["[Enter] [Enter as Root] [Build] [Reset]"]
                ROW2["[Export Cmd] [Edit Dockerfile] [Edit .env] [Edit Config]"]
            end

            SAVE["[Save Config] [Cancel Changes]"]

            subgraph config["Configuration"]
                CFG["Command / Home / Wayland / SSH<br/>Overlay / GPU Passthrough"]
            end

            subgraph volumes["Volume Mounts"]
                VOL["[host path] [container path] [ro] [X]<br/>[+ Add Volume Mount]"]
            end

            subgraph envvars["Environment Variables"]
                ENV["[KEY] [value] [X]<br/>[+ Add Environment Variable]"]
            end

            subgraph devices["Devices"]
                DEV["[/dev/video0] [X]<br/>[+ Add Device]"]
            end

            subgraph shared["Shared Volumes"]
                SHR["[x] comms [rw]<br/>[ ] data [rw]<br/>[+ New Shared Volume]"]
            end
        end
    end
```

### Key features

- **Instance list**: Start/stop toggle button (click to start or stop overlay),
  base type indicator (F for fedora), clone/rename/delete buttons per instance
- **Quick-open buttons**: Open instance path or home directory in file manager (❐/⌂)
  or terminal (⌘) directly from the detail panel header
- **Config editing**: All scratch.toml fields editable in-place, dirty tracking
  with save/revert, orange highlight on unsaved changes
- **Actions**: Enter, build, reset overlay, export commands, edit files in terminal
- **Edit Config**: Opens scratch.toml in `$VISUAL`/`$EDITOR`, auto-reloads on close
- **Create dialog**: Name, fedora toggle, skel toggle, full config editor
- **Volume mounts**: Defaults to read-only for safety; mode always written explicitly
- **Shared volumes**: Toggle per-instance, create new volumes inline
- **Live status polling**: Overlay and image status updates in-place every 5 seconds

---

## CLI Reference

### Global options

```
scratch-monkey [--instances-dir DIR] [--base-image IMAGE] COMMAND
```

| Option | Default | Description |
|--------|---------|-------------|
| `--instances-dir` | `~/scratch-monkey` | Where instances are stored |
| `--base-image` | `scratch_monkey` | Default base image for new instances |

### Commands

| Command | Description |
|---------|-------------|
| `create <name> [--fedora] [--skel]` | Create a new instance |
| `clone <source> <dest>` | Clone instance (fresh home) |
| `delete <name> [--yes]` | Delete instance, image, and overlay |
| `rename <old> <new>` | Rename an instance |
| `list` | List all instances with status |
| `skel <name>` | Copy /etc/skel dotfiles into home |
| `edit <name> [--file config\|dockerfile\|env]` | Edit instance files |
| `build [--fedora] [--yes]` | Build a base image |
| `build-instance <name>` | Build instance Dockerfile (auto-builds base) |
| `enter <name> [--root]` | Interactive shell |
| `run <name> [--root] [--wayland] [--ssh] [--podman] [--cmd CMD]` | Run with overrides |
| `start <name>` | Start overlay container (creates if needed, enables overlay mode) |
| `stop <name>` | Stop overlay container |
| `reset <name> [--yes]` | Remove overlay container |
| `export <name> <cmd> [bin]` | Export command to ~/.local/bin |
| `unexport <bin>` | Remove exported command |
| `share create <name>` | Create a shared volume |
| `share delete <name> [--yes]` | Delete shared volume (cleans all configs) |
| `share add <vol> <instance>` | Add volume to instance |
| `share remove <vol> <instance>` | Remove volume from instance |
| `share list` | List volumes and which instances use them |
| `gui` | Launch graphical interface |
