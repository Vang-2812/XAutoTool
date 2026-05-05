# 🐦 XAuto: AI-Powered X Automation Bot

XAuto is a sophisticated, AI-driven automation tool for X (formerly Twitter). It uses Playwright for browser automation and leverages cutting-edge LLMs (Gemini, OpenAI, DeepSeek) to generate human-like, high-engagement replies. Designed with stealth and engagement optimization in mind, XAuto helps you scale your presence on X effortlessly.

## ✨ Features

- **🤖 Multi-Model AI Support**: Choose between Google Gemini (2.0/2.5 Flash/Pro), OpenAI (GPT-4o/mini), or DeepSeek (v4 Flash/Pro).
- **📈 Engagement-Driven Strategies**:
  - **Reply to Post**: Direct AI response based on post content.
  - **Mimic Top Comments**: Analyzes high-performing replies to blend in and maximize reach.
  - **Reply if Latest Comment Active**: Smart filtering to only engage with posts currently trending.
  - **Re-Reply Strategy**: Automatically sequences follow-up comments based on your previous interaction performance.
- **🛡️ Stealth & Anti-Detection**: Integrated `playwright-stealth` and realistic human-mimicry delays to avoid bot detection.
- **📊 Real-Time Dashboard**: Built with Streamlit for a clean, interactive UI to monitor logs, manage settings, and track interaction history.
- **💾 Local Database**: SQLite integration to prevent duplicate interactions and manage reply variants.
- **🔑 Session Management**: Persistent browser context so you only need to log in once.

## 🛠️ Installation

### 1. Clone the Repository
```bash
git clone https://github.com/yourusername/XAuto.git
cd XAuto
```

### 2. Install Dependencies
Ensure you have Python 3.9+ installed.
```bash
pip install -r requirements.txt
```

### 3. Install Playwright Browsers
```bash
playwright install chromium
```

## 🚀 Getting Started

### 1. Launch the Dashboard
```bash
streamlit run main.py
```

### 2. Configure Your API Keys
Open the sidebar in the Streamlit dashboard and enter your API keys for:
- OpenAI
- Google Gemini
- DeepSeek (and Base URL if applicable)

### 3. Setup X Session
Click **"🔑 Setup X Session (Login)"** in the dashboard. A browser window will open; log in to your X account and close the window once redirected to the home feed. Your session will be saved locally in the `x_profile` folder.

### 4. Start Automating
Adjust your scan limits and engagement thresholds, then hit **"🚀 Start Automation"**.

## ⚙️ Configuration

| Setting | Description |
|---------|-------------|
| **Max Posts to Scan** | Number of posts to analyze in the timeline. |
| **Max Comments to Post** | Daily/Session limit for successful replies. |
| **View Threshold** | Minimum views a post must have to be considered. |
| **Comment Strategy** | The logic used to generate and post replies. |
| **Min Comment Views** | Used in Mimic/Active modes to filter quality interactions. |

## 📁 Project Structure

- `main.py`: Streamlit frontend and dashboard logic.
- `bot_engine.py`: Core Playwright automation and AI integration.
- `db_manager.py`: SQLite database operations for history and variants.
- `settings_manager.py`: Persistent configuration handling.
- `x_profile/`: Local directory storing your encrypted X session data.

## ⚠️ Disclaimer

This tool is for educational purposes only. Automated interaction with X may violate their Terms of Service. Use responsibly and at your own risk.

---
*Built with ❤️ for the X Community.*
