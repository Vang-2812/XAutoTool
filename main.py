import streamlit as st
import pandas as pd
import threading
import time
import shutil
from streamlit.runtime.scriptrunner import get_script_run_ctx, add_script_run_ctx
from bot_engine import XBot, start_login_session
from settings_manager import load_settings, save_settings
from db_manager import init_db, get_history
import os

# Page Config
st.set_page_config(page_title="X AutoBot", page_icon="🐦", layout="wide")

USER_DATA_DIR = "x_profile"  # must match bot_engine.py

# Initialize DB
init_db()

# Load Settings
if "settings" not in st.session_state:
    st.session_state.settings = load_settings()

if "logs" not in st.session_state:
    st.session_state.logs = []

if "is_running" not in st.session_state:
    st.session_state.is_running = False

if "status_text" not in st.session_state:
    st.session_state.status_text = "Idle"

if "progress" not in st.session_state:
    st.session_state.progress = 0.0

# Ensure new settings keys exist with defaults
st.session_state.settings.setdefault("comment_strategy", "Reply to Post")
st.session_state.settings.setdefault("min_comment_views", 1000)
st.session_state.settings.setdefault("deepseek_api_key", "")
st.session_state.settings.setdefault("deepseek_base_url", "https://ds2api-peach-two.vercel.app/v1")

# Helper to update status from thread
def update_status(text):
    st.session_state.status_text = text
    st.session_state.logs.append({"Time": time.strftime("%H:%M:%S"), "Message": text})
    # Keep only last 50 logs
    if len(st.session_state.logs) > 50:
        st.session_state.logs.pop(0)

# Sidebar: Settings
with st.sidebar:
    st.title("⚙️ Settings")
    
    api_key = st.text_input("OpenAI API Key", value=st.session_state.settings["openai_api_key"], type="password")
    gemini_key = st.text_input("Gemini API Key", value=st.session_state.settings["gemini_api_key"], type="password")
    deepseek_key = st.text_input("DeepSeek API Key", value=st.session_state.settings["deepseek_api_key"], type="password")
    deepseek_url = st.text_input("DeepSeek Base URL", value=st.session_state.settings["deepseek_base_url"])
    
    models = ["gpt-4o", "gpt-4o-mini", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro", "deepseek-v4-flash", "deepseek-v4-pro"]
    current_model = st.session_state.settings["ai_model"]
    model_index = models.index(current_model) if current_model in models else 1
    model = st.selectbox("AI Model", models, index=model_index)
    
    st.divider()
    
    max_posts = st.number_input("Max Posts to Scan", min_value=1, max_value=500, value=st.session_state.settings["max_posts_scan"])
    max_comments = st.number_input("Max Comments to Post", min_value=1, max_value=100, value=st.session_state.settings["max_comments_post"])
    view_threshold = st.number_input("Post View Threshold", min_value=0, value=st.session_state.settings["view_threshold"])

    st.divider()
    st.markdown("**💬 Comment Strategy**")
    strategies = ["Reply to Post", "Mimic Top Comments", "Reply if Latest Comment Active", "Re-Reply to Post"]
    current_strategy = st.session_state.settings["comment_strategy"]
    strategy_index = strategies.index(current_strategy) if current_strategy in strategies else 0
    comment_strategy = st.selectbox(
        "How to generate replies",
        strategies,
        index=strategy_index,
        help=(
            "**Reply to Post**: AI reads the post and crafts a direct reply.\n\n"
            "**Mimic Top Comments**: AI reads top-viewed comments and generates a similar one.\n\n"
            "**Reply if Latest Comment Active**: Opens Latest tab — only replies if the most recent "
            "comment meets the Min Comment Views threshold.\n\n"
            "**Re-Reply to Post**: Scans your account's Replies. If a reply has high views, "
            "it posts the next queued variant from the 5 saved comments for that post."
        )
    )

    min_comment_views = st.number_input(
        "Min Comment Views (Mimic / Active mode)",
        min_value=0,
        value=st.session_state.settings["min_comment_views"],
        help="Mimic mode: only use comments with ≥ this many views.\nLatest-Active mode: skip post if the latest comment has fewer views than this.",
        disabled=(comment_strategy == "Reply to Post")
    )
    # Auto-sync: always keep session_state and settings.json up to date
    # with whatever is currently shown in the sidebar (no Save button needed).
    _current = {
        "openai_api_key": api_key,
        "gemini_api_key": gemini_key,
        "deepseek_api_key": deepseek_key,
        "deepseek_base_url": deepseek_url,
        "ai_model": model,
        "max_posts_scan": int(max_posts),
        "max_comments_post": int(max_comments),
        "view_threshold": int(view_threshold),
        "comment_strategy": comment_strategy,
        "min_comment_views": int(min_comment_views),
    }
    if _current != st.session_state.settings:
        st.session_state.settings = _current
        save_settings(_current)

    st.divider()
    st.markdown("**🔑 X Session**")
    session_exists = os.path.exists(USER_DATA_DIR)
    st.caption(f"Status: {'✅ Active' if session_exists else '❌ No session'}")

    if st.button("🔑 Setup X Session (Login)"):
        with st.spinner("Opening browser for login..."):
            start_login_session()
            st.success("Session saved!")

    if st.button("🗑️ Clear X Session", disabled=not session_exists):
        try:
            shutil.rmtree(USER_DATA_DIR)
            st.success("Session cleared. You can now log in with a different account.")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to clear session: {e}")

# Main Dashboard
st.title("🐦 X Automation Dashboard")

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("Controls")
    if not st.session_state.is_running:
        if st.button("🚀 Start Automation", width="stretch"):
            selected_model = st.session_state.settings["ai_model"]
            has_key = False
            if "gpt" in selected_model and st.session_state.settings["openai_api_key"]:
                has_key = True
            elif "gemini" in selected_model and st.session_state.settings["gemini_api_key"]:
                has_key = True
            elif "deepseek" in selected_model and st.session_state.settings["deepseek_api_key"]:
                has_key = True
            
            if not has_key:
                st.error(f"Please enter the API key for your selected model ({selected_model}) in Settings.")
            elif not os.path.exists("x_profile"):
                st.error("Please setup X Session first.")
            else:
                st.session_state.is_running = True
                st.session_state.status_text = "Starting..."
                
                # Start bot in thread
                st.session_state.bot = XBot(
                    api_key=st.session_state.settings["openai_api_key"],
                    gemini_api_key=st.session_state.settings["gemini_api_key"],
                    deepseek_api_key=st.session_state.settings["deepseek_api_key"],
                    deepseek_base_url=st.session_state.settings["deepseek_base_url"],
                    model=st.session_state.settings["ai_model"],
                    max_posts=st.session_state.settings["max_posts_scan"],
                    max_comments=st.session_state.settings["max_comments_post"],
                    view_threshold=st.session_state.settings["view_threshold"],
                    comment_strategy=st.session_state.settings["comment_strategy"],
                    min_comment_views=st.session_state.settings["min_comment_views"],
                )
                
                def run_bot():
                    try:
                        st.session_state.bot.run(update_status)
                    finally:
                        st.session_state.is_running = False
                        st.session_state.status_text = "Finished"
                
                ctx = get_script_run_ctx()
                thread = threading.Thread(target=run_bot)
                add_script_run_ctx(thread, ctx)
                thread.start()
    else:
        if st.button("🛑 Stop Automation", width="stretch"):
            st.session_state.is_running = False
            if "bot" in st.session_state:
                st.session_state.bot.stop_requested = True
            st.info("Stopping... (will finish current task)")

    st.metric("Status", st.session_state.status_text)
    st.progress(st.session_state.progress)

with col2:
    st.subheader("Live Logs")
    if st.session_state.logs:
        with st.container(height=340, border=True):
            for entry in reversed(st.session_state.logs):
                st.markdown(
                    f"<small style='color:#888'>{entry['Time']}</small>&nbsp;&nbsp;{entry['Message']}",
                    unsafe_allow_html=True,
                )
    else:
        st.info("No logs yet. Start the bot to see activity.")

st.divider()

# Results Gallery
st.subheader("📊 Interaction History")

tab1, tab2 = st.tabs(["📜 History", "🔍 Check Post Variants"])

with tab1:
    history = get_history()
    if history:
        history_df = pd.DataFrame(history, columns=["ID", "Timestamp", "Post URL", "Comment", "Status", "Views"])
        st.dataframe(history_df, width="stretch")
    else:
        st.info("No successful interactions yet.")

with tab2:
    st.markdown("### 🔎 Check Comment Status by URL")
    search_url = st.text_input("Enter Post URL to check variants:", placeholder="https://x.com/username/status/...")
    
    if search_url:
        from db_manager import get_variants_for_url
        variants = get_variants_for_url(search_url)
        if variants:
            st.success(f"Found {len(variants)} variants for this post.")
            
            # Format data for display
            v_data = []
            for v in variants:
                status = "✅ Used (Y)" if v["posted_at"] else "⏳ Not Used (N)"
                v_data.append({
                    "Index": v["variant_index"] + 1,
                    "Reply Content": v["reply_text"],
                    "Status": status,
                    "Generated At": v["created_at"],
                    "Posted At": v["posted_at"] or "-",
                    "Views at Posting": f"{v['post_views']:,}" if v["post_views"] else "-"
                })
            
            st.table(v_data)
        else:
            st.warning("No generated variants found for this URL in the database.")
    else:
        st.info("Enter a URL above to see the status of its 5 generated AI replies.")

# Auto-refresh UI
if st.session_state.is_running:
    time.sleep(2)
    st.rerun()
