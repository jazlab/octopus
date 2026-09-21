"""
Octopus 🐙 — Jazlab's weekly lunch pairing system.

Every Monday, Octopus posts a lunch offer in #octopus-lunch.
Lab members reply "🐙" or "in" to sign up.
First 3 to sign up get a funded lunch together ($20/person).
All times are Boston (America/New_York), handling EDT/EST automatically.

Commands (called by GitHub Actions):
  auto    — Default: decide invite/poll/close from Boston wall-clock time
  invite  — Post the weekly lunch offer
  poll    — Check for new sign-ups, enforce rules
  close   — Close the window, confirm or quietly drop
"""

import os
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── credentials ───────────────────────────────────────────────────────────
SLACK_BOT_TOKEN  = os.environ["SLACK_BOT_TOKEN"]
PI_SLACK_USER_ID = os.environ["PI_SLACK_USER_ID"]
KATIE_SLACK_USER_ID = os.environ["KATIE_SLACK_USER_ID"]

slack = WebClient(token=SLACK_BOT_TOKEN)

BOSTON_TZ      = ZoneInfo("America/New_York")
CHANNEL_NAME   = "octopus-lunch"
MAX_SIGNUPS   = 3
MIN_SIGNUPS   = 2
LUNCH_BUDGET  = 20   # $ per person

# Normalized to lowercase; matching strips punctuation and whitespace.
# The octopus emoji is checked separately (unicode variants).
SIGNUP_WORDS = {"in", "yes", "i'm in", "im in", "me", "down"}
SIGNUP_EMOJI = {"🐙"}  # U+1F419; Slack may send skin-tone or variant selectors


def _is_signup(text: str) -> bool:
    """Decide whether a Slack reply is a sign-up.

    Handles common variations:
      - Case and whitespace:  "In", " YES ", "I'm in"
      - Trailing punctuation: "in!", "yes."
      - Emoji variants:       octopus with variation selector / skin tone
      - Slack rich-text:      ":octopus:" shortcode alongside the emoji
    """
    # Strip variation selectors / zero-width joiners that Slack sometimes appends
    cleaned = "".join(
        ch for ch in text
        if unicodedata.category(ch) not in ("Mn", "Cf")  # marks, format chars
    ).strip()

    # Check for octopus emoji anywhere in the message
    for emoji in SIGNUP_EMOJI:
        if emoji in cleaned:
            return True

    # Slack may send the shortcode instead of the unicode char
    if ":octopus:" in cleaned.lower():
        return True

    # Normalize: lowercase, strip punctuation, collapse whitespace
    normalized = re.sub(r"[^\w\s']", "", cleaned.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)

    return normalized in SIGNUP_WORDS


# ══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def get_or_create_channel():
    try:
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
    except SlackApiError as e:
        if e.response["error"] != "name_taken":
            raise

    cursor = None
    while True:
        response = slack.conversations_list(
            types="public_channel", limit=1000, cursor=cursor,
        )
        for ch in response["channels"]:
            if ch["name"] == CHANNEL_NAME:
                try:
                    slack.conversations_join(channel=ch["id"])
                except SlackApiError:
                    pass
                return ch["id"]
        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    raise RuntimeError(f"#{CHANNEL_NAME} exists but could not be found via API")


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
            "history":           [],     # list of {date, members}
        }


def save_state(state):
    with open("octopus_state.json", "w") as f:
        json.dump(state, f, indent=2)


def get_display_name(user_id):
    info = slack.users_info(user=user_id)["user"]
    return info.get("real_name") or info.get("name")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — WEEKLY INVITATION  (Monday 9am Boston)
# ══════════════════════════════════════════════════════════════════════════════

def send_weekly_invitation():
    channel_id = get_or_create_channel()
    state = load_state()

    # reset for the new week
    state["current_post_ts"] = None
    state["signups"]         = []
    state["window_open"]     = True
    save_state(state)

    # date strings for the message (Boston time, handles EDT/EST automatically)
    today = datetime.now(BOSTON_TZ)
    monday_str = today.strftime("%b %-d")
    tuesday = today + timedelta(days=1)
    tuesday_str = tuesday.strftime("%b %-d")

    resp = slack.chat_postMessage(
        channel=channel_id,
        text=(
            f"*🐙 This week's Octopus lunch is open ({monday_str}).*\n\n"
            "The lab is covering *$20 per person* for 2–3 people to have lunch "
            "together this week — wherever you want, whenever works for you.\n\n"
            "No agenda. Just show up, eat, and learn a little about what your "
            "labmates are working on. Build some bridges.\n\n"
            "Reply *🐙* or *in* to sign up. "
            "All names are visible so you can see who's already in "
            "before committing.\n\n"
            f"_Window closes Tuesday ({tuesday_str}) 9am. 3 spots — once they're filled, sign-ups close. "
            "If you lunched this month, your spot opens again next month._"
        )
    )
    state["current_post_ts"] = resp["ts"]
    save_state(state)
    logger.info(f"Weekly Octopus invitation posted: {resp['ts']}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — POLL  (hourly, or on demand)
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

    current_month = datetime.now(BOSTON_TZ).strftime("%Y-%m")

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
        if not _is_signup(text):
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

    # date strings for the message (Boston time, handles EDT/EST automatically)
    today = datetime.now(BOSTON_TZ)
    # find the Monday of this week
    monday = today - timedelta(days=today.weekday())
    monday_str = monday.strftime("%b %-d")
    tuesday = monday + timedelta(days=1)
    tuesday_str = tuesday.strftime("%b %-d")

    try:
        slack.chat_update(
            channel=channel_id,
            ts=state["current_post_ts"],
            text=(
                f"*🐙 This week's Octopus lunch is open ({monday_str}).*\n\n"
                "The lab is covering *$20 per person* for 2–3 people to have lunch "
                "together this week — wherever you want, whenever works for you.\n\n"
                "No agenda. Just show up, eat, and learn a little about what your "
                "labmates are working on. Build some bridges.\n\n"
                "Reply *🐙* or *in* to sign up. "
                "All names are visible so you can see who's already in "
                "before committing.\n\n"
                f"*Already in:* {names}\n"
                f"*Spots remaining:* {remaining}\n\n"
                f"_Window closes Tuesday ({tuesday_str}) 9am. 3 spots — once they're filled, sign-ups close. "
                "If you lunched this month, your spot opens again next month._"
            )
        )
    except SlackApiError as e:
        logger.error(f"Error updating post: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — CLOSE  (Tuesday 9am Boston)
# ══════════════════════════════════════════════════════════════════════════════

def close_window():
    poll()

    state = load_state()

    if not state["window_open"]:
        return   # already closed (hit max signups during the week)

    channel_id = get_or_create_channel()
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
    current_month = datetime.now(BOSTON_TZ).strftime("%Y-%m")
    names        = ", ".join(s["display_name"] for s in signups)
    user_ids     = [s["user_id"] for s in signups]

    # mark all as lunched this month
    for s in signups:
        state["monthly_lunchers"][s["user_id"]] = current_month

    # append to persistent history
    if "history" not in state:
        state["history"] = []
    state["history"].append({
        "date": datetime.now(BOSTON_TZ).strftime("%Y-%m-%d"),
        "members": [s["display_name"] for s in signups],
    })

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
# AUTO — decide command from Boston wall-clock time
# ══════════════════════════════════════════════════════════════════════════════

def auto():
    """Pick the right command based on Boston time.

    Monday  8am-4pm → invite (wide window; state guard prevents double-invite)
    Tuesday 8am-4pm → close
    Everything else → poll
    """
    now = datetime.now(BOSTON_TZ)
    day = now.weekday()   # 0=Mon, 1=Tue
    hour = now.hour
    state = load_state()

    if day == 0 and 8 <= hour < 16:
        if state["window_open"] or state["current_post_ts"]:
            logger.info(f"Auto: Monday {now.strftime('%I:%M %p %Z')} → already invited, poll")
            poll()
        else:
            logger.info(f"Auto: Monday {now.strftime('%I:%M %p %Z')} → invite")
            send_weekly_invitation()
    elif day == 1 and 8 <= hour < 16:
        logger.info(f"Auto: Tuesday {now.strftime('%I:%M %p %Z')} → close")
        close_window()
    else:
        logger.info(f"Auto: {now.strftime('%A %I:%M %p %Z')} → poll")
        poll()


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINTS
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    commands = {
        "auto":   auto,
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
