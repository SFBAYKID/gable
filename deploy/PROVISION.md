# Droplet provisioning — run by Chase

Every step here needs either a DigitalOcean console session or `sudo`. I do not
handle account passwords and I do not enter credentials anywhere (CLAUDE.md §3),
so this is yours to run. Once it is done, `make deploy` is mine.

**Verification status:** these commands follow Ubuntu and systemd documentation
and the constraints in CLAUDE.md §9 / ARCHITECTURE.md §7. **I have not run them
against a droplet** — none exists yet. Anything that behaves differently on the
real box is a bug I fix, not a surprise you work around. Tell me what it printed.

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
