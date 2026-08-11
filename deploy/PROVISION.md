# Droplet provisioning

> **STATUS: DONE.** Provisioned 2026-08-10. Everything below has been **run and
> verified on the live droplet** — this is a record, not a plan. The one thing
> still outstanding is at the bottom.

## The droplet

| | |
|---|---|
| Name | `gable` |
| IP | `143.110.146.87` |
| Region | SFO3 |
| Size | 1 vCPU / 1 GB / 25 GB — **$6.00/month** |
| OS | Ubuntu 24.04.4 LTS, Python 3.12.3 |
| SSH key | `~/.ssh/gable_droplet` (ed25519, generated locally, no passphrase) |
| Access | `make ssh` |

**On the $6 vs the $4 in CLAUDE.md §9:** §9 says the $4 tier is 512MB-class and
that 512MB is tight for Python plus image handling. Since the renderer moved to
Google Slides most compositing happens on Google's servers, but Pillow still
normalizes every photo locally. 1 GB removes that risk for $2/month. Called
deliberately, flagged here rather than buried.

## Verified on the box

```
passwordauthentication no          # sshd -T
permitrootlogin without-password
kbdinteractiveauthentication no
/swapfile file 1024M               # swapon --show, and in /etc/fstab
vm.swappiness=10
ufw: active, deny incoming, allow outgoing, 22/tcp ALLOW
gable uid=109 gid=112, /opt/gable, /opt/gable/var, /opt/gable/secrets (0700)
python3-venv, python3-dev, git installed
```

`systemd-analyze verify` on the installed unit found a real bug: **StartLimitIntervalSec
and StartLimitBurst were in `[Service]`, where systemd ignores them silently.**
The crash-loop guard was doing nothing while looking correct. Moved to `[Unit]`;
`systemctl show` now reports `StartLimitIntervalUSec=5min`, `StartLimitBurst=5`.
`tests/test_deploy_unit.py` asserts the placement so it cannot regress.

The unit is installed at `/etc/systemd/system/gable.service`, currently
**disabled and inactive** — correct, because `ExecStart` points at
`gable.slackapp.app`, which is still a placeholder. Enable and start it when
Phase 1 lands.

## Still outstanding — needs Chase

1. **Secrets.** `.env` → `/opt/gable/.env` (chmod 600, owned by gable), and the
   Google service-account JSON → `/opt/gable/secrets/`. I never handle these.
2. **A git remote.** The repo is local-only, so `make deploy` has nothing to
   pull yet.
3. **Firewall scope.** SSH is currently open to any source address. If you want
   it narrowed to your IP, say the word — I left it open rather than risk
   locking us both out from a guess.

---

## Original instructions, kept for rebuilding from scratch

The commands below are what was run. They are idempotent and safe to re-run.

---

## 1. Create the droplet

- Ubuntu LTS, the $4 tier.
- **Add your SSH public key during creation.** Do not set a root password.
- Note the IP.

**Please report the tier's actual RAM and disk.** CLAUDE.md §9 says "512MB-class,
verify current specs before sizing anything," and the swap size below assumes
512MB. If DigitalOcean has changed that tier, the swap number changes.

## 2. Lock down SSH — key only

```bash
ssh root@<IP>
sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
sudo sshd -t && sudo systemctl restart ssh
```

`sshd -t` validates the config *before* the restart. Skipping it is how people
lock themselves out of a box they can still see in the dashboard.

Keep this session open until you have confirmed a second one still works.

## 3. Swap — 1GB

512MB plus Python plus Pillow is tight. Swap is what stops an image resize from
killing the process (CLAUDE.md §9).

```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# Prefer RAM; only reach for swap under real pressure.
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-gable-swap.conf
sudo sysctl -p /etc/sysctl.d/99-gable-swap.conf
free -h
```

`/etc/fstab` is what makes it survive a reboot. Without that line the swap
silently disappears the first time the droplet restarts.

## 4. Firewall — outbound only, plus your SSH

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from <YOUR_IP> to any port 22 proto tcp
sudo ufw enable
sudo ufw status verbose
```

Socket Mode opens an outbound WebSocket, so Gable needs **no** inbound rule.
That is the whole reason for the design (ARCHITECTURE.md §2.2).

Substitute your real address for `<YOUR_IP>`. If yours is dynamic, `sudo ufw
allow 22/tcp` works but is weaker — say which you chose and I will note it.

## 5. Unprivileged service user

```bash
sudo adduser --system --group --home /opt/gable --shell /usr/sbin/nologin gable
```

`--system` and `nologin`: this account runs a process, it is not a person. Gable
never runs as root (ARCHITECTURE.md §7).

## 6. Python and the checkout

```bash
sudo apt update && sudo apt install -y python3-venv python3-dev git
python3 --version    # must be 3.11 or newer — please report what it prints
```

The version matters: `pyproject.toml` pins mypy to 3.11 as the floor precisely
because the droplet's distro Python sets the real ceiling.

```bash
sudo -u gable git clone <REPO_URL> /opt/gable
sudo -u gable python3 -m venv /opt/gable/.venv
sudo -u gable /opt/gable/.venv/bin/pip install -e /opt/gable
sudo -u gable mkdir -p /opt/gable/var
```

`/opt/gable/var` is the only writable path the unit grants. It holds the xlsx
batches and the temp files photos stream through.

No repo exists on a remote yet — that is item 8 in my open questions.

## 7. Secrets — you place these, I never touch them

```bash
sudo -u gable cp /opt/gable/.env.example /opt/gable/.env
sudo -u gable nano /opt/gable/.env          # fill in real values
sudo chmod 600 /opt/gable/.env
sudo chown gable:gable /opt/gable/.env

# Service-account JSON lives OUTSIDE the repo tree (ARCHITECTURE.md §7).
sudo -u gable mkdir -p /home/gable/secrets   # adjust if the home differs
# scp the key here, then:
sudo chmod 600 /home/gable/secrets/gable-service-account.json
sudo chown gable:gable /home/gable/secrets/gable-service-account.json
```

Then point `GOOGLE_SERVICE_ACCOUNT_FILE` in `.env` at that path.

Two things to check: `git status` in `/opt/gable` must not list `.env`, and
`ProtectHome=true` in the unit blocks `/home` — so if you keep the key under
`/home/gable`, tell me and I will add a `ReadOnlyPaths` line for it. Putting it
in `/opt/gable/secrets/` instead avoids that entirely and is what I would pick.

## 8. Install the unit

```bash
sudo cp /opt/gable/deploy/gable.service /etc/systemd/system/gable.service
sudo systemctl daemon-reload
sudo systemctl enable gable
```

**Do not `start` it yet.** `ExecStart` points at `gable.slackapp.app`, which is
still a Phase 0 placeholder with no `__main__`. Starting it now fails five times
and trips the restart limiter. Enable it so it comes back after a reboot, and
start it when Phase 1 lands.

Let `make deploy` handle restarts from then on. Nothing gets hand-edited on the
server.

## 9. Passwordless restart for deploys

`make deploy` runs `sudo systemctl restart gable` over a non-interactive SSH
session, which cannot answer a password prompt.

```bash
echo '<YOUR_SSH_USER> ALL=(ALL) NOPASSWD: /bin/systemctl restart gable' \
  | sudo tee /etc/sudoers.d/gable-deploy
sudo chmod 440 /etc/sudoers.d/gable-deploy
sudo visudo -c
```

Scoped to exactly that one command — not a blanket NOPASSWD.

---

## What to send back

1. The droplet's actual RAM/disk, and `python3 --version`.
2. `free -h` after step 3.
3. `sudo ufw status verbose`.
4. The value for `make deploy GABLE_HOST=…` — the SSH user and host.
5. Where you put the service-account JSON, so I can fix `ProtectHome` if needed.
6. `systemctl status gable` after step 8. "enabled, inactive (dead)" is correct
   at this stage and is what I expect to see.


---

## Deployed 2026-08-11 — the Slack listener is live

Recorded because the next person needs the real steps, not the intended ones.

**Access.** The droplet pulls from GitHub with a **read-only deploy key**
(`/root/.ssh/gable_deploy`, registered on the repo as "gable droplet
(read-only)"). Read-only is deliberate: the droplet never needs to push, and a
compromised box should not be able to rewrite the repo. `/root/.ssh/config`
pins that key to `github.com`.

**Layout.**

    /opt/gable/                  the repo, owned by gable:gable
    /opt/gable/.venv/            the virtualenv
    /opt/gable/.env              mode 600, gable:gable
    /opt/gable/secrets/          the service-account key, mode 600

**The one thing that differs from the local checkout.** `.env` is copied with
`GOOGLE_SERVICE_ACCOUNT_FILE` rewritten to `/opt/gable/secrets/...`. Everything
else is identical, so a variable that works locally works here.

**Deploying an update:**

    ssh root@143.110.146.87 'cd /opt/gable && git pull --ff-only \
      && install -m 644 deploy/gable.service /etc/systemd/system/gable.service \
      && systemctl daemon-reload && systemctl restart gable'

`git` needs `safe.directory` set for `/opt/gable`, because root pulls into a
tree owned by `gable`. It is configured; a fresh box will need it again.

**Two things worth knowing.**

- `journalctl -u gable` shows warnings from *earlier boots*. A
  `StartLimitIntervalSec in section 'Service'` warning appeared after a deploy
  that had already fixed it — the entry was historical. Use
  `journalctl -u gable --since "1 minute ago"` when checking a restart, and
  `systemd-analyze verify` for the file itself.
- Bolt auto-enables an OAuth installation store when `SLACK_CLIENT_ID` and
  `SLACK_CLIENT_SECRET` are present, and then ignores the bot token. `app.py`
  hides those two before constructing the app. Do not "helpfully" remove that.

**Verified after deploy:** `systemctl is-active` active, `is-enabled` enabled,
a clean start with no unknown keys, 33 MB resident on a 1 GB box, and the local
copy stopped so exactly one listener holds the Socket Mode connection.
