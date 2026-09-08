# Octopus 🐙

> *No agenda. Just lunch and each other's work. Build some bridges.*

Octopus is Jazlab's weekly lab lunch system. Every Monday it posts a funded lunch offer in Slack for up to 3 lab members. People sign up voluntarily — all names are visible so you can see who's already in before committing. Once 3 people sign up, the lunch is full. No structure, no agenda — just food and the chance to learn what your labmates are actually working on.

**Runs entirely on GitHub Actions. No server. No external URL. Nothing to host or maintain.**

---

## How it works

**Monday 9am** — Octopus posts the weekly offer in `#octopus`. Anyone replies 🐙 or "in" to sign up.

**During the week** — As people sign up, the post updates live to show everyone who's already in. All names are visible so you can see who you'd be lunching with before committing.

**3 spots, then closed** — Once 3 people sign up, the lunch is full and further sign-ups are blocked. No waitlist.

**Tuesday 9am** — If the window hasn't already closed (3 spots filled), it closes automatically.
- 0–1 sign-ups → quietly rolls over. No public announcement. Nobody is left hanging.
- 2–3 sign-ups → match confirmed. The group gets a warm DM with instructions.

**The match DM** — tells them the lab covers $20/person, they pick the day and place that works for them, keep the receipt and send it to Katie for reimbursement. No agenda beyond learning about each other's work.

**Monthly lockout** — anyone who lunched sits out the rest of the month. Over a semester the whole lab cycles through naturally.

**PI + Katie notified** — both receive a Slack DM when a match is confirmed, so Katie is already expecting the receipt when it arrives.

---

## Setup — step by step

### 1. Create the Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App → From scratch**.
2. Name it `Octopus`. Select the **MIT** workspace.
3. Under **OAuth & Permissions → Bot Token Scopes**, add all of these:
   - `channels:manage` — create #octopus
   - `channels:read` — list channels
   - `channels:history` — read replies to the weekly post
   - `chat:write` — post messages
   - `chat:write.public` — post to channels the bot isn't in
   - `im:write` — send DMs
   - `mpim:write` — send group DMs to matched lunchers
   - `users:read` — get member names
4. Click **Install to Workspace**.
5. Copy the **Bot User OAuth Token** (starts with `xoxb-`). This is your `SLACK_BOT_TOKEN`.

> No webhook URL needed. Octopus polls Slack for new sign-ups rather than waiting for Slack to push events to a server.

---

### 2. Find the Slack User IDs you need

You need two Slack member IDs:

**PI's Slack user ID** — go to the PI's Slack profile → click the ··· menu → **Copy member ID**. This is your `PI_SLACK_USER_ID`.

**Katie's Slack user ID** — same process on Katie's profile. This is your `KATIE_SLACK_USER_ID`.

---

### 3. Create a GitHub Personal Access Token (PAT)

Octopus saves its state (who signed up, monthly lockouts) to a file in the repo after each run. It needs permission to do that.

1. Go to GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Set repository access to the **Jazlab GitHub repo** only.
3. Under **Repository permissions**, set **Contents** to **Read and write**.
4. Copy the token. This is your `GH_PAT`.

---

### 4. Add GitHub Secrets

In the Jazlab GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**.

Add each of these:

| Secret name | What it is |
|---|---|
| `SLACK_BOT_TOKEN` | From Step 1 — starts with `xoxb-` |
| `PI_SLACK_USER_ID` | From Step 2 |
| `KATIE_SLACK_USER_ID` | From Step 2 |
| `GH_PAT` | From Step 3 |

---

### 5. Add the bot to the workspace

In Slack, go to the `#octopus` channel (or create it) and invite the Octopus bot:
`/invite @Octopus`

If `#octopus` doesn't exist yet, the bot will create it automatically on its first run.

---

### 6. Adjust timing if needed

The default schedule is **Monday 9am ET** for the invite and **Tuesday 9am ET** to close the window. If your lab runs on a different timezone or schedule, edit these two lines in `.github/workflows/octopus.yml`:

```yaml
- cron: "0 14 * * 1"   # Monday 9am ET  (UTC = ET + 5 in winter, ET + 4 in summer)
- cron: "0 14 * * 2"   # Tuesday 9am ET
```

Use [crontab.guru](https://crontab.guru) to build the right expression for your timezone. The format is `minute hour day month weekday` in UTC.

---

### 7. Test it

Go to the **Actions tab** in the Jazlab GitHub repo → **Octopus 🐙** → **Run workflow** → select `invite` → **Run workflow**.

Within a minute you should see the weekly lunch offer appear in `#octopus`. Reply 🐙 to test the sign-up flow.

---

## Files

```
octopus/
├── src/
│   ├── octopus.py              # all bot logic
│   └── octopus_state.json      # persistent state (auto-updated by Actions)
├── .github/
│   └── workflows/
│       └── octopus.yml         # GitHub Actions scheduler
├── requirements.txt            # one Python package (slack-sdk)
└── README.md
```

---

## How the pieces fit together

**`octopus.py`** — the brain. Handles posting the weekly offer, processing sign-ups, enforcing the monthly lockout, confirming matches, and notifying the PI and Katie. Three commands: `invite`, `poll`, `close`.

**`octopus.yml`** — the heartbeat. GitHub Actions runs `invite` on Monday morning, `poll` every 5 minutes throughout the week to catch new sign-ups, and `close` on Tuesday morning.

**`octopus_state.json`** — the memory. Tracks who signed up, who lunched this month, and which Slack messages have already been processed. Gets committed back to the repo automatically after each run.

---

## Maintaining it

**State** lives in `octopus_state.json` and is committed automatically. You never need to touch it.

**Timing** — adjust the cron expressions in `octopus.yml` if the lab meeting schedule changes.

**New lab member** — just invite them to `#octopus`. The bot discovers members automatically.

**Something broke** — check the **Actions tab** in GitHub. Each run shows complete logs. Most failures will be a missing or expired secret.

**Changing the lunch budget** — edit the `LUNCH_BUDGET = 20` line near the top of `octopus.py`.

---

## Questions?

Open an issue in the Jazlab GitHub repo.
