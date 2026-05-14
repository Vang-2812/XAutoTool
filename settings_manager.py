import json
import os
import uuid

SETTINGS_FILE = "settings.json"

# Default bot settings for a new account
_DEFAULT_ACCOUNT_SETTINGS = {
    "ai_model": "gpt-4o-mini",
    "max_posts_scan": 20,
    "max_comments_post": 5,
    "view_threshold": 1000,
    "comment_strategy": "Reply to Post",
    "min_comment_views": 1000,
    "custom_prompt": (
        "- Each reply ≤ 310 characters.\n"
        "- Replies must be in the SAME LANGUAGE as the post / comments provided.\n"
        "- Tone: natural, human — slight typos allowed — humorous / professional / viral as fits.\n"
        "- Content: light analysis of the post with an expert-like, engaging angle.\n"
        "- Avoid sensitive, harmful, or policy-violating language.\n"
        "- Goal: maximize views (10k+) and shareability."
    ),
    "premium_only": False,
    "skip_sponsored": False,
    "auto_follow_high_ratio": False,
}

# Default settings for a single plan step
_DEFAULT_STEP_SETTINGS = {
    "strategy": "Reply to Post",
    "trigger": "delay",          # "delay" or "time"
    "delay_minutes": 0,
    "scheduled_time": "",         # "HH:MM" (24h, local time)
    "ai_model": "gpt-4o-mini",
    "max_posts_scan": 20,
    "max_comments_post": 5,
    "view_threshold": 1000,
    "min_comment_views": 1000,
    "premium_only": False,
    "skip_sponsored": False,
    "auto_follow_high_ratio": False,
    "custom_prompt": (
        "- Each reply ≤ 310 characters.\n"
        "- Replies must be in the SAME LANGUAGE as the post / comments provided.\n"
        "- Tone: natural, human — slight typos allowed — humorous / professional / viral as fits.\n"
        "- Content: light analysis of the post with an expert-like, engaging angle.\n"
        "- Avoid sensitive, harmful, or policy-violating language.\n"
        "- Goal: maximize views (10k+) and shareability."
    ),
}

STEP_SETTING_KEYS = list(_DEFAULT_STEP_SETTINGS.keys())

# Keys that belong to global (shared) config
_GLOBAL_KEYS = ["openai_api_key", "gemini_api_key", "deepseek_api_key", "deepseek_base_url"]

# Keys that belong to per-account config
ACCOUNT_SETTING_KEYS = list(_DEFAULT_ACCOUNT_SETTINGS.keys())


def _default_global():
    return {
        "openai_api_key": "",
        "gemini_api_key": "",
        "deepseek_api_key": "",
        "deepseek_base_url": "https://api.deepseek.com",
    }


def _make_account(account_id=None, name="Account 1"):
    """Create a new account dict with default settings."""
    return {
        "id": account_id or f"acc_{uuid.uuid4().hex[:8]}",
        "name": name,
        **_DEFAULT_ACCOUNT_SETTINGS.copy(),
    }


def load_settings():
    """
    Load settings from disk.
    Handles migration from old single-account format to new multi-account format.
    Returns: { "global": {...}, "accounts": [{...}, ...] }
    """
    defaults = {
        "global": _default_global(),
        "accounts": [_make_account(account_id="default", name="Account 1")],
    }

    if not os.path.exists(SETTINGS_FILE):
        return defaults

    # Try utf-8 first; fall back to cp1252 for old Windows-encoded files
    raw = None
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            with open(SETTINGS_FILE, "r", encoding=enc) as f:
                raw = json.load(f)
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

    if raw is None:
        # Could not decode at all — return defaults
        return defaults

    # --- Migration: old flat format → new multi-account format ---
    if "accounts" not in raw:
        # Old format detected — convert
        global_cfg = {}
        for k in _GLOBAL_KEYS:
            global_cfg[k] = raw.get(k, _default_global().get(k, ""))

        account_cfg = _make_account(account_id="default", name="Account 1")
        for k in ACCOUNT_SETTING_KEYS:
            if k in raw:
                account_cfg[k] = raw[k]

        return {"global": global_cfg, "accounts": [account_cfg]}

    # New format — ensure all keys present
    result = {"global": {}, "accounts": []}

    # Merge global defaults
    g = _default_global()
    g.update(raw.get("global", {}))
    result["global"] = g

    # Merge account defaults
    for acc in raw.get("accounts", []):
        merged = _make_account(account_id=acc.get("id"), name=acc.get("name", "Unnamed"))
        for k in ACCOUNT_SETTING_KEYS:
            if k in acc:
                merged[k] = acc[k]
        merged["id"] = acc.get("id", merged["id"])
        merged["name"] = acc.get("name", merged["name"])
        # Preserve the plans list which isn't in ACCOUNT_SETTING_KEYS
        if "plans" in acc:
            merged["plans"] = acc["plans"]
        result["accounts"].append(merged)

    if not result["accounts"]:
        result["accounts"] = [_make_account(account_id="default", name="Account 1")]

    return result


def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)


def add_account(settings, name="New Account"):
    """Add a new account to settings and return the updated settings + new account."""
    acc = _make_account(name=name)
    settings["accounts"].append(acc)
    save_settings(settings)
    return settings, acc


def remove_account(settings, account_id):
    """Remove an account by id. Cannot remove the last remaining account."""
    if len(settings["accounts"]) <= 1:
        return settings
    settings["accounts"] = [a for a in settings["accounts"] if a["id"] != account_id]
    save_settings(settings)
    return settings


def get_account_by_id(settings, account_id):
    """Return the account dict with the given id, or None."""
    for acc in settings["accounts"]:
        if acc["id"] == account_id:
            return acc
    return None


def get_profile_dir(account_id):
    """Return the browser profile directory path for an account."""
    if account_id == "default":
        return "x_profile"
    return f"x_profile_{account_id}"


# ------------------------------------------------------------------
# Plan & Step helpers
# ------------------------------------------------------------------

def _make_step(overrides: dict = None) -> dict:
    """Create a new plan step with default settings."""
    step = _DEFAULT_STEP_SETTINGS.copy()
    if overrides:
        for k in STEP_SETTING_KEYS:
            if k in overrides:
                step[k] = overrides[k]
    return step


def _make_plan(name: str = "New Plan") -> dict:
    """Create a new plan with one default step."""
    return {
        "id": f"plan_{uuid.uuid4().hex[:8]}",
        "name": name,
        "enabled": True,
        "steps": [_make_step()],
    }


def get_plans_for_account(settings: dict, account_id: str) -> list:
    """Return the plans list for an account, ensuring it exists."""
    acc = get_account_by_id(settings, account_id)
    if acc is None:
        return []
    return acc.setdefault("plans", [])


def add_plan(settings: dict, account_id: str, name: str = "New Plan") -> dict:
    """Add a new plan to the given account. Returns the new plan dict."""
    plans = get_plans_for_account(settings, account_id)
    plan = _make_plan(name=name)
    plans.append(plan)
    save_settings(settings)
    return plan


def remove_plan(settings: dict, account_id: str, plan_id: str):
    """Remove a plan by id from the given account."""
    acc = get_account_by_id(settings, account_id)
    if acc is None:
        return
    acc["plans"] = [p for p in acc.get("plans", []) if p["id"] != plan_id]
    save_settings(settings)


def get_plan_by_id(settings: dict, account_id: str, plan_id: str):
    """Return a specific plan dict, or None."""
    for p in get_plans_for_account(settings, account_id):
        if p["id"] == plan_id:
            return p
    return None


def add_step_to_plan(settings: dict, account_id: str, plan_id: str, overrides: dict = None) -> dict | None:
    """Append a new step to an existing plan. Returns the new step, or None."""
    plan = get_plan_by_id(settings, account_id, plan_id)
    if plan is None:
        return None
    step = _make_step(overrides)
    plan["steps"].append(step)
    save_settings(settings)
    return step


def remove_step_from_plan(settings: dict, account_id: str, plan_id: str, step_index: int):
    """Remove a step by index from a plan."""
    plan = get_plan_by_id(settings, account_id, plan_id)
    if plan is None or step_index < 0 or step_index >= len(plan["steps"]):
        return
    plan["steps"].pop(step_index)
    save_settings(settings)


def make_step_from_account(acc: dict) -> dict:
    """Create a plan step pre-filled with the account's current settings."""
    return _make_step({
        "strategy": acc.get("comment_strategy", "Reply to Post"),
        "ai_model": acc.get("ai_model", "gpt-4o-mini"),
        "max_posts_scan": acc.get("max_posts_scan", 20),
        "max_comments_post": acc.get("max_comments_post", 5),
        "view_threshold": acc.get("view_threshold", 1000),
        "min_comment_views": acc.get("min_comment_views", 1000),
        "premium_only": acc.get("premium_only", False),
        "skip_sponsored": acc.get("skip_sponsored", False),
        "auto_follow_high_ratio": acc.get("auto_follow_high_ratio", False),
        "custom_prompt": acc.get("custom_prompt", ""),
    })
