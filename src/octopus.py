"""
Octopus 🐙 — Jazlab's weekly lunch pairing system.

Every Monday, Octopus posts a lunch offer in #octopus.
Lab members reply "🐙" or "in" to sign up.
First 3 to sign up get a funded lunch together ($20/person).
The bot polls Slack every 5 minutes for replies.

Commands (called by GitHub Actions):
  invite  — Monday 9am: post the weekly lunch offer
  poll    — Every 5 min: check for new sign-ups, enforce rules
  close   — Tuesday 9am: close the window, confirm or quietly drop
  nudge   — (future) follow-up nudge if they haven't lunched yet
"""

import os
import json
import logging
from datetime import datetime, timedelta
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── credentials ───────────────────────────────────────────────────────────
SLACK_BOT_TOKEN  = os.environ["SLACK_BOT_TOKEN"]
PI_SLACK_USER_ID = os.environ["PI_SLACK_USER_ID"]
KATIE_SLACK_USER_ID = os.environ["KATIE_SLACK_USER_ID"]

slack = WebClient(token=SLACK_BOT_TOKEN)

CHANNEL_NAME  = "octopus"
MAX_SIGNUPS   = 3
MIN_SIGNUPS   = 2
LUNCH_BUDGET  = 20   # $ per person

SIGNUP_TRIGGERS = {"🐙", "in", "In", "IN", "yes", "Yes", "YES"}


# ══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def get_or_create_channel():
    response = slack.conversations_list(types="public_channel", limit=200)
    for ch in response["channels"]:
        if ch["name"] == CHANNEL_NAME:
            return ch["id"]

    response = slack.conversations_create(name=CHANNEL_NAME, is_private=False)
    channel_id = response["channel"]["id"]
    slack.conversations_setTopic(
        channel=channel_id,
        topic="🐙 Octopus — weekly lab lunches. Sign up, show up, build bridges."
    )
    slack.conversations_setPurpose(
        channel=channel_id,
        purpose=(
            "Every Monday, Octopus offers a funded lab lunch for 2-3 people. "
            "Reply 🐙 to sign up. No agenda — just lunch and each other's work."
        )
    )
    logger.info(f"Created #{CHANNEL_NAME}: {channel_id}")
    return channel_id


def load_state():
    try:
        with open("octopus_state.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "current_post_ts":   None,   # ts of this week's offer post
            "signups":           [],     # list of {user_id, display_name, ts}
            "monthly_lunchers":  {},     # {user_id: "YYYY-MM"}
            "seen_ts":           [],     # reply timestamps already processed
            "window_open":       False,
        }


def save_state(state):
    with open("octopus_state.json", "w") as f:
        json.dump(state, f, indent=2)


def get_display_name(user_id):
    info = slack.users_info(user=user_id)["user"]
    return info.get("real_name") or info.get("name")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — WEEKLY INVITATION  (Monday 9am ET)
# ══════════════════════════════════════════════════════════════════════════════

def send_weekly_invitation():
    channel_id = get_or_create_channel()
    state = load_state()

    # reset for the new week
    state["current_post_ts"] = None
    state["signups"]         = []
    state["window_open"]     = True
    save_state(state)

    resp = slack.chat_postMessage(
        channel=channel_id,
        text=(
            "*🐙 This week's Octopus lunch is open.*\n\n"
            "The lab is covering *$20 per person* for 2–3 people to have lunch "
            "together this week — wherever you want, whenever works for you.\n\n"
            "No agenda. Just show up, eat, and learn a little about what your "
            "labmates are working on. Build some bridges.\n\n"
            "Reply *🐙* or *in* to sign up. "
            "All names are visible so you can see who's already in "
            "before committing.\n\n"
            "_Window closes Tuesday 9am. 3 spots — once they're filled, sign-ups close. "
            "If you lunched this month, your spot opens again next month._"
        )
    )
    state["current_post_ts"] = resp["ts"]
    save_state(state)
    logger.info(f"Weekly Octopus invitation posted: {resp['ts']}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — POLL  (every 5 minutes)
# ══════════════════════════════════════════════════════════════════════════════

def poll():
    channel_id = get_or_create_channel()
    state = load_state()

    if not state["window_open"] or not state["current_post_ts"]:
        return

    # fetch replies to this week's post
    try:
        replies = slack.conversations_replies(
            channel=channel_id,
            ts=state["current_post_ts"]
        )
    except SlackApiError as e:
        logger.error(f"Error fetching replies: {e}")
        return

    current_month = datetime.utcnow().strftime("%Y-%m")

    for msg in replies["messages"][1:]:   # skip original post
        ts      = msg.get("ts")
        user_id = msg.get("user")
        text    = msg.get("text", "").strip()

        if ts in state["seen_ts"]:
            continue
        if not user_id:
            continue

        state["seen_ts"].append(ts)

        # check if it's a signup trigger
        if text not in SIGNUP_TRIGGERS:
            continue

        # already signed up this week
        if any(s["user_id"] == user_id for s in state["signups"]):
            slack.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text="You're already signed up this week! :octopus:"
            )
            continue

        # monthly lockout
        if state["monthly_lunchers"].get(user_id) == current_month:
            slack.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text=(
                    "You already had an Octopus lunch this month — "
                    "your spot opens again next month! :wave:"
                )
            )
            continue

        # slots full
        if len(state["signups"]) >= MAX_SIGNUPS:
            slack.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text=(
                    "This week's lunch is full — try again next Monday! :octopus:"
                )
            )
            continue

        # accept signup
        display_name = get_display_name(user_id)
        state["signups"].append({
            "user_id":      user_id,
            "display_name": display_name,
            "ts":           ts,
        })

        slot_num = len(state["signups"])
        logger.info(f"Signup {slot_num}: {display_name}")

        # acknowledge privately
        slack.chat_postEphemeral(
            channel=channel_id, user=user_id,
            text=f"You're in! :octopus: Slot {slot_num} of {MAX_SIGNUPS}."
        )

        # update the post to show who's signed up (visible to all)
        _update_post_with_signups(channel_id, state)

        # if we hit max, close immediately
        if slot_num == MAX_SIGNUPS:
            state["window_open"] = False
            save_state(state)
            _confirm_match(channel_id, state)
            return

    save_state(state)


def _update_post_with_signups(channel_id, state):
    """Edit the original post to show current sign-ups so newcomers can see who's in."""
    if not state["signups"]:
        return

    names = " · ".join(s["display_name"] for s in state["signups"])
    remaining = MAX_SIGNUPS - len(state["signups"])

    try:
        slack.chat_update(
            channel=channel_id,
            ts=state["current_post_ts"],
            text=(
                "*🐙 This week's Octopus lunch is open.*\n\n"
                "The lab is covering *$20 per person* for 2–3 people to have lunch "
                "together this week — wherever you want, whenever works for you.\n\n"
                "No agenda. Just show up, eat, and learn a little about what your "
                "labmates are working on. Build some bridges.\n\n"
                "Reply *🐙* or *in* to sign up. "
                "All names are visible so you can see who's already in "
                "before committing.\n\n"
                f"*Already in:* {names}\n"
                f"*Spots remaining:* {remaining}\n\n"
                "_Window closes Tuesday 9am. 3 spots — once they're filled, sign-ups close. "
                "If you lunched this month, your spot opens again next month._"
            )
        )
    except SlackApiError as e:
        logger.error(f"Error updating post: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — CLOSE  (Tuesday 9am ET)
# ══════════════════════════════════════════════════════════════════════════════

def close_window():
    channel_id = get_or_create_channel()
    state = load_state()

    if not state["window_open"]:
        return   # already closed (hit max signups during the week)

    state["window_open"] = False
    save_state(state)

    signups = state["signups"]

    if len(signups) < MIN_SIGNUPS:
        # quietly let it go — no public announcement, no one is left hanging
        logger.info(f"Not enough signups ({len(signups)}) — rolling over silently.")
        # update post to remove the open call
        try:
            slack.chat_update(
                channel=channel_id,
                ts=state["current_post_ts"],
                text=(
                    "*🐙 This week's Octopus lunch is closed.*\n\n"
                    "Not enough sign-ups this time — the window opens again next Monday."
                )
            )
        except SlackApiError:
            pass
        return

    # enough signups — confirm the match
    _confirm_match(channel_id, state)


def _confirm_match(channel_id, state):
    signups      = state["signups"]
    current_month = datetime.utcnow().strftime("%Y-%m")
    names        = ", ".join(s["display_name"] for s in signups)
    user_ids     = [s["user_id"] for s in signups]

    # mark all as lunched this month
    for s in signups:
        state["monthly_lunchers"][s["user_id"]] = current_month
    save_state(state)

    # open a group DM with all participants
    dm = slack.conversations_open(users=",".join(user_ids))
    dm_channel = dm["channel"]["id"]

    slack.chat_postMessage(
        channel=dm_channel,
        text=(
            f"*🐙 You're this week's Octopus lunch!*\n\n"
            f"*{names}* — you're having lunch together this week. "
            f"Pick a day and place that works for all of you.\n\n"
            f"The lab is covering *${LUNCH_BUDGET} per person*. "
            f"Keep your receipt and send it to Katie to be reimbursed.\n\n"
            f"No agenda. Use the time to learn about each other's work, "
            f"share what you're excited about, what you're puzzling over. "
            f"Build some bridges. :octopus:\n\n"
            f"_Enjoy._"
        )
    )

    # notify PI and Katie
    summary = " · ".join(s["display_name"] for s in signups)
    for notify_id in [PI_SLACK_USER_ID, KATIE_SLACK_USER_ID]:
        slack.chat_postMessage(
            channel=notify_id,
            text=(
                f"*🐙 Octopus lunch this week:* {summary}\n"
                f"Katie — expecting a receipt from each of them, "
                f"up to ${LUNCH_BUDGET}/person."
            )
        )

    logger.info(f"Octopus match confirmed: {summary}")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINTS
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    commands = {
        "invite": send_weekly_invitation,
        "poll":   poll,
        "close":  close_window,
    }
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in commands:
        commands[command]()
    else:
        print(f"Unknown command: '{command}'")
        print(f"Usage: python octopus.py [{' | '.join(commands)}]")
        sys.exit(1)
