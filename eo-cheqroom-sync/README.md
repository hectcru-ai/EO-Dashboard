# EO Cheqroom Sync

Pulls this week's orders from Cheqroom every 15 minutes and publishes them as a
plain JSON file the order board reads. Read-only — never writes anything back
to Cheqroom.

## Setup (one-time)

1. **Create the repo.** On GitHub, create a new repository (private is fine)
   and upload these files, keeping the folder structure as-is (the
   `.github/workflows/sync.yml` path matters).

2. **Add your Cheqroom API key as a secret.**
   Repo → Settings → Secrets and variables → Actions → New repository secret.
   - Name: `CHEQROOM_API_KEY`
   - Value: the key you generated in Cheqroom (Settings → Integrations → API).

3. **Turn on GitHub Pages.**
   Repo → Settings → Pages → Source: "Deploy from a branch" → Branch: `main`,
   folder: `/docs`. Save. GitHub will give you a URL like
   `https://<your-username>.github.io/<repo-name>/orders.json` — that's the
   URL the board will fetch from.

4. **Test it manually before trusting the schedule.**
   Repo → Actions → "Sync Cheqroom orders" → Run workflow. Then click into
   the run and open the "Run sync script" step's log.
   - ✅ If it says `Wrote N order(s) to ...` — it worked. Check
     `docs/orders.json` in the repo to see the real data.
   - ❌ If it fails, the log will print the response Cheqroom sent back and
     which line to fix. Paste that log back to Claude and it'll get corrected —
     the endpoint/header names in `sync_cheqroom.py` are marked as best
     guesses, not confirmed, until this first run succeeds.

5. Once a manual run succeeds, the schedule (every 15 minutes) takes over
   automatically — no further action needed.

## Files

- `sync_cheqroom.py` — the script that talks to Cheqroom.
- `.github/workflows/sync.yml` — runs the script on a timer via GitHub Actions.
- `docs/orders.json` — the output file GitHub Pages serves; the board fetches
  this URL.
