# scratch-monkey

A Podman-based dev container manager for rootless Fedora/ostree systems.

Each **instance** is a named environment with its own home directory, customizable Dockerfile, and TOML config. Two base image types are supported:

- **scratch** — empty image with Fedora ostree symlinks; host `/usr`, `/etc`, etc. are bind-mounted read-only. No image bloat, instant startup.
- **fedora** — full `fedora:latest` image. Self-contained, own package manager, no host mounts.

---

## Install

```bash
# Clone and run the install script
git clone https://github.com/djoyce/scratch-monkey.git
cd scratch-monkey
./install.sh

# With GUI support:
./install.sh --gui
```

The install script checks for [uv](https://github.com/astral-sh/uv) and offers to install it if missing, then installs scratch-monkey into `~/.local/bin`.

<details>
<summary>Manual install (without install.sh)</summary>

```bash
uv tool install --editable .

# With GUI support:
uv tool install --editable ".[gui]"
```
</details>

Ensure `~/.local/bin` is in your `PATH`.

---

## Quick start

```bash
# Create an instance (scratch base)
scratch-monkey create myproject

# Create a fedora-based instance with shell configs copied in
scratch-monkey create myproject --fedora --skel

# Enter an interactive shell
scratch-monkey enter myproject

# List all instances
scratch-monkey list
```

---

## Instance management

```bash
scratch-monkey create <name>              # scratch base
scratch-monkey create <name> --fedora     # fedora base
scratch-monkey create <name> --skel       # copy /etc/skel dotfiles into home

scratch-monkey clone <source> <dest>      # copy Dockerfile + config, fresh home/
scratch-monkey delete <name>              # prompts for confirmation (--yes to skip)
scratch-monkey list                       # show all instances with status

scratch-monkey skel <name>                # copy /etc/skel dotfiles (post-create)
scratch-monkey edit <name>                # edit scratch.toml ($EDITOR)
scratch-monkey edit <name> --file dockerfile
scratch-monkey edit <name> --file env
```

Each instance lives at `$HOME/scratch-monkey/<name>/`:

```
myproject/
  home/         ← container home directory (mounted read-write)
  Dockerfile    ← customizable, extends the base image
  scratch.toml  ← instance configuration
  .env          ← environment variables / secrets (KEY=VALUE, one per line)
```

---

## Running

```bash
scratch-monkey enter <name>               # interactive shell
scratch-monkey enter <name> --root        # root shell
scratch-monkey run   <name>               # same as enter
scratch-monkey run   <name> --wayland     # override: enable Wayland
scratch-monkey run   <name> --ssh         # override: enable SSH agent
scratch-monkey run   <name> --x11         # override: enable X11 sharing
scratch-monkey run   <name> --gpu         # override: enable GPU passthrough
scratch-monkey run   <name> --cmd /bin/zsh

scratch-monkey start <name>               # start overlay container (headless)
scratch-monkey stop  <name>               # stop overlay container
```

---

## Configuration (`scratch.toml`)

All fields are optional — commented-out defaults are shown when an instance is created.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cmd` | string | `/bin/bash` | Command to run on entry |
| `wayland` | bool | `false` | Mount Wayland socket, set `WAYLAND_DISPLAY` |
| `x11` | bool | `false` | Mount X11 socket, set `DISPLAY` and `XAUTHORITY` |
| `ssh` | bool | `false` | Forward `SSH_AUTH_SOCK` for SSH agent access |
| `home` | string | `""` | Override the instance `home/` dir with a custom path |
| `volumes` | list | `[]` | Extra volume mounts (`host:container:mode`) |
| `env` | list | `[]` | Extra environment variables (`KEY=value`) |
| `shared` | list | `[]` | Shared volume names (mounted at `/shared/<name>`; append `:ro` for read-only) |
| `overlay` | bool | `false` | Enable overlay mode (persistent writable layer) |
| `gpu` | bool | `false` | Enable GPU passthrough (auto-detects `/dev/dri`, `/dev/kfd`, `/dev/nvidia*`) |
| `devices` | list | `[]` | Extra device paths to mount (e.g. `/dev/video0`, `/dev/bus/usb`) |

Example:

```toml
cmd = "/bin/zsh"
wayland = true
x11 = true
ssh = true
volumes = ["/home/linuxbrew/.linuxbrew:/home/linuxbrew/.linuxbrew:ro"]
shared = ["comms", "data:ro"]
overlay = true
gpu = true
devices = ["/dev/video0"]
```

---

## Building

```bash
# Build the base image
scratch-monkey build               # scratch base
scratch-monkey build --fedora      # fedora base

# Build an instance's custom Dockerfile (tagged as the instance name)
scratch-monkey build-instance <name>
```

Auto-builds the base if it's missing when you run `enter`.

---

## Overlay mode

When `overlay = true` in `scratch.toml`, a persistent daemon container (`<name>-overlay`) is kept between sessions. Package installs (e.g. `dnf install vim`) survive across runs without rebuilding the image.

```bash
scratch-monkey enter myproject        # exec into the overlay container
scratch-monkey enter myproject --root # exec as root
scratch-monkey reset myproject        # wipe the overlay container (prompts)
scratch-monkey refresh myproject      # recreate the container, preserving installs
```

> **Note:** Overlay user setup (`useradd`, `sudoers`) is only performed for fedora-based instances. Scratch instances mount `/etc` from the host read-only — the user already exists and sudo works via host config.

### Refreshing the overlay container

Once created, the overlay container is only ever `podman start`ed — changes to `scratch.toml` (volumes, env, GPU, devices, etc.) or to scratch-monkey's own run args don't reach an existing container. Use `refresh` to recreate it with current settings:

```bash
scratch-monkey refresh myproject            # recreate, preserving installed software
scratch-monkey refresh myproject --discard  # recreate from a clean image (prompts)
```

By default, `refresh` commits the container's writable layer (e.g. anything installed with `dnf install`) to the instance's image tag *before* removing the container, then recreates it from that snapshot — so ad-hoc installs survive. If the commit fails, the original container is left untouched.

`--discard` skips the snapshot and recreates from the base/instance image instead, throwing away anything installed inside the container; it prompts for confirmation since this is destructive. `--discard` recreates from whatever image `enter`/`start` would use — so if a previous refresh (or `build-instance`) already tagged the instance image, it discards only changes made since that snapshot, not the full history back to the pristine base image.

Each preserving `refresh` adds one more layer to the instance's image, since it's committed on top of whatever was there before. Repeated refreshes will grow the image over time — rebuild the instance image (`build-instance`) for a clean slate.

> **Root entry:** `--root` sets the workdir to `/root`. Scratch images don't create `/root`, so scratch-monkey handles it automatically: in non-overlay mode, `/root` is a tmpfs mount; in overlay mode, `/root` is created in the container's writable layer on first root entry and persists across restarts.

---

## Shared volumes

Shared volumes let multiple instances share a host directory, useful for IPC via files, sockets, FIFOs, or databases. They live at `$HOME/scratch-monkey/.shared/<name>/` and are mounted at `/shared/<name>` inside each container.

```bash
scratch-monkey share create comms         # create the shared volume
scratch-monkey share add    comms agent1  # add to instance config
scratch-monkey share add    comms agent2

# Both instances now see /shared/comms/ (read-write, same host dir)
scratch-monkey enter agent1
scratch-monkey enter agent2

scratch-monkey share list                 # list volumes and which instances use them
scratch-monkey share remove comms agent1  # remove from instance config
scratch-monkey share delete comms         # delete the volume directory
```

Shared volumes default to read-write. Append `:ro` in `scratch.toml` to mount read-only:

```toml
shared = ["comms", "data:ro"]
```

---

## Exporting commands

Make a command from an instance available on your host `PATH`:

```bash
scratch-monkey export myproject /usr/bin/rg          # creates ~/.local/bin/rg
scratch-monkey export myproject /usr/bin/rg myrg     # custom bin name

scratch-monkey unexport rg
```

The generated script:
1. If already inside the instance, execs the command directly
2. If the overlay container is running, execs into it
3. Otherwise, launches a one-shot `podman run --rm`

---

## GUI

A graphical interface is available when installed with GUI dependencies:

```bash
uv tool install --editable ".[gui]"
scratch-monkey-gui                                # standalone launcher
scratch-monkey gui                                # or via the CLI (respects --instances-dir)
scratch-monkey --instances-dir /path/to/dir gui  # use a custom instances directory
```

![scratch-monkey GUI with Zed editor launched from inside an instance](screenshot.png)

The GUI provides:
- Instance list with start/stop toggle buttons and image/overlay status
- Quick-open buttons to open instance path or home directory in a file manager or terminal
- Configuration editing (cmd, wayland, ssh, home, overlay, GPU passthrough)
- Volume mount management (add/remove host:container mounts, set ro/rw mode; defaults to ro)
- Environment variable, device, and shared volume management
- Action buttons (enter, enter as root, build, reset overlay, export cmd, edit files)
- Live status polling with in-place overlay/image status updates

---

## Customizing the Dockerfile

### Scratch instances

`RUN` does not work (no shell in the image). Use `COPY`/`ADD` or multi-stage builds:

```dockerfile
FROM golang:latest AS builder
RUN go install github.com/some/tool@latest

FROM scratch_monkey
COPY --from=builder /go/bin/tool /usr/local/bin/tool
```

### Fedora instances

Full OS available — `RUN`, `COPY`, `ADD` all work:

```dockerfile
FROM scratch_monkey_fedora
RUN dnf install -y git vim neovim
```

Build with `scratch-monkey build-instance myproject`, then run normally.

---

## What gets mounted

### Scratch instances

| Host path | Container path | Mode |
|-----------|---------------|------|
| `/usr` | `/usr` | read-only |
| `/etc` | `/etc` | read-only |
| `/var/usrlocal` | `/var/usrlocal`, `/usr/local` | read-only |
| `/var/opt` | `/var/opt` | read-only |
| Instance `home/` | `/home/$USER` | read-write |
| *(tmpfs)* | `/tmp` | read-write, in-memory |

### Fedora instances

| Host path | Container path | Mode |
|-----------|---------------|------|
| Instance `home/` | `/home/$USER` | read-write |

Fedora instances use their own filesystem — no host system mounts.

---

## Command reference

| Command | Description |
|---------|-------------|
| `create <name>` | Create a new instance (`--fedora`, `--skel`) |
| `clone <src> <dest>` | Clone an instance (fresh home) |
| `delete <name>` | Delete an instance (`--yes` to skip confirm) |
| `list` | List all instances with status |
| `skel <name>` | Copy /etc/skel dotfiles into home |
| `edit <name>` | Edit instance files (`--file config\|dockerfile\|env`) |
| `build` | Build the base image (`--fedora`) |
| `build-instance <name>` | Build an instance's Dockerfile |
| `run <name>` | Run an instance (`--root`, `--wayland`, `--ssh`, `--x11`, `--gpu`, `--cmd`) |
| `enter <name>` | Interactive shell (`--root` for root) |
| `start <name>` | Start overlay container (creates if needed) |
| `stop <name>` | Stop overlay container |
| `reset <name>` | Remove overlay container (`--yes` to skip confirm) |
| `refresh <name>` | Recreate overlay container, preserving installs (`--discard` for clean slate) |
| `export <name> <cmd> [bin]` | Export a command to `~/.local/bin` |
| `unexport <bin>` | Remove an exported command |
| `share create <name>` | Create a shared volume |
| `share delete <name>` | Delete a shared volume |
| `share add <vol> <instance>` | Add volume to instance config |
| `share remove <vol> <instance>` | Remove volume from instance config |
| `share list` | List all shared volumes and usage |
| `gui` | Launch the GUI (requires `[gui]` extras) |

Global options (before the command):

```
--instances-dir TEXT   Default: ~/scratch-monkey
--base-image TEXT      Default: scratch_monkey
```

---

## Notes

- Requires **rootless podman**
- Uses `--userns=keep-id` for correct user ID mapping (dropped for `--root`)
- SELinux labeling disabled for the container (`--security-opt label=disable`)
- Host networking enabled (`--network=host`)
- Container hostname is set to `<instance>.<hostname>` for prompt clarity

---

## Development

[uv](https://github.com/astral-sh/uv) is required. The `install.sh` script installs it if missing.

```bash
uv tool install --editable .      # install CLI
uv run pytest                     # unit tests, no real podman needed
uv run --all-extras pytest        # include GUI smoke tests
uv run ruff check src tests       # lint
uv run ruff format src tests      # format
```

See `justfile` for the full list of dev recipes (`just test`, `just lint`, `just fmt`, etc.).
