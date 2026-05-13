import streamlit as st
import pandas as pd
import threading
import time
import shutil
from concurrent.futures import ThreadPoolExecutor
from streamlit.runtime.scriptrunner import get_script_run_ctx, add_script_run_ctx
from bot_engine import XBot, start_login_session
from settings_manager import (
    load_settings, save_settings,
    add_account, remove_account, get_account_by_id,
    get_profile_dir, ACCOUNT_SETTING_KEYS,
    get_plans_for_account, add_plan, remove_plan, get_plan_by_id,
    add_step_to_plan, remove_step_from_plan, make_step_from_account,
    STEP_SETTING_KEYS,
)
from db_manager import init_db, get_history, get_variants_for_url
import os

# Page Config
st.set_page_config(page_title="X AutoBot", page_icon="🐦", layout="wide")

# Initialize DB
init_db()

# Load Settings (multi-account format)
if "settings" not in st.session_state:
    st.session_state.settings = load_settings()

if "logs" not in st.session_state:
    st.session_state.logs = []  # Global log stream

if "account_status" not in st.session_state:
    st.session_state.account_status = {}  # {account_id: "Idle" | "Running" | "Finished" | ...}

if "account_bots" not in st.session_state:
    st.session_state.account_bots = {}  # {account_id: XBot instance}

if "account_threads" not in st.session_state:
    st.session_state.account_threads = {}  # {account_id: Thread}

if "is_running" not in st.session_state:
    st.session_state.is_running = False

if "progress" not in st.session_state:
    st.session_state.progress = 0.0


# Helper to update status from thread (thread-safe via session_state)
def make_status_callback(account_id, account_name):
    """Create a status callback scoped to a specific account."""
    def update_status(text):
        prefixed = f"[{account_name}] {text}"
        st.session_state.account_status[account_id] = text
        st.session_state.logs.append({"Time": time.strftime("%H:%M:%S"), "Message": prefixed})
        # Keep only last 100 logs
        if len(st.session_state.logs) > 100:
            st.session_state.logs.pop(0)
    return update_status


# ------------------------------------------------------------------
# Sidebar: Global Settings + Account Management
# ------------------------------------------------------------------
with st.sidebar:
    st.title("⚙️ Settings")
    settings = st.session_state.settings

    # --- Global API Keys ---
    st.markdown("**🔑 API Keys (Shared)**")
    api_key = st.text_input("OpenAI API Key", value=settings["global"].get("openai_api_key", ""), type="password")
    gemini_key = st.text_input("Gemini API Key", value=settings["global"].get("gemini_api_key", ""), type="password")
    deepseek_key = st.text_input("DeepSeek API Key", value=settings["global"].get("deepseek_api_key", ""), type="password")
    deepseek_url = st.text_input("DeepSeek Base URL", value=settings["global"].get("deepseek_base_url", ""))

    # Sync global keys
    _new_global = {
        "openai_api_key": api_key,
        "gemini_api_key": gemini_key,
        "deepseek_api_key": deepseek_key,
        "deepseek_base_url": deepseek_url,
    }
    if _new_global != settings["global"]:
        settings["global"] = _new_global
        save_settings(settings)

    st.divider()

    # --- Account Management ---
    st.markdown("**👥 Account Management**")

    account_names = [f"{acc['name']} ({acc['id']})" for acc in settings["accounts"]]
    account_ids = [acc["id"] for acc in settings["accounts"]]

    # Select which account to configure
    selected_idx = st.selectbox(
        "Select Account",
        range(len(account_names)),
        format_func=lambda i: account_names[i],
        key="config_account_select"
    )
    acc = settings["accounts"][selected_idx]

    # Add / Remove Account
    col_add, col_del = st.columns(2)
    with col_add:
        if st.button("➕ Add New", use_container_width=True):
            new_name = f"Account {len(settings['accounts']) + 1}"
            settings, new_acc = add_account(settings, name=new_name)
            st.session_state.settings = settings
            st.rerun()
    with col_del:
        if st.button("🗑️ Remove Current", use_container_width=True, disabled=len(settings["accounts"]) <= 1):
            st.session_state.show_confirm_delete = True

    if st.session_state.get("show_confirm_delete"):
        st.warning(f"Delete '{acc['name']}'?")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("✅ Yes, Delete", type="primary", use_container_width=True):
                settings = remove_account(settings, acc["id"])
                st.session_state.settings = settings
                st.session_state.show_confirm_delete = False
                st.rerun()
        with col_no:
            if st.button("❌ Cancel", use_container_width=True):
                st.session_state.show_confirm_delete = False
                st.rerun()

    st.divider()

    # --- Per-Account Settings ---
    st.markdown(f"**⚙️ Settings: {acc['name']}**")

    # --- Copy Settings Feature ---
    other_accounts = [a for a in settings["accounts"] if a["id"] != acc["id"]]
    if other_accounts:
        with st.expander("👯 Copy Settings from...", expanded=False):
            copy_src_names = [f"{a['name']} ({a['id']})" for a in other_accounts]
            selected_copy_src_idx = st.selectbox(
                "Source Account",
                range(len(copy_src_names)),
                format_func=lambda i: copy_src_names[i],
                key=f"copy_src_{acc['id']}"
            )
            if st.button("📋 Copy Now", key=f"copy_btn_{acc['id']}", use_container_width=True):
                src_acc = other_accounts[selected_copy_src_idx]
                # Copy all keys except id and name
                for k in ACCOUNT_SETTING_KEYS:
                    if k not in ["id", "name"]:
                        acc[k] = src_acc.get(k, acc[k])
                
                # IMPORTANT: Also update session_state keys so widgets refresh immediately
                aid = acc["id"]
                st.session_state[f"model_{aid}"] = acc["ai_model"]
                st.session_state[f"maxposts_{aid}"] = acc["max_posts_scan"]
                st.session_state[f"maxcomments_{aid}"] = acc["max_comments_post"]
                st.session_state[f"viewthresh_{aid}"] = acc["view_threshold"]
                st.session_state[f"strategy_{aid}"] = acc["comment_strategy"]
                st.session_state[f"minviews_{aid}"] = acc["min_comment_views"]
                st.session_state[f"prompt_{aid}"] = acc["custom_prompt"]
                st.session_state[f"premium_{aid}"] = acc["premium_only"]
                st.session_state[f"skip_sponsored_{aid}"] = acc.get("skip_sponsored", False)

                # Update settings and save
                settings["accounts"][selected_idx] = acc
                st.session_state.settings = settings
                save_settings(settings)
                st.rerun()

    acc_name = st.text_input("Account Name", value=acc["name"], key=f"name_{acc['id']}")

    models = ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-4o", "gpt-4o-mini", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro", "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"]
    current_model = acc.get("ai_model", "gpt-4o-mini")
    model_index = models.index(current_model) if current_model in models else 1
    model = st.selectbox("AI Model", models, index=model_index, key=f"model_{acc['id']}")

    max_posts = st.number_input("Max Posts to Scan", min_value=1, max_value=500, value=acc.get("max_posts_scan", 20), key=f"maxposts_{acc['id']}")
    max_comments = st.number_input("Max Comments to Post", min_value=1, max_value=100, value=acc.get("max_comments_post", 5), key=f"maxcomments_{acc['id']}")
    view_threshold = st.number_input("Post View Threshold", min_value=0, value=acc.get("view_threshold", 1000), key=f"viewthresh_{acc['id']}")
    premium_only = st.checkbox("Premium Account Only", value=acc.get("premium_only", False), key=f"premium_{acc['id']}",
                               help="Only interact with posts from accounts with a verified badge.")
    skip_sponsored = st.checkbox("Skip Sponsored Posts", value=acc.get("skip_sponsored", False), key=f"skip_sponsored_{acc['id']}",
                                 help="Automatically skip posts marked as Ads or Promoted by X.")

    st.divider()
    st.markdown("**💬 Comment Strategy**")
    strategies = ["Reply to Post", "Mimic Top Comments", "Reply if Latest Comment Active", "Re-Reply to Post"]
    current_strategy = acc.get("comment_strategy", "Reply to Post")
    strategy_index = strategies.index(current_strategy) if current_strategy in strategies else 0
    comment_strategy = st.selectbox(
        "How to generate replies",
        strategies,
        index=strategy_index,
        key=f"strategy_{acc['id']}",
        help=(
            "**Reply to Post**: AI reads the post and crafts a direct reply.\n\n"
            "**Mimic Top Comments**: AI reads top-viewed comments and generates a similar one.\n\n"
            "**Reply if Latest Comment Active**: Opens Latest tab — only replies if at least one of the 5 most recent "
            "comments meets the Min Comment Views threshold.\n\n"
            "**Re-Reply to Post**: Scans your account's Replies. If a reply has high views, "
            "it posts the next queued variant from the 5 saved comments for that post."
        )
    )

    min_comment_views = st.number_input(
        "Min Comment Views (Mimic / Active mode)",
        min_value=0,
        value=acc.get("min_comment_views", 1000),
        key=f"minviews_{acc['id']}",
        help="Mimic mode: only use comments with ≥ this many views.\nLatest-Active mode: skip post if none of the 5 most recent comments meet this threshold.",
        disabled=(comment_strategy == "Reply to Post")
    )

    st.divider()
    st.markdown("**✍️ AI Content Prompt**")
    st.caption("Customize the content/style instructions (Output format is fixed).")
    custom_prompt_val = st.text_area(
        "Instructions",
        value=acc.get("custom_prompt", ""),
        height=180,
        key=f"prompt_{acc['id']}",
        help="Add instructions like 'Tone: funny' or 'Keep it short'."
    )

    # Auto-sync per-account settings
    _updated_acc = {
        "id": acc["id"],
        "name": acc_name,
        "ai_model": model,
        "max_posts_scan": int(max_posts),
        "max_comments_post": int(max_comments),
        "view_threshold": int(view_threshold),
        "comment_strategy": comment_strategy,
        "min_comment_views": int(min_comment_views),
        "custom_prompt": custom_prompt_val,
        "premium_only": premium_only,
        "skip_sponsored": skip_sponsored,
        "plans": acc.get("plans", []),
    }
    if _updated_acc != acc:
        settings["accounts"][selected_idx] = _updated_acc
        st.session_state.settings = settings
        save_settings(settings)

    st.divider()

    # --- X Session Management (per account) ---
    st.markdown(f"**🔑 X Session: {acc_name}**")
    profile_dir = get_profile_dir(acc["id"])
    session_exists = os.path.exists(profile_dir)
    st.caption(f"Profile: `{profile_dir}` — {'✅ Active' if session_exists else '❌ No session'}")

    if st.button("🔑 Setup X Session (Login)", key=f"login_{acc['id']}"):
        with st.spinner(f"Opening browser for {acc_name} login..."):
            start_login_session(account_id=acc["id"])
            st.rerun()

    if st.button("🗑️ Clear X Session", key=f"clear_{acc['id']}", disabled=not session_exists):
        try:
            shutil.rmtree(profile_dir)
            st.success(f"Session cleared for {acc_name}.")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to clear session: {e}")

# ------------------------------------------------------------------
# Main Dashboard
# ------------------------------------------------------------------
st.title("🐦 X Automation Dashboard")

# ------------------------------------------------------------------
# Account selection for running
# ------------------------------------------------------------------
st.subheader("🚀 Run Accounts")

# Build list of accounts with session status
run_options = []
for acc in settings["accounts"]:
    profile_dir = get_profile_dir(acc["id"])
    has_session = os.path.exists(profile_dir)
    status = st.session_state.account_status.get(acc["id"], "Idle")
    run_options.append({
        "id": acc["id"],
        "name": acc["name"],
        "has_session": has_session,
        "status": status,
    })

# Display account cards
cols = st.columns(min(len(run_options), 4))
selected_accounts = []

for i, opt in enumerate(run_options):
    col = cols[i % len(cols)]
    with col:
        is_account_running = opt["id"] in st.session_state.account_threads and \
                             st.session_state.account_threads[opt["id"]].is_alive()
        
        checked = st.checkbox(
            f"{'✅' if opt['has_session'] else '❌'} {opt['name']}",
            value=False,
            key=f"run_{opt['id']}",
            disabled=not opt["has_session"] or is_account_running,
            help=f"Status: {opt['status']}" + ("" if opt["has_session"] else " — No session! Login first.")
        )
        if checked:
            selected_accounts.append(opt["id"])

        # Show per-account status
        status_text = opt["status"]
        if is_account_running:
            st.caption(f"🔄 Running: {status_text}")
        else:
            st.caption(f"Status: {status_text}")

st.divider()

# ------------------------------------------------------------------
# Start / Stop controls
# ------------------------------------------------------------------
col_start, col_stop = st.columns(2)

any_running = any(
    t.is_alive() for t in st.session_state.account_threads.values()
) if st.session_state.account_threads else False

with col_start:
    start_disabled = (not selected_accounts) or any_running
    if st.button("🚀 Start Selected Accounts", disabled=start_disabled, use_container_width=True):
        # Validate API keys for selected accounts
        can_start = True
        for acc_id in selected_accounts:
            acc = get_account_by_id(settings, acc_id)
            if not acc:
                continue
            selected_model = acc["ai_model"]
            g = settings["global"]
            has_key = False
            if "gpt" in selected_model and g.get("openai_api_key"):
                has_key = True
            elif "gemini" in selected_model and g.get("gemini_api_key"):
                has_key = True
            elif "deepseek" in selected_model and g.get("deepseek_api_key"):
                has_key = True
            if not has_key:
                st.error(f"Missing API key for {acc['name']} (model: {selected_model})")
                can_start = False

        if can_start:
            st.session_state.is_running = True
            ctx = get_script_run_ctx()

            for acc_id in selected_accounts:
                acc = get_account_by_id(settings, acc_id)
                g = settings["global"]

                bot = XBot(
                    api_key=g.get("openai_api_key", ""),
                    gemini_api_key=g.get("gemini_api_key", ""),
                    deepseek_api_key=g.get("deepseek_api_key", ""),
                    deepseek_base_url=g.get("deepseek_base_url", ""),
                    model=acc["ai_model"],
                    max_posts=acc["max_posts_scan"],
                    max_comments=acc["max_comments_post"],
                    view_threshold=acc["view_threshold"],
                    comment_strategy=acc["comment_strategy"],
                    min_comment_views=acc["min_comment_views"],
                    custom_prompt=acc["custom_prompt"],
                    premium_only=acc["premium_only"],
                    skip_sponsored=acc.get("skip_sponsored", False),
                    account_id=acc["id"],
                    account_name=acc["name"],
                )
                st.session_state.account_bots[acc_id] = bot
                st.session_state.account_status[acc_id] = "Starting..."

                callback = make_status_callback(acc_id, acc["name"])

                def run_bot(b=bot, cb=callback, aid=acc_id):
                    try:
                        b.run(cb)
                    except Exception as e:
                        cb(f"❌ Fatal error: {e}")
                    finally:
                        st.session_state.account_status[aid] = "Finished"

                thread = threading.Thread(target=run_bot, daemon=True)
                add_script_run_ctx(thread, ctx)
                thread.start()
                st.session_state.account_threads[acc_id] = thread

            st.rerun()

with col_stop:
    if st.button("🛑 Stop All Accounts", disabled=not any_running, use_container_width=True):
        for acc_id, bot in st.session_state.account_bots.items():
            bot.stop_requested = True
        st.info("Stopping all accounts... (will finish current tasks)")

# ------------------------------------------------------------------
# Live Logs
# ------------------------------------------------------------------
st.divider()
col_log, col_status = st.columns([2, 1])

with col_log:
    st.subheader("📋 Live Logs")
    if st.session_state.logs:
        with st.container(height=400, border=True):
            for entry in reversed(st.session_state.logs):
                st.markdown(
                    f"<small style='color:#888'>{entry['Time']}</small>&nbsp;&nbsp;{entry['Message']}",
                    unsafe_allow_html=True,
                )
    else:
        st.info("No logs yet. Start the bot to see activity.")

with col_status:
    st.subheader("📊 Account Status")
    for acc in settings["accounts"]:
        aid = acc["id"]
        status = st.session_state.account_status.get(aid, "Idle")
        is_alive = aid in st.session_state.account_threads and st.session_state.account_threads[aid].is_alive()
        icon = "🟢" if is_alive else ("✅" if status == "Finished" else "⚪")
        st.markdown(f"{icon} **{acc['name']}**: {status}")

# ------------------------------------------------------------------
# Results Gallery
# ------------------------------------------------------------------
st.divider()
st.subheader("📊 Interaction History")

tab1, tab2, tab3 = st.tabs(["📜 History", "📋 Plans", "🔍 Check Post Variants"])

with tab1:
    # Filter by account
    filter_options = ["All Accounts"] + [f"{a['name']} ({a['id']})" for a in settings["accounts"]]
    filter_ids = [None] + [a["id"] for a in settings["accounts"]]
    filter_idx = st.selectbox("Filter by Account", range(len(filter_options)), format_func=lambda i: filter_options[i])
    selected_filter_id = filter_ids[filter_idx]

    history = get_history(account_id=selected_filter_id)
    if history:
        history_df = pd.DataFrame(history, columns=["ID", "Timestamp", "Post URL", "Comment", "Status", "Views", "Account ID"])
        
        # Map Account ID to Account Name for display
        account_map = {a["id"]: a["name"] for a in settings["accounts"]}
        history_df["Account"] = history_df["Account ID"].map(lambda x: account_map.get(x, x))
        
        cols = ["ID", "Timestamp", "Account", "Post URL", "Comment", "Status", "Views"]
        history_df = history_df[cols]
        
        st.dataframe(history_df, use_container_width=True)
    else:
        st.info("No interactions yet.")

# ------------------------------------------------------------------
# Tab 2: Plans
# ------------------------------------------------------------------
with tab2:
    st.markdown("### 📋 Scheduling & Plan Setup")
    st.caption(
        "Create plans with multiple strategy steps. "
        "Each step can have its own settings and a delay or scheduled time trigger."
    )

    # --- Select account for plan management ---
    plan_acc_names = [f"{a['name']} ({a['id']})" for a in settings["accounts"]]
    plan_acc_idx = st.selectbox(
        "Account",
        range(len(plan_acc_names)),
        format_func=lambda i: plan_acc_names[i],
        key="plan_account_select"
    )
    plan_acc = settings["accounts"][plan_acc_idx]
    plan_acc_id = plan_acc["id"]

    plans = get_plans_for_account(settings, plan_acc_id)

    # --- Add Plan ---
    col_plan_add, col_plan_spacer = st.columns([1, 3])
    with col_plan_add:
        if st.button("➕ New Plan", key="add_plan_btn", use_container_width=True):
            new_plan = add_plan(settings, plan_acc_id, name=f"Plan {len(plans) + 1}")
            st.session_state.settings = settings
            st.rerun()

    if not plans:
        st.info("No plans yet for this account. Click **➕ New Plan** to create one.")
    else:
        for p_idx, plan in enumerate(plans):
            plan_id = plan["id"]
            is_plan_running = (
                f"plan_{plan_acc_id}_{plan_id}" in st.session_state.account_threads
                and st.session_state.account_threads[f"plan_{plan_acc_id}_{plan_id}"].is_alive()
            )

            with st.expander(
                f"{'🟢' if is_plan_running else '📋'} {plan.get('name', 'Unnamed')} — "
                f"{len(plan.get('steps', []))} step(s)",
                expanded=(len(plans) == 1)
            ):
                # --- Plan header: name + controls ---
                hcol1, hcol2, hcol3 = st.columns([3, 1, 1])
                with hcol1:
                    new_plan_name = st.text_input(
                        "Plan Name", value=plan.get("name", ""),
                        key=f"pname_{plan_id}", label_visibility="collapsed"
                    )
                    if new_plan_name != plan.get("name"):
                        plan["name"] = new_plan_name
                        save_settings(settings)
                with hcol2:
                    if st.button(
                        "▶️ Run" if not is_plan_running else "🔄 Running...",
                        key=f"run_plan_{plan_id}",
                        disabled=is_plan_running or not os.path.exists(get_profile_dir(plan_acc_id)),
                        use_container_width=True
                    ):
                        # Launch plan in background thread
                        g = settings["global"]
                        bot = XBot(
                            api_key=g.get("openai_api_key", ""),
                            gemini_api_key=g.get("gemini_api_key", ""),
                            deepseek_api_key=g.get("deepseek_api_key", ""),
                            deepseek_base_url=g.get("deepseek_base_url", ""),
                            model=plan_acc.get("ai_model", "gpt-4o-mini"),
                            max_posts=plan_acc.get("max_posts_scan", 20),
                            max_comments=plan_acc.get("max_comments_post", 5),
                            view_threshold=plan_acc.get("view_threshold", 1000),
                            comment_strategy=plan_acc.get("comment_strategy", "Reply to Post"),
                            min_comment_views=plan_acc.get("min_comment_views", 1000),
                            custom_prompt=plan_acc.get("custom_prompt", ""),
                            premium_only=plan_acc.get("premium_only", False),
                            skip_sponsored=plan_acc.get("skip_sponsored", False),
                            account_id=plan_acc_id,
                            account_name=plan_acc["name"],
                        )
                        thread_key = f"plan_{plan_acc_id}_{plan_id}"
                        st.session_state.account_bots[thread_key] = bot
                        st.session_state.account_status[thread_key] = "Starting plan..."

                        cb = make_status_callback(thread_key, f"{plan_acc['name']}|{plan['name']}")

                        def _run_plan(b=bot, p=plan, callback=cb, tk=thread_key):
                            try:
                                b.run_plan(p, callback)
                            except Exception as e:
                                callback(f"❌ Fatal plan error: {e}")
                            finally:
                                st.session_state.account_status[tk] = "Plan Finished"

                        ctx = get_script_run_ctx()
                        thread = threading.Thread(target=_run_plan, daemon=True)
                        add_script_run_ctx(thread, ctx)
                        thread.start()
                        st.session_state.account_threads[thread_key] = thread
                        st.rerun()

                with hcol3:
                    if is_plan_running:
                        thread_key = f"plan_{plan_acc_id}_{plan_id}"
                        if st.button("🛑 Stop", key=f"stop_plan_{plan_id}", use_container_width=True):
                            bot = st.session_state.account_bots.get(thread_key)
                            if bot:
                                bot.stop_requested = True
                            st.info("Stopping plan...")
                    else:
                        if st.button("🗑️ Delete", key=f"del_plan_{plan_id}", use_container_width=True):
                            remove_plan(settings, plan_acc_id, plan_id)
                            st.session_state.settings = settings
                            st.rerun()

                # Show plan running status
                if is_plan_running:
                    thread_key = f"plan_{plan_acc_id}_{plan_id}"
                    status = st.session_state.account_status.get(thread_key, "")
                    st.info(f"🔄 {status}")

                st.markdown("---")

                # --- Steps ---
                steps = plan.get("steps", [])
                strategies_list = ["Reply to Post", "Mimic Top Comments", "Reply if Latest Comment Active", "Re-Reply to Post"]
                models_list = ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-4o", "gpt-4o-mini", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro", "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"]

                plan_changed = False

                for s_idx, step in enumerate(steps):
                    step_key = f"{plan_id}_s{s_idx}"
                    st.markdown(f"**Step {s_idx + 1}**")

                    # Row 1: Strategy + Trigger
                    c1, c2, c3, c4 = st.columns([2, 1, 1, 0.5])
                    with c1:
                        cur_strat = step.get("strategy", "Reply to Post")
                        s_i = strategies_list.index(cur_strat) if cur_strat in strategies_list else 0
                        new_strat = st.selectbox(
                            "Strategy", strategies_list, index=s_i,
                            key=f"strat_{step_key}", label_visibility="collapsed"
                        )
                        if new_strat != step.get("strategy"):
                            step["strategy"] = new_strat
                            plan_changed = True

                    with c2:
                        trigger_opts = ["delay", "time"]
                        cur_trigger = step.get("trigger", "delay")
                        t_i = trigger_opts.index(cur_trigger) if cur_trigger in trigger_opts else 0
                        new_trigger = st.selectbox(
                            "Trigger", trigger_opts, index=t_i,
                            key=f"trigger_{step_key}",
                            format_func=lambda x: "⏳ Delay" if x == "delay" else "⏰ Time",
                            label_visibility="collapsed",
                            disabled=(s_idx == 0)  # First step always runs immediately
                        )
                        if s_idx > 0 and new_trigger != step.get("trigger"):
                            step["trigger"] = new_trigger
                            plan_changed = True

                    with c3:
                        if s_idx == 0:
                            st.caption("Runs immediately")
                        elif new_trigger == "delay":
                            new_delay = st.number_input(
                                "Delay (min)", min_value=0, max_value=1440,
                                value=step.get("delay_minutes", 0),
                                key=f"delay_{step_key}", label_visibility="collapsed"
                            )
                            if int(new_delay) != step.get("delay_minutes"):
                                step["delay_minutes"] = int(new_delay)
                                plan_changed = True
                        else:  # time
                            from datetime import time as dt_time
                            cur_time_str = step.get("scheduled_time", "00:00")
                            try:
                                h, m = map(int, cur_time_str.split(":"))
                                cur_time_val = dt_time(h, m)
                            except Exception:
                                cur_time_val = dt_time(0, 0)
                            new_time = st.time_input(
                                "Time", value=cur_time_val,
                                key=f"time_{step_key}", label_visibility="collapsed"
                            )
                            new_time_str = new_time.strftime("%H:%M")
                            if new_time_str != step.get("scheduled_time"):
                                step["scheduled_time"] = new_time_str
                                plan_changed = True

                    with c4:
                        if st.button("✕", key=f"del_step_{step_key}", disabled=(len(steps) <= 1)):
                            remove_step_from_plan(settings, plan_acc_id, plan_id, s_idx)
                            st.session_state.settings = settings
                            st.rerun()

                    # Row 2: Collapsed per-step settings
                    with st.expander(f"⚙️ Step {s_idx + 1} Settings", expanded=False):
                        sc1, sc2 = st.columns(2)
                        with sc1:
                            cur_model = step.get("ai_model", "gpt-4o-mini")
                            m_i = models_list.index(cur_model) if cur_model in models_list else 0
                            s_model = st.selectbox("AI Model", models_list, index=m_i, key=f"smodel_{step_key}")
                            if s_model != step.get("ai_model"):
                                step["ai_model"] = s_model
                                plan_changed = True

                            s_max_posts = st.number_input("Max Posts", min_value=1, max_value=500, value=step.get("max_posts_scan", 20), key=f"smaxp_{step_key}")
                            if int(s_max_posts) != step.get("max_posts_scan"):
                                step["max_posts_scan"] = int(s_max_posts)
                                plan_changed = True

                            s_max_comments = st.number_input("Max Comments", min_value=1, max_value=100, value=step.get("max_comments_post", 5), key=f"smaxc_{step_key}")
                            if int(s_max_comments) != step.get("max_comments_post"):
                                step["max_comments_post"] = int(s_max_comments)
                                plan_changed = True

                        with sc2:
                            s_view = st.number_input("View Threshold", min_value=0, value=step.get("view_threshold", 1000), key=f"sview_{step_key}")
                            if int(s_view) != step.get("view_threshold"):
                                step["view_threshold"] = int(s_view)
                                plan_changed = True

                            s_minv = st.number_input("Min Comment Views", min_value=0, value=step.get("min_comment_views", 1000), key=f"sminv_{step_key}")
                            if int(s_minv) != step.get("min_comment_views"):
                                step["min_comment_views"] = int(s_minv)
                                plan_changed = True

                            s_prem = st.checkbox("Premium Only", value=step.get("premium_only", False), key=f"sprem_{step_key}")
                            if s_prem != step.get("premium_only"):
                                step["premium_only"] = s_prem
                                plan_changed = True

                            s_skip = st.checkbox("Skip Sponsored", value=step.get("skip_sponsored", False), key=f"sskip_{step_key}")
                            if s_skip != step.get("skip_sponsored"):
                                step["skip_sponsored"] = s_skip
                                plan_changed = True

                        s_prompt = st.text_area("Custom Prompt", value=step.get("custom_prompt", ""), height=120, key=f"sprompt_{step_key}")
                        if s_prompt != step.get("custom_prompt"):
                            step["custom_prompt"] = s_prompt
                            plan_changed = True

                    if s_idx < len(steps) - 1:
                        st.markdown("<div style='text-align:center;color:#888;font-size:1.2em'>⬇️</div>", unsafe_allow_html=True)

                # --- Add Step button ---
                if st.button("➕ Add Step", key=f"add_step_{plan_id}", use_container_width=True):
                    # Pre-fill new step from account's current settings
                    new_step = make_step_from_account(plan_acc)
                    add_step_to_plan(settings, plan_acc_id, plan_id, overrides=new_step)
                    st.session_state.settings = settings
                    st.rerun()

                # Save any inline changes
                if plan_changed:
                    save_settings(settings)

# ------------------------------------------------------------------
# Tab 3: Check Post Variants
# ------------------------------------------------------------------
with tab3:
    st.markdown("### 🔎 Check Comment Status by URL")
    search_url = st.text_input("Enter Post URL to check variants:", placeholder="https://x.com/username/status/...")

    if search_url:
        variants = get_variants_for_url(search_url)
        if variants:
            st.success(f"Found {len(variants)} variants for this post.")

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

# Auto-refresh UI while any bot is running
any_still_running = any(
    t.is_alive() for t in st.session_state.account_threads.values()
) if st.session_state.account_threads else False

if any_still_running:
    time.sleep(2)
    st.rerun()
