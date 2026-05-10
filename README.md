# 🐦 XAuto: Multi-Account AI Automation Bot

XAuto is a robust, multi-account automation tool for X (formerly Twitter). It leverages Playwright for high-fidelity browser automation and state-of-the-art LLMs (Gemini, OpenAI, DeepSeek) to generate engaging, human-like interactions. Designed for scale, XAuto allows you to manage and run multiple accounts simultaneously from a single dashboard.

## ✨ Key Features

- **👥 Multi-Account Management**: Add, remove, and configure multiple X accounts independently.
- **🚀 Concurrent Execution**: Run multiple bot instances in parallel using multi-threading.
- **🤖 Advanced AI Integration**: Support for Gemini 2.0/2.5, GPT-4o, and official DeepSeek (v4, chat, reasoner) models.
- **📈 Engagement-Driven Strategies**:
  - **Reply to Post**: Direct AI response based on tweet content.
  - **Mimic Top Comments**: Blends in by matching the tone of high-performing replies.
  - **Reply if Latest Active**: Engages only if recent comments meet a view threshold.
  - **Re-Reply Strategy**: Sequences follow-up comments on active threads.
- **👯 Copy Settings**: Quickly clone configurations (models, prompts, thresholds) between accounts.
- **🛡️ Stealth & Anti-Detection**: Uses `playwright-stealth` and human-like interaction patterns.
- **📊 Real-Time Dashboard**: Monitor logs, status, and history for all accounts in one view.
- **💾 Data Isolation**: Each account maintains its own unique browser profile and database history.

## 🛠️ Installation

### 1. Clone & Setup
```bash
git clone https://github.com/yourusername/XAuto.git
cd XAuto
pip install -r requirements.txt
playwright install chromium
```

## 🚀 Getting Started

1. **Launch Dashboard**: `streamlit run main.py`
2. **Add Accounts**: In the sidebar, use **"➕ Add New"** to create account profiles.
3. **Setup Session**: Select an account → Click **"🔑 Setup X Session"** to log in manually once.
4. **Configure**: Set your AI model, strategy, and custom prompts per account.
5. **Start Bot**: Select the accounts you want to run and click **"🚀 Start Selected Accounts"**.

## ⚙️ Configuration

| Setting | Description |
|---------|-------------|
| **View Threshold** | Minimum views a post must have to be scanned. |
| **Min Content Length** | Bot automatically skips posts with < 50 characters for better quality. |
| **Premium Only** | Filter to only interact with verified (blue check) accounts. |
| **Min Comment Views** | Threshold for mimicking or detecting "active" threads. |
| **Custom Prompt** | Add specific tone/style instructions for the AI. |

## 📁 Project Structure

- `main.py`: Multi-threaded Streamlit dashboard.
- `bot_engine.py`: Automation core and AI logic.
- `db_manager.py`: Multi-tenant SQLite handling.
- `settings_manager.py`: Hierarchical config (Global API keys + Per-Account settings).
- `x_profile_*/`: Isolated browser profiles for each account.

## ⚠️ Disclaimer

This tool is for educational purposes. Use responsibly. Automated activity may lead to account restrictions if not configured with realistic delays.

---
*Built with ❤️ for the X Community.*
