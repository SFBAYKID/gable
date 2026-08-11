# Gable — project brief

Everything the building agent needs to start. Hand this whole folder to the
agent in PyCharm.

---

## The name

**Gable.** A gable is the triangular end of a pitched roof — the shape every
house icon is already drawn from, which is why the logo writes itself. One
syllable, fast to type as `@gable`, professional without being cute, and it
won't sound dated in two years.

Alternates considered, in case you want to change it (it's a find-and-replace
across these files plus one line in the Slack manifest):

| Name | Angle |
|---|---|
| **Curb** | From "curb appeal." Real-estate native, very short. |
| **Placard** | The sign in the front yard — literally the artifact being made. |
| **Shingle** | "Hanging out your shingle," plus roof shingle. Warmer, more playful. |
| **Foyer** | Entryway. Softer, more hospitality than real estate. |

## The logo

`assets/gable-icon-512.png` — 512×512, Slack's required size.

A warm amber gable roof sitting on a white flyer, on a deep slate ground. The
mark reads as *house* and *document* simultaneously, which is exactly what the
agent does. It stays legible at 24px in a Slack sidebar, which is where it will
actually live. Background color `#16222E` is set in the manifest to match.

---

## What's in this folder

| File | Purpose |
|---|---|
| `CLAUDE.md` | Rules for the agent building this. Honesty, code standards, what's verified vs. guessed. **Read first.** |
| `ARCHITECTURE.md` | System design, data model, decision log. |
| `AGENTS.md` | How Gable behaves at runtime — Slack message formats, prohibitions. |
| `slack/manifest.json` | Paste into api.slack.com to create or update the app. |
| `.env.example` | Every config variable, documented. |
| `assets/gable-icon-512.png` | Slack app icon. |

---

## Setup order

Things that must happen in sequence, because each depends on the last.

**1. You (Chase), not the agent — these involve credentials**

- Create the Slack app from `slack/manifest.json`, upload the icon, generate the
  app-level token, install to the workspace, `/invite @Gable` to `C0BP597644B`.
- Create a Google Cloud service account, enable the Sheets and Drive APIs,
  download the JSON key.
- **Share the Sheet with the service account's `client_email` as Editor.** It
  does not inherit your access — this step is missed constantly.
- Create the droplet, add your SSH public key, disable password auth.
- Create the Spaces bucket if you're using one.
- Get a Firecrawl API key.

**2. Spike A — before any application code**

Open your flyer template in Canva, go to Apps → Bulk create → Upload data, and
upload a two-row CSV with a column containing an image URL. Find out whether
Canva accepts a URL in an image column from an *uploaded file*.

I confirmed image columns exist in Bulk Create's **manual** data-entry table —
there are literal "Add text" and "Add image" buttons, and adding an image column
produces a distinct image-typed column. I did **not** confirm it for uploaded
files, and Phase 1's entire output format rests on that.

If uploaded files can't carry image URLs, stop and tell me. The fix is probably
moving Phase 2 forward, not working around it.

**3. Then the agent builds Phase 1.**

---

## Things I want to be straight with you about

**The Slack app is live.** Bot authentication and Socket Mode were verified.
`files:read` from `slack/manifest.json` is installed: on 2026-08-11 a real
thread upload was fetched and measured successfully. A watched upload after the
automatic AI upscale deployment reached the image model successfully; its
derivative failed the seam gate and correctly fell back to the original. That
run then exposed a server-directory ownership fault, repaired at 10:54 Pacific.
A finished live flyer from that repaired path is still pending.

**The deployed droplet is the $6, 1 GB tier with 1 GB swap.** Slack uploads are
capped at 25 MB before Pillow opens them. The full photo workflow still needs a
live RSS measurement before adding a systemd memory limit.

**Socket Mode means polling.** No inbound port means no Apps Script webhook, so
the Sheet is checked every two minutes from 7 AM Central through 7 PM Pacific
and every ten minutes otherwise, weekends included.

**Hero photos come from Carmen in the listing's Slack thread.** The form's Drive
links are inaccessible to Gable and often contain several photos with no hero
choice. The upload is fitted locally, served by nginx, verified anonymously,
and resumes the same paused run.

**On AI-generated house photos.** You said you're not sure and lean toward
generating freely. I built it as a config switch defaulting to
`generate_with_approval`, so nothing is locked in either direction.

My concern isn't Canva's disclosure rule. It's that an image model can't know
what 123 Main St. looks like — given the address it will invent a house, and
that lands a factually wrong photograph on marketing material for a specific
property someone can drive to. You made the case yourself that these photos are
public and easy to find; that's precisely what makes this a retrieval problem
rather than a generation one. AI's good job here is cleaning up the photo you
found.

If you set `generate_freely`, the code honors it — but generated images stay
tagged, badged in Slack, and logged in the `Runs` tab. I'd rather you flip that
switch on purpose than inherit it by accident.

**Credentials.** You offered logins for Canva, DigitalOcean, Slack, and Google.
I won't enter a password anywhere, and `CLAUDE.md` §3 instructs the building
agent not to either. The split is: you do the console and OAuth steps, the agent
works from tokens in `.env`. Same for the droplet — SSH key, never a password.

---

## What's still unknown

Carried into `CLAUDE.md` §4.3 so the building agent can't miss it:

1. Whether uploaded CSV/XLSX can carry image URLs into Bulk Create. **Blocking.**
2. Bulk Create's max rows per batch.
3. Whether Bulk Create preserves brand-template layout, and how it handles
   overflowing text.
4. Whether a private data-connector app can ship to a Teams team without Canva
   marketplace review. **Gates Phase 2.**
5. The Canva autofill trial quota — `canva_autofill_spike.py` prints it, but has
   never been run against the live API.

---

## What I actually verified, and how

In your live Canva account on 2026-08-10, via your browser:

- Plan is Canva Teams, $30/month, 2 members.
- Bulk Create opens with **no upgrade wall** on that plan.
- Its "Select data source" panel lists live connector apps: Google Sheets,
  Google Analytics, Meta SKU Catalog, BigQuery, Snowflake, HubSpot Data, QrDy,
  SheetSync. Canva's docs claim data connectors need Business or Enterprise —
  your account contradicts the docs, so trust the account.
- The manual data table has "Add text" and "Add image" buttons, and adding an
  image column produces a distinctly image-typed column.

From Canva's published documentation:

- Autofill via the Connect API requires Enterprise; the response carries
  `trial_information.uses_remaining`.
- Data-connector image cells are `{type: 'image_upload', url, thumbnailUrl,
  mimeType}` — external HTTPS URL, ≤4096 chars, ≤50MB, with an `ai_disclosure`
  field.

The scratch design I created while testing is still in your Canva — "White Beige
Modern House for Sale Flyer." Delete it whenever.
