import json
import os

SETTINGS_FILE = "settings.json"

def load_settings():
    defaults = {
        "openai_api_key": "",
        "gemini_api_key": "",
        "deepseek_api_key": "",
        "deepseek_base_url": "https://ds2api-peach-two.vercel.app/v1",
        "ai_model": "gpt-4o-mini",
        "max_posts_scan": 20,
        "max_comments_post": 5,
        "view_threshold": 1000,
        "comment_strategy": "Reply to Post",
        "min_comment_views": 1000,
    }
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            settings = json.load(f)
            # Merge defaults to handle missing keys
            for key, value in defaults.items():
                if key not in settings:
                    settings[key] = value
            return settings
    return defaults

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)
