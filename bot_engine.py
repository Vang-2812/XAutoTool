import time
import random
import logging
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
import openai
import google.genai as google_genai
from db_manager import (
    log_interaction, get_posted_urls,
    save_reply_variants, mark_variant_posted,
    get_next_variant, get_posts_with_pending_variants,
    get_post_url_by_variant_text
)
from settings_manager import get_profile_dir
import os
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("XBot")

STATE_FILE = "state.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"


def gaussian_sleep(mean: float, sigma: float, min_val: float = 1.0) -> None:
    """
    Sleep for a duration drawn from a normal (Gaussian) distribution.
    Much more human-like than uniform random.randint.
    
    Args:
        mean: Average seconds to wait.
        sigma: Standard deviation (spread). Larger = more varied.
        min_val: Floor — never sleep less than this.
    """
    duration = max(min_val, random.gauss(mean, sigma))
    time.sleep(duration)


class ModalDetectedException(Exception):
    """Raised when an X modal (like 'Views' popup) blocks the UI."""
    pass


class XBot:
    def __init__(self, api_key, model, max_posts, max_comments, view_threshold,
                 gemini_api_key=None, deepseek_api_key=None, deepseek_base_url=None,
                 comment_strategy="Reply to Post", min_comment_views=1000, custom_prompt="",
                 premium_only=False, account_id="default", account_name="Account"):
        self.api_key = api_key
        self.gemini_api_key = gemini_api_key
        self.deepseek_api_key = deepseek_api_key
        self.deepseek_base_url = deepseek_base_url
        self.model = model
        self.max_posts = max_posts
        self.max_comments = max_comments
        self.view_threshold = view_threshold
        self.comment_strategy = comment_strategy
        self.min_comment_views = min_comment_views
        self.custom_prompt = custom_prompt
        self.premium_only = premium_only
        self.account_id = account_id
        self.account_name = account_name
        self.user_data_dir = get_profile_dir(account_id)

        if "gpt" in model:
            self.client = openai.OpenAI(api_key=api_key)
        elif "gemini" in model:
            self.gemini_client = google_genai.Client(api_key=gemini_api_key)
        elif "deepseek" in model:
            self.deepseek_client = openai.OpenAI(
                api_key=deepseek_api_key,
                base_url=deepseek_base_url
            )

        self.stop_requested = False
        # Track processed post URLs to avoid re-evaluating
        self.processed_urls = set()
        # Load already-replied URLs from DB to avoid duplicates (scoped to this account)
        self.replied_urls = set(get_posted_urls(account_id=self.account_id))
 
    def _do_human_actions(self, page, status_callback=None) -> None:
        """
        Perform random passive human-like actions to break behavioral patterns.
        Each action has a probability weight — not all run every call.
        """
        try:
            actions = random.choices(
                ["scroll_down", "scroll_up", "hover", "pause"],
                weights=[40, 15, 25, 20],
                k=random.randint(1, 3)
            )
            for action in actions:
                if self.stop_requested:
                    break
                if action == "scroll_down":
                    amount = random.randint(200, 700)
                    page.evaluate(f"window.scrollBy(0, {amount})")
                    gaussian_sleep(1.2, 0.4, 0.5)
                elif action == "scroll_up":
                    amount = random.randint(100, 400)
                    page.evaluate(f"window.scrollBy(0, -{amount})")
                    gaussian_sleep(0.9, 0.3, 0.4)
                elif action == "hover":
                    # Hover over a random article without clicking
                    articles = page.query_selector_all('article')
                    if articles:
                        target = random.choice(articles[:5])
                        try:
                            target.hover()
                            gaussian_sleep(1.0, 0.5, 0.3)
                        except Exception:
                            pass
                elif action == "pause":
                    # Just pause — simulates reading
                    gaussian_sleep(2.5, 1.0, 1.0)
        except Exception as e:
            logger.debug(f"Human action error (non-critical): {e}")

    def _try_like_post(self, page, article_el) -> bool:
        """
        Attempt to like a post by clicking the Like button inside an article element.
        Returns True if the like action was performed, False otherwise.
        Only clicks if the post is NOT already liked (to avoid unlike).
        """
        try:
            like_btn = article_el.query_selector('[data-testid="like"]')
            if like_btn and like_btn.is_visible():
                like_btn.click()
                gaussian_sleep(0.8, 0.3, 0.4)
                logger.info("Liked a post (random human action).")
                return True
        except Exception as e:
            logger.debug(f"Like action failed (non-critical): {e}")
        return False

    def _handle_modals(self, page, skip_on_detect=False):
        """Check for and dismiss common X modals (like the 'Views' info popup)."""
        try:
            # Target only buttons inside a modal/dialog container to avoid sidebar false positives
            modal_container = page.query_selector('div[role="dialog"]') or \
                              page.query_selector('div[aria-modal="true"]')
            
            if not modal_container:
                return False

            dismiss_btn = modal_container.query_selector('div[role="button"]:has-text("Dismiss")') or \
                          modal_container.query_selector('button:has-text("Dismiss")') or \
                          modal_container.query_selector('div[aria-label="Close"]') or \
                          modal_container.query_selector('button[aria-label="Close"]') or \
                          modal_container.query_selector('div[role="button"]:has-text("Cancel")') or \
                          modal_container.query_selector('button:has-text("Cancel")')
            
            if dismiss_btn and dismiss_btn.is_visible():
                modal_text = modal_container.inner_text()
                if "Discard post?" in modal_text:
                    logger.info("Discard modal detected, clicking Cancel.")
                    cancel_btn = modal_container.query_selector('div[role="button"]:has-text("Cancel")') or \
                                 modal_container.query_selector('button:has-text("Cancel")')
                    if cancel_btn:
                        cancel_btn.click()
                        time.sleep(1)
                        return True
                
                logger.info("Modal detected, attempting to dismiss...")

                try:
                    dismiss_btn.click()
                    time.sleep(1)
                except:
                    pass
                
                if skip_on_detect:
                    logger.info("skip_on_detect is True, raising ModalDetectedException.")
                    raise ModalDetectedException("UI blocked by modal")
                return True
        except ModalDetectedException:
            raise
        except Exception:
            pass
        return False



    # ------------------------------------------------------------------
    # Internal prompt builders
    # ------------------------------------------------------------------

    _FIXED_PROMPT_PART = """
OUTPUT FORMAT — follow EXACTLY:
- Output exactly 5 replies, each on its own line.
- Prefix each line with its number and a pipe: "1| ", "2| ", "3| ", ...
- No markdown, no preamble, no explanation — ONLY the 5 numbered lines."""

    def _get_full_prompt_suffix(self):
        """Combine the fixed format prompt with the user's custom instructions."""
        return f"\n\n{self.custom_prompt}\n\n{self._FIXED_PROMPT_PART}"

    def _parse_variants(self, raw: str) -> list[str]:
        """Parse 5 pipe-prefixed reply lines from AI output into a clean list."""
        variants = []
        for line in raw.splitlines():
            line = line.strip()
            # Match "1| ...", "2| ...", etc.
            m = re.match(r'^\d+\|\s*(.+)$', line)
            if m:
                text = m.group(1).strip().replace('"', '')
                # Strip markdown bold/italic
                text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
                text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
                text = " ".join(text.split())
                if len(text) > 310:
                    text = text[:310].rsplit(' ', 1)[0].rstrip('.,;:')
                if text:
                    variants.append(text)
        return variants[:5]

    # ------------------------------------------------------------------
    # Public generation methods — return first variant, save rest to DB
    # ------------------------------------------------------------------

    def generate_reply(self, post_url: str, post_text: str) -> str | None:
        """
        Generate 5 reply variants for a plain post.
        Saves all 5 to the DB keyed by post_url.
        Returns the first variant (to be posted now), or None on failure.
        """
        try:
            prompt = (
                f'You are an engaging social media user on X (Twitter).\n'
                f'Post: "{post_text[:800]}"\n'
                f'Generate 5 replies to this post.'
                + self._get_full_prompt_suffix()
            )
            raw = self._call_ai(prompt)
            if not raw:
                return None
            variants = self._parse_variants(raw)
            if not variants:
                logger.warning("generate_reply: no variants parsed from AI output.")
                return None
            save_reply_variants(post_url, variants, account_id=self.account_id)
            logger.info(f"Saved {len(variants)} reply variants for {post_url}")
            return variants[0]
        except Exception as e:
            logger.error(f"AI Generation failed: {e}")
            return None

    def generate_mimic_reply(self, post_url: str, post_text: str, top_comments: list) -> str | None:
        """
        Generate 5 reply variants that mimic the style of top comments.
        Saves all 5 to the DB keyed by post_url.
        Returns the first variant (to be posted now), or None on failure.
        """
        try:
            comments_block = "\n".join(
                [f"- {c['text'][:200]} ({c['views']:,} views)" for c in top_comments]
            )
            prompt = (
                f'You are a social media user replying to a post on X (Twitter).\n\n'
                f'Post: "{post_text[:500]}"\n\n'
                f'Top comments (for style/tone reference ONLY):\n{comments_block}\n\n'
                f'Generate 5 replies that match the tone and style of those top comments.'
                + self._get_full_prompt_suffix()
            )
            raw = self._call_ai(prompt)
            if not raw:
                return None
            variants = self._parse_variants(raw)
            if not variants:
                logger.warning("generate_mimic_reply: no variants parsed from AI output.")
                return None
            save_reply_variants(post_url, variants, account_id=self.account_id)
            logger.info(f"Saved {len(variants)} mimic-reply variants for {post_url}")
            return variants[0]
        except Exception as e:
            logger.error(f"Mimic AI generation failed: {e}")
            return None

    def _call_ai(self, prompt):
        """Internal helper: sends a prompt to the configured AI and returns the text."""
        if "gpt" in self.model:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100
            )
            return response.choices[0].message.content.strip().replace('"', '')
        elif "gemini" in self.model:
            response = self.gemini_client.models.generate_content(
                model=self.model,
                contents=prompt
            )
            return response.text.strip().replace('"', '')
        elif "deepseek" in self.model:
            response = self.deepseek_client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100
            )
            return response.choices[0].message.content.strip().replace('"', '')
        return None


    def _extract_post_url(self, post):
        """Extract the canonical URL from a post article element."""
        # Look for the timestamp link first (most reliable)
        link_element = post.query_selector("a:has(time)")
        if not link_element:
            # Fallback: look for /status/ links
            links = post.query_selector_all("a[href*='/status/']")
            for link in links:
                href = link.get_attribute("href")
                if href and "/status/" in href:
                    link_element = link
                    break

        if not link_element:
            return None

        href = link_element.get_attribute("href")
        if not href:
            return None

        full_url = "https://x.com" + href if href.startswith("/") else href
        
        # Ensure we return the canonical URL (strip /analytics, /photo/1, etc.)
        # Match up to /status/1234567890
        match = re.search(r"(https://x\.com/[^/]+/status/\d+)", full_url)
        if match:
            return match.group(1)
            
        return full_url

    def _extract_view_count(self, page, post, post_text):
        """Extract view count from a post using multiple strategies."""
        # Only skip if we are NOT on the main page (i.e. we are in a post's new tab)
        is_new_tab = len(page.context.pages) > 1 and page != page.context.pages[0]
        self._handle_modals(page, skip_on_detect=is_new_tab)
        view_count = 0

        # Strategy 1: aria-label from engagement group
        engagement_group = post.query_selector('div[role="group"][aria-label*="views"]')
        if engagement_group:
            label = engagement_group.get_attribute("aria-label")
            if label:
                match = re.search(r"([\d,]+)\s+views", label)
                if match:
                    view_count_str = match.group(1).replace(",", "")
                    try:
                        view_count = int(view_count_str)
                    except ValueError:
                        view_count = 0

        # Strategy 2: aria-label with "view" (singular) 
        if view_count == 0:
            engagement_group = post.query_selector('div[role="group"][aria-label*="view"]')
            if engagement_group:
                label = engagement_group.get_attribute("aria-label")
                if label:
                    match = re.search(r"([\d,]+)\s+view", label)
                    if match:
                        view_count_str = match.group(1).replace(",", "")
                        try:
                            view_count = int(view_count_str)
                        except ValueError:
                            view_count = 0

        # Strategy 3: Parse from analytics link
        if view_count == 0:
            analytics_link = post.query_selector('a[href*="/analytics"]')
            if analytics_link:
                aria = analytics_link.get_attribute("aria-label")
                if aria:
                    match = re.search(r"([\d,.]+[KMkm]?)\s*view", aria)
                    if match:
                        view_count = self._parse_count_string(match.group(1))

        # Strategy 4: Fallback text parsing
        if view_count == 0 and post_text and "Views" in post_text:
            parts = post_text.split("Views")
            if len(parts) > 0:
                try:
                    view_str = parts[0].strip().split("\n")[-1].strip()
                    view_count = self._parse_count_string(view_str)
                except (ValueError, IndexError):
                    pass

        return view_count
    
    def _is_premium_account(self, post):
        """Check if the post author has a verified (premium) badge."""
        try:
            # X uses data-testid="icon-verified" for blue/gold/grey ticks
            verified_icon = post.query_selector('[data-testid="icon-verified"]')
            if verified_icon:
                return True
            
            # Fallback check for SVG paths or aria-labels that might indicate verification
            # The badge is usually inside the user name container
            user_name_container = post.query_selector('[data-testid="User-Name"]')
            if user_name_container:
                # Look for SVGs that might be the badge if data-testid is missing/changed
                svgs = user_name_container.query_selector_all('svg')
                for svg in svgs:
                    label = svg.get_attribute("aria-label")
                    if label and "Verified" in label:
                        return True
            
            return False
        except Exception:
            return False

    def _parse_count_string(self, count_str):
        """Parse a count string like '1.2K', '3M', '12,345' into an integer."""
        count_str = count_str.strip().replace(",", "")
        try:
            if count_str.upper().endswith('K'):
                return int(float(count_str[:-1]) * 1000)
            elif count_str.upper().endswith('M'):
                return int(float(count_str[:-1]) * 1000000)
            else:
                return int(count_str)
        except (ValueError, IndexError):
            return 0

    def _extract_comment_view_count(self, page, article):
        """Extract the view/impression count from a reply article element."""
        # Only skip if we are NOT on the main page
        is_new_tab = len(page.context.pages) > 1 and page != page.context.pages[0]
        self._handle_modals(page, skip_on_detect=is_new_tab)
        view_count = 0

        # Strategy 1: engagement group aria-label with 'views'
        engagement_group = article.query_selector('div[role="group"][aria-label*="views"]')
        if not engagement_group:
            engagement_group = article.query_selector('div[role="group"][aria-label*="view"]')
        if engagement_group:
            label = engagement_group.get_attribute("aria-label") or ""
            match = re.search(r"([\d,]+)\s+view", label, re.IGNORECASE)
            if match:
                view_count = self._parse_count_string(match.group(1))

        # Strategy 2: analytics link aria-label
        if view_count == 0:
            analytics_link = article.query_selector('a[href*="/analytics"]')
            if analytics_link:
                aria = article.query_selector('a[href*="/analytics"]').get_attribute("aria-label") or ""
                match = re.search(r"([\d,.]+[KMkm]?)\s*view", aria, re.IGNORECASE)
                if match:
                    view_count = self._parse_count_string(match.group(1))

        return view_count

    def _extract_top_comments(self, page):
        """
        Scrape comments from the currently loaded post page.
        Returns a list of dicts: {text, views} sorted by views descending.
        Only includes comments whose view count meets self.min_comment_views.
        """
        comments = []
        try:
            # Wait for at least one reply article to appear
            page.wait_for_selector('article', timeout=10000)
            time.sleep(2)

            articles = page.query_selector_all('article')
            # The first article is the original post — skip it. 
            # We only check the first 5 reply articles as per the "Active" check rules.
            reply_articles = articles[1:6]

            for article in reply_articles:
                try:
                    # Extract text content
                    text_el = article.query_selector('[data-testid="tweetText"]')
                    if not text_el:
                        continue
                    comment_text = text_el.inner_text().strip()
                    if not comment_text or len(comment_text) < 5:
                        continue

                    # Extract view count (impressions)
                    views = self._extract_comment_view_count(page, article)

                    if views >= self.min_comment_views:
                        comments.append({"text": comment_text, "views": views})

                except Exception as e:
                    logger.debug(f"Error extracting comment: {e}")
                    continue

        except Exception as e:
            logger.warning(f"Could not extract comments: {e}")

        # Sort by views descending, return top 5
        comments.sort(key=lambda c: c["views"], reverse=True)
        return comments[:5]

    def _enter_post_and_reply(self, page, post_url, post_text, view_count, status_callback):
        """Navigate into a post's comment section and post an AI-generated reply (reply-to-post mode)."""
        # Navigate to the post's comment section FIRST
        status_callback(f"📄 Opening post: {post_url}")
        try:
            page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector('article', timeout=15000)
            time.sleep(random.uniform(2, 4))
        except Exception as e:
            status_callback(f"❌ Failed to load post page: {e}")
            log_interaction(post_url, "", "Navigation Failed", int(view_count), account_id=self.account_id)
            return False

        # Check for modals immediately after loading
        self._handle_modals(page, skip_on_detect=True)

        # Generate 5 variants AFTER successful navigation
        status_callback(f"💬 Generating 5 AI reply variants for post with {view_count:,} views...")
        reply_text, variant_id = self._generate_and_get_variant(post_url, "reply", post_text)
        if not reply_text:
            status_callback("❌ AI failed to generate a reply. Skipping this post.")
            log_interaction(post_url, "", "AI Generation Failed", int(view_count), account_id=self.account_id)
            return False

        status_callback(f"🤖 AI Reply (variant 1/5): \"{reply_text[:80]}...\"")
        logger.info(f"Generated reply: {reply_text}")

        return self._post_reply_on_page(page, post_url, reply_text, view_count, status_callback, variant_id)

    def _mimic_top_comment_and_reply(self, page, post_url, post_text, view_count, status_callback):
        """Navigate into a post, scrape top comments, mimic their style, and post a reply."""
        # Navigate first so we can scrape comments
        status_callback(f"📄 Opening post to scan comments: {post_url}")
        try:
            page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector('article', timeout=15000)
            time.sleep(random.uniform(2, 4))
        except Exception as e:
            status_callback(f"❌ Failed to load post page: {e}")
            log_interaction(post_url, "", "Navigation Failed", int(view_count), account_id=self.account_id)
            return False

        # Check for modals immediately after loading
        self._handle_modals(page, skip_on_detect=True)


        # Scrape top comments
        status_callback(f"🔍 Scanning comments (min {self.min_comment_views:,} likes)...")
        top_comments = self._extract_top_comments(page)

        if not top_comments:
            status_callback(f"⚠️ No comments found with ≥{self.min_comment_views:,} likes. Skipping post.")
            log_interaction(post_url, "", "No Qualifying Comments", int(view_count), account_id=self.account_id)
            return False

        status_callback(
            f"✨ Found {len(top_comments)} top comment(s). "
            f"Top: \"{top_comments[0]['text'][:60]}...\" ({top_comments[0]['views']:,} views)"
        )

        # Generate 5 mimic variants
        status_callback("🤖 Generating 5 mimic reply variants based on top comments...")
        reply_text, variant_id = self._generate_and_get_variant(post_url, "mimic", post_text, top_comments)
        if not reply_text:
            status_callback("❌ AI failed to generate a mimic reply. Skipping.")
            log_interaction(post_url, "", "AI Generation Failed", int(view_count), account_id=self.account_id)
            return False

        status_callback(f"🤖 AI Mimic Reply (variant 1/5): \"{reply_text[:80]}...\"")
        logger.info(f"Generated mimic reply: {reply_text}")

        return self._post_reply_on_page(page, post_url, reply_text, view_count, status_callback, variant_id)

    def _reply_if_latest_comment_active(self, page, post_url, post_text, view_count, status_callback):
        """
        Open the post, switch to Latest comments, check the most recent comment's view count.
        If it meets min_comment_views threshold, generate a reply based on the post text and post it.
        Otherwise skip.
        """
        status_callback(f"📄 Opening post to check latest activity: {post_url}")
        try:
            page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector('article', timeout=15000)
            time.sleep(random.uniform(2, 4))
        except Exception as e:
            status_callback(f"❌ Failed to load post page: {e}")
            log_interaction(post_url, "", "Navigation Failed", int(view_count), account_id=self.account_id)
            return False

        # Check for modals immediately after loading
        self._handle_modals(page, skip_on_detect=True)


        # Switch to "Recent" sort order.
        # X's sort button shows the *current* sort: "Relevant" (default), "Recent", or "Likes".
        # Flow: find the button via JS → if not "Recent", click it → pick "Recent" from dropdown.
        status_callback("🔄 Switching to Recent comments order...")
        switched = False
        try:
            time.sleep(2)  # Let reply section render fully

            # Use JS to find the sort button by its *exact* trimmed text content.
            # We look for a small clickable element (button or div with role=button)
            # whose visible text exactly matches one of the known sort labels.
            sort_labels = ["Relevant", "Recent", "Likes", "Top", "Latest"]
            sort_btn = None
            current_label = None

            js_find_sort = """
            (labels) => {
                for (const label of labels) {
                    // Search buttons and role=button divs
                    const candidates = [
                        ...document.querySelectorAll('button'),
                        ...document.querySelectorAll('[role="button"]'),
                        ...document.querySelectorAll('[role="tab"]'),
                    ];
                    for (const el of candidates) {
                        const text = el.innerText ? el.innerText.trim() : '';
                        if (text === label) {
                            return label;
                        }
                    }
                }
                return null;
            }
            """
            current_label = page.evaluate(js_find_sort, sort_labels)

            if current_label:
                # Re-locate the element in Playwright for clicking
                # Use exact text match on button/role=button elements
                for role in ["button"]:
                    loc = page.get_by_role(role, name=current_label, exact=True)
                    if loc.count() > 0:
                        sort_btn = loc.first
                        break
                if not sort_btn:
                    # Fallback: any role=button with exact text
                    sort_btn = page.locator(
                        f'button, [role="button"], [role="tab"]'
                    ).filter(has_text=current_label).first

            if current_label == "Recent":
                switched = True
                status_callback("✅ Comments already in Recent order.")
            elif sort_btn and current_label:
                status_callback(f"🖱️ Found sort button: '{current_label}' — clicking to open menu...")
                sort_btn.scroll_into_view_if_needed()
                sort_btn.click()
                time.sleep(1.5)

                # Wait for the dropdown/menu to appear
                try:
                    page.wait_for_selector('[role="menu"], [role="listbox"], [role="menuitem"]', timeout=4000)
                except Exception:
                    pass

                # Find the "Recent" menu item — try multiple selectors
                recent_item = None
                for sel in [
                    page.get_by_role("menuitem", name="Recent", exact=True),
                    page.get_by_role("option", name="Recent", exact=True),
                    page.locator('[role="menuitem"]').filter(has_text="Recent"),
                    page.locator('[role="option"]').filter(has_text="Recent"),
                    page.locator('li').filter(has_text="Recent"),
                ]:
                    try:
                        if sel.count() > 0:
                            recent_item = sel.first
                            break
                    except Exception:
                        continue

                if recent_item:
                    recent_item.scroll_into_view_if_needed()
                    recent_item.click()
                    time.sleep(2)
                    switched = True
                    status_callback("✅ Switched to Recent comments.")
                else:
                    status_callback("⚠️ Dropdown opened but 'Recent' option not found.")
            else:
                # Last resort: try clicking a "Relevant"/"Top" tab/button directly via JS
                status_callback("⚠️ Could not find sort button via Playwright — trying JS click fallback...")
                clicked = page.evaluate("""
                    (labels) => {
                        const candidates = [
                            ...document.querySelectorAll('button'),
                            ...document.querySelectorAll('[role="button"]'),
                            ...document.querySelectorAll('[role="tab"]'),
                        ];
                        for (const label of labels) {
                            for (const el of candidates) {
                                const text = el.innerText ? el.innerText.trim() : '';
                                if (text === label) {
                                    el.click();
                                    return label;
                                }
                            }
                        }
                        return null;
                    }
                """, ["Relevant", "Top", "Likes", "Latest"])

                if clicked:
                    status_callback(f"🖱️ JS clicked sort button: '{clicked}' — waiting for dropdown...")
                    time.sleep(1.5)
                    try:
                        page.wait_for_selector('[role="menu"], [role="listbox"], [role="menuitem"]', timeout=4000)
                    except Exception:
                        pass

                    # Click "Recent" inside the now-open dropdown via JS
                    recent_clicked = page.evaluate("""
                        () => {
                            const items = [
                                ...document.querySelectorAll('[role="menuitem"]'),
                                ...document.querySelectorAll('[role="option"]'),
                                ...document.querySelectorAll('li'),
                            ];
                            for (const el of items) {
                                const text = el.innerText ? el.innerText.trim() : '';
                                if (text === 'Recent') {
                                    el.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    if recent_clicked:
                        time.sleep(2)
                        switched = True
                        status_callback("✅ Switched to Recent comments (via JS fallback).")
                    else:
                        status_callback("⚠️ JS fallback: dropdown open but 'Recent' not found.")
                else:
                    status_callback("⚠️ Could not find the sort button at all — reading in default order.")

        except Exception as e:
            logger.warning(f"Sort switch failed: {e}")
            status_callback("⚠️ Sort switch error — reading comments in default order.")


        # Re-query articles after sort switch
        try:
            page.wait_for_selector('article', timeout=8000)
        except Exception:
            pass
        time.sleep(1.5)

        try:
            articles = page.query_selector_all('article')
        except Exception as e:
            if "closed" in str(e).lower():
                status_callback("⚠️ Browser was closed during comment scan.")
                return False
            articles = []

        reply_articles = articles[1:]  # skip the original post article

        if not reply_articles:
            status_callback("⚠️ No comments found on this post. Skipping.")
            return False

        # Check if any of the 5 most recent comments meet the view threshold
        is_active = False
        max_to_check = min(5, len(reply_articles))
        found_active_views = 0
        
        status_callback(f"🔎 Checking top {max_to_check} latest comments for activity...")
        
        for i in range(max_to_check):
            views = self._extract_comment_view_count(page, reply_articles[i])
            if views >= self.min_comment_views:
                is_active = True
                found_active_views = views
                status_callback(f"✅ Found active comment at position {i+1} with {views:,} views.")
                break
        
        if not is_active:
            status_callback(
                f"⏭️ None of the top {max_to_check} latest comments met the {self.min_comment_views:,} views threshold. Skipping post."
            )
            return False

        # Post is still active — generate 5 reply variants based on the post's own content
        status_callback(
            f"🔥 Post is active! (Max views in top 5: {found_active_views:,}). Generating 5 reply variants..."
        )
        reply_text, variant_id = self._generate_and_get_variant(post_url, "reply", post_text)
        if not reply_text:
            status_callback("❌ AI failed to generate a reply. Skipping.")
            log_interaction(post_url, "", "AI Generation Failed", int(view_count), account_id=self.account_id)
            return False

        status_callback(f"🤖 AI Reply (variant 1/5): \"{reply_text[:80]}...\"")
        logger.info(f"Generated reply (latest-active mode): {reply_text}")

        return self._post_reply_on_page(page, post_url, reply_text, view_count, status_callback, variant_id)

    def _generate_and_get_variant(
        self, post_url: str, mode: str, post_text: str, top_comments: list = None
    ) -> tuple[str | None, int | None]:
        """
        Call the appropriate generator, return (first_reply_text, variant_db_id).
        Returns (None, None) on failure.
        """
        if mode == "mimic" and top_comments:
            text = self.generate_mimic_reply(post_url, post_text[:500], top_comments)
        else:
            text = self.generate_reply(post_url, post_text[:1000])

        if not text:
            return None, None

        # Retrieve the DB id for variant 0 (the one we're about to post)
        from db_manager import get_next_variant
        v = get_next_variant(post_url, account_id=self.account_id)
        variant_id = v["id"] if v else None
        return text, variant_id

    def _post_reply_on_page(self, page, post_url, reply_text, view_count, status_callback, variant_id=None):
        """Shared helper: find the reply box on the current post page and submit a reply."""
        # This is always called in a new tab or specific post page, so skip on modal
        self._handle_modals(page, skip_on_detect=True)
        status_callback("✍️ Looking for reply input box...")

        reply_posted = False

        # Strategy 1: Direct reply box on the post page
        reply_box = page.query_selector('div[data-testid="tweetTextarea_0"]')

        if not reply_box:
            # Strategy 2: Click the reply button/icon first to open the reply area
            status_callback("🔍 Reply box not visible, clicking reply button...")
            reply_button = page.query_selector('div[data-testid="reply"]')
            if reply_button:
                try:
                    reply_button.scroll_into_view_if_needed()
                    time.sleep(0.5)
                    reply_button.click(timeout=5000)
                    time.sleep(2)
                    reply_box = page.query_selector('div[data-testid="tweetTextarea_0"]')
                except Exception as e:
                    # A modal might have appeared late, check and skip if true
                    self._handle_modals(page, skip_on_detect=True)
                    logger.error(f"Failed to click reply button: {e}")

        if not reply_box:
            # Strategy 3: Try contenteditable div
            reply_box = page.query_selector('div[contenteditable="true"][data-testid="tweetTextarea_0"]')

        if not reply_box:
            # Strategy 4: Look for any contenteditable reply area
            reply_box = page.query_selector('div.public-DraftEditor-content[contenteditable="true"]')

        if reply_box:
            try:
                status_callback("📝 Typing reply...")
                reply_box.scroll_into_view_if_needed()
                time.sleep(0.5)
                
                try:
                    reply_box.click(timeout=5000)
                except Exception as e:
                    # Late-appearing modal might be intercepting pointer events
                    self._handle_modals(page, skip_on_detect=True)
                    # If no modal was found, try forcing the click
                    reply_box.click(force=True, timeout=3000)
                    
                time.sleep(0.5)

                # Type with human-like delays
                page.keyboard.type(reply_text, delay=random.randint(30, 80))
                time.sleep(1)

                # If the last word is a mention or hashtag (e.g. @abc, #bill), 
                # it will likely trigger an autocomplete dropdown that can block the Reply button.
                words = reply_text.strip().split()
                if words and (words[-1].startswith('@') or words[-1].startswith('#')):
                    logger.info(f"Last word '{words[-1]}' detected as mention/hashtag, ensuring dropdown is dismissed.")
                    # Adding a space is a reliable way to 'confirm' the text and close the dropdown
                    page.keyboard.type(" ")
                    time.sleep(0.5)

                # Explicitly check for and dismiss any visible autocomplete dropdowns
                try:
                    # X uses role="listbox" or data-testid="TypeaheadUserSuggestions" for mentions/hashtags
                    typeahead = page.query_selector('[role="listbox"], [data-testid="TypeaheadUserSuggestions"]')
                    if typeahead and typeahead.is_visible():
                        logger.info("Autocomplete dropdown detected, dismissing with Escape.")
                        page.keyboard.press("Escape")
                        time.sleep(0.5)
                        # Ensure Escape didn't trigger 'Discard post?' modal if the dropdown wasn't truly capturing it
                        self._handle_modals(page)
                except:
                    pass
                
                time.sleep(1)


                # Find and click the send/reply button
                send_button = (
                    page.query_selector('div[data-testid="tweetButtonInline"]') or
                    page.query_selector('button[data-testid="tweetButtonInline"]') or
                    page.query_selector('div[data-testid="tweetButton"]') or
                    page.query_selector('button[data-testid="tweetButton"]')
                )

                if send_button:
                    status_callback("📤 Sending reply...")
                    time.sleep(random.uniform(0.5, 1.5))
                    
                    try:
                        # Check if the button is aria-disabled (sometimes happens when autocomplete is active)
                        is_disabled = page.evaluate("el => el.getAttribute('aria-disabled') === 'true' || el.disabled", send_button)
                        if is_disabled:
                            logger.info("Send button is disabled, trying to refresh state with Tab/Shift+Tab and space/backspace...")
                            # Force focus change to trigger UI update
                            page.keyboard.press("Tab")
                            time.sleep(0.3)
                            page.keyboard.press("Shift+Tab")
                            time.sleep(0.3)
                            # Standard state refresh
                            page.keyboard.type(" ")
                            time.sleep(0.3)
                            page.keyboard.press("Backspace")
                            time.sleep(0.5)

                        # Use force=True to click even if obscured by an autocomplete dropdown
                        send_button.click(force=True, timeout=5000)
                    except Exception as e:
                        logger.warning(f"Click failed, trying fallback: {e}")
                        # Fallback 1: JS click
                        try:
                            page.evaluate("el => el.click()", send_button)
                        except:
                            # Fallback 2: Keyboard shortcut
                            page.keyboard.press("Control+Enter")
                        
                    time.sleep(3)


                    reply_posted = True
                    # Mark this variant as posted in the DB (so next run uses variant 2, etc.)
                    if variant_id is not None:
                        try:
                            mark_variant_posted(variant_id, int(view_count))
                        except Exception as _ve:
                            logger.warning(f"Could not mark variant posted: {_ve}")

                    log_interaction(post_url, reply_text, "Success", int(view_count), account_id=self.account_id)
                    status_callback(f"✅ Reply posted successfully!")
                    logger.info(f"Reply posted to {post_url}")
                else:
                    status_callback("❌ Could not find the send button.")
                    log_interaction(post_url, reply_text, "Send Button Not Found", int(view_count), account_id=self.account_id)
            except Exception as e:
                status_callback(f"❌ Error posting reply: {e}")
                log_interaction(post_url, reply_text, f"Error: {str(e)[:100]}", int(view_count), account_id=self.account_id)
                logger.error(f"Error posting reply: {e}")
        else:
            status_callback("❌ Could not find reply input box on post page.")
            log_interaction(post_url, reply_text, "Reply Box Not Found", int(view_count), account_id=self.account_id)

        # Wait before navigating away — Gaussian for human-like distribution
        if reply_posted:
            gaussian_sleep(22, 6, 10)
            status_callback("⏳ Waiting before next action (anti-detection)...")

        return reply_posted

    def _re_reply_to_posts(self, page, status_callback) -> int:
        """
        Strategy: 'Re-Reply to Post'
        -------------------------------------------------
        1. Navigate to the logged-in account's Replies tab.
        2. For each reply found, extract the view count of that reply tweet.
        3. If views >= min_comment_views:
               - Find the next un-posted variant for that post URL.
               - Navigate to the post and post the variant reply.
        4. If views < threshold: skip.
        Returns the number of new comments posted.
        """
        status_callback("♻️ [Re-Reply] Navigating to your Replies tab...")
        comments_posted = 0

        # --- Resolve the current account's username ---
        username = None
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)
            # The profile link in the sidebar always contains the username
            profile_link = page.query_selector('a[data-testid="AppTabBar_Profile_Link"]')
            if profile_link:
                href = profile_link.get_attribute("href") or ""
                username = href.strip("/").split("/")[-1] or None
        except Exception as e:
            logger.warning(f"Re-Reply: could not detect username: {e}")

        if not username:
            status_callback("❌ [Re-Reply] Could not detect logged-in username. Aborting.")
            return 0

        replies_url = f"https://x.com/{username}/with_replies"
        status_callback(f"👤 [Re-Reply] Scanning replies for @{username}...")

        try:
            page.goto(replies_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector('article', timeout=15000)
            time.sleep(2)
        except Exception as e:
            status_callback(f"❌ [Re-Reply] Failed to load replies tab: {e}")
            return 0

        # Build a set of post_urls that have pending variants — fast lookup
        pending_post_urls = set(get_posts_with_pending_variants(account_id=self.account_id))
        if not pending_post_urls:
            status_callback("ℹ️ [Re-Reply] No posts with queued variants found. Run a normal strategy first.")
            return 0

        status_callback(f"📋 [Re-Reply] {len(pending_post_urls)} posts have queued variants.")

        processed_reply_urls: set[str] = set()
        scroll_count = 0
        max_scrolls = 30
        no_new_count = 0

        while (
            comments_posted < self.max_comments
            and not self.stop_requested
            and scroll_count < max_scrolls
        ):
            scroll_count += 1
            articles = page.query_selector_all('article')
            new_found = 0

            for article in articles:
                if self.stop_requested or comments_posted >= self.max_comments:
                    break
                try:
                    # Get the canonical URL of this reply tweet
                    reply_url = self._extract_post_url(article)
                    if not reply_url:
                        continue
                    
                    if reply_url in processed_reply_urls:
                        continue
                        
                    processed_reply_urls.add(reply_url)
                    new_found += 1
                    
                    status_callback(f"🔍 [Re-Reply] Processing reply: ...{reply_url[-20:]}")

                    # Derive the original post URL.
                    # A reply tweet URL looks like: https://x.com/user/status/ID
                    # The parent post URL is stored in pending_post_urls.
                    parent_url = None
                    links = article.query_selector_all("a[href*='/status/']") or []
                    for link in links:
                        href = link.get_attribute("href") or ""
                        # Convert to full URL
                        candidate = ("https://x.com" + href) if href.startswith("/") else href
                        # Canonicalize: strip query params, /analytics, etc.
                        match = re.search(r"(https://x\.com/[^/]+/status/\d+)", candidate)
                        if match:
                            candidate = match.group(1)
                        else:
                            candidate = candidate.split('?')[0].rstrip("/")

                        if candidate in pending_post_urls and candidate != reply_url:
                            parent_url = candidate
                            break

                    if not parent_url:
                        # Fallback: Extract text and look up in DB
                        text_el = article.query_selector('[data-testid="tweetText"]')
                        if text_el:
                            reply_text_content = text_el.inner_text().strip()
                            # Clean up the text (remove newlines, etc. to match DB)
                            reply_text_content = reply_text_content.replace("\n", " ")
                            parent_url = get_post_url_by_variant_text(reply_text_content, account_id=self.account_id)
                            if parent_url:
                                status_callback(f"🔗 [Re-Reply] Linked reply to parent via content match.")

                    if not parent_url:
                        # status_callback(f"⏭️ [Re-Reply] No queued parent found for reply ...{reply_url[-15:]}")
                        continue

                    # Check the view count of THIS reply tweet
                    reply_views = self._extract_comment_view_count(page, article)
                    status_callback(
                        f"👁️ [Re-Reply] Reply views: {reply_views:,} "
                        f"(threshold: {self.min_comment_views:,}) — post: {parent_url[-40:]}"
                    )

                    if reply_views < self.min_comment_views:
                        status_callback(
                            f"⏭️ [Re-Reply] {reply_views:,} views < threshold. Skipping."
                        )
                        continue

                    # Fetch the next un-posted variant for this parent post
                    variant = get_next_variant(parent_url, account_id=self.account_id)
                    if not variant:
                        status_callback(
                            f"ℹ️ [Re-Reply] No more queued variants for {parent_url[-40:]}. Skipping."
                        )
                        pending_post_urls.discard(parent_url)  # nothing left for this post
                        continue

                    v_idx = variant["variant_index"] + 1
                    status_callback(
                        f"🔥 [Re-Reply] Posting variant #{v_idx}/5 on active post ({reply_views:,} views)..."
                    )

                    # Navigate to the parent post and post the reply in a NEW TAB
                    new_page = page.context.new_page()
                    Stealth().apply_stealth_sync(new_page)
                    success = False
                    try:
                        status_callback(f"📄 [Re-Reply] Opening post in new tab: {parent_url}")
                        new_page.goto(parent_url, wait_until="domcontentloaded", timeout=30000)
                        new_page.wait_for_selector('article', timeout=15000)
                        time.sleep(random.uniform(2, 4))
                        
                        success = self._post_reply_on_page(
                            new_page, parent_url, variant["reply_text"],
                            reply_views, status_callback, variant["id"]
                        )
                    except ModalDetectedException:
                        status_callback(f"⏭️ [Re-Reply] Skipping post: UI blocked by popup modal.")
                        success = False
                    except Exception as e:
                        status_callback(f"❌ [Re-Reply] Failed to load or process post page: {e}")
                        success = False
                    finally:
                        new_page.close()
                    
                    if success:
                        comments_posted += 1
                        status_callback(
                            f"📊 [Re-Reply] Progress: {comments_posted}/{self.max_comments} comments posted"
                        )
                        # Update pending set
                        if not get_next_variant(parent_url, account_id=self.account_id):
                            pending_post_urls.discard(parent_url)
                    
                    # No need to return to replies tab, we never left it!
                    time.sleep(random.uniform(1, 2))

                except Exception as e:
                    logger.error(f"Re-Reply: error processing article: {e}")
                    continue

            if new_found == 0:
                no_new_count += 1
                if no_new_count >= 5:
                    status_callback("⚠️ [Re-Reply] No new replies found. Ending scan.")
                    break
            else:
                no_new_count = 0

            # Scroll down to reveal more replies
            try:
                page.evaluate(f"window.scrollBy(0, {random.randint(600, 1000)})")
            except Exception as e:
                if "closed" in str(e).lower():
                    break
            time.sleep(random.uniform(2, 3))

        return comments_posted

    def run(self, status_callback):
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=False,
                channel="msedge",
                ignore_default_args=["--enable-automation"],
                args=["--disable-blink-features=AutomationControlled"],
                user_agent=USER_AGENT,
                viewport={'width': 1920, 'height': 1080},
                locale="en-US"
            )

            page = context.pages[0] if context.pages else context.new_page()

            # Apply stealth
            Stealth().apply_stealth_sync(page)

            status_callback("🌐 Navigating to X...")
            page.goto("https://x.com/home")

            # Check if logged in
            time.sleep(5)
            if "login" in page.url:
                status_callback("🔑 Not logged in. Please log in manually. You have 5 minutes.")
                try:
                    page.wait_for_timeout(300000)  # 5 minutes
                except Exception as e:
                    logger.error(f"Wait timeout error: {e}")

                if self.stop_requested:
                    context.close()
                    return
                status_callback("▶️ Proceeding after login wait...")

            # Wait for timeline to load
            status_callback("⏳ Waiting for timeline to load...")
            try:
                page.wait_for_selector('article', timeout=15000)
                gaussian_sleep(3.0, 1.0, 2.0)
            except Exception:
                status_callback("⚠️ Timeline took long to load, proceeding anyway...")

            # Warm-up: do a few human-like actions before starting to comment
            status_callback("🧘 Warming up session (simulating human browsing)...")
            self._do_human_actions(page, status_callback)
            gaussian_sleep(4.0, 1.5, 2.0)

            comments_posted = 0
            posts_scanned = 0
            scroll_count = 0
            max_scrolls = self.max_posts * 3  # Allow enough scrolling to find posts
            no_new_posts_count = 0
            posts_since_human_action = 0
            human_action_interval = random.randint(5, 10)  # Trigger every 5-10 posts

            status_callback(f"🚀 Starting scan. Target: {self.max_posts} posts, {self.max_comments} comments, min views: {self.view_threshold:,}")

            # ----------------------------------------------------------------
            # Strategy: Re-Reply to Post — operates on the Replies tab, not
            # the timeline, so it runs separately and then exits.
            # ----------------------------------------------------------------
            if self.comment_strategy == "Re-Reply to Post":
                posted = self._re_reply_to_posts(page, status_callback)
                status_callback(f"🏁 [Re-Reply] Done! Posted {posted} queued variant(s).")
                context.close()
                return

            while (posts_scanned < self.max_posts and 
                   comments_posted < self.max_comments and 
                   not self.stop_requested and
                   scroll_count < max_scrolls):

                scroll_count += 1

                # Find all post articles currently visible
                posts = page.query_selector_all("article")
                new_posts_found = 0

                for post in posts:
                    if self.stop_requested or comments_posted >= self.max_comments:
                        break

                    try:
                        # Extract the post URL for deduplication
                        post_url = self._extract_post_url(post)
                        if not post_url:
                            continue

                        # Skip if already processed
                        if post_url in self.processed_urls:
                            continue

                        # Mark as processed so we don't evaluate again
                        self.processed_urls.add(post_url)
                        new_posts_found += 1
                        posts_scanned += 1
                        posts_since_human_action += 1

                        # Periodically do human actions every 5-10 posts
                        if posts_since_human_action >= human_action_interval:
                            status_callback(f"🧘 [{posts_scanned}/{self.max_posts}] Taking a human-like break...")
                            self._do_human_actions(page, status_callback)
                            posts_since_human_action = 0
                            human_action_interval = random.randint(5, 10)  # Randomize next interval

                        # Skip if already replied to (from DB)
                        if post_url in self.replied_urls:
                            status_callback(f"⏭️ [{posts_scanned}/{self.max_posts}] Already replied to this post, skipping.")
                            continue

                        # Get post text (entire article text for metrics extraction)
                        post_text = post.inner_text()
                        
                        # Extract actual tweet content text (the post body)
                        tweet_text_el = post.query_selector('[data-testid="tweetText"]')
                        tweet_content = tweet_text_el.inner_text().strip() if tweet_text_el else ""
                        
                        # Check text length: skip if < 50 characters (as requested)
                        if len(tweet_content) < 50:
                            status_callback(f"⏭️ [{posts_scanned}/{self.max_posts}] Content is too short ({len(tweet_content)} characters), skipping.")
                            continue

                        # Extract view count
                        view_count = self._extract_view_count(page, post, post_text)

                        # Extract a short preview of the post from the actual content
                        preview = tweet_content[:60].replace("\n", " ")

                        if view_count < self.view_threshold:
                            # 15% chance: like this post randomly even if below threshold
                            if random.random() < 0.15:
                                if self._try_like_post(page, post):
                                    status_callback(
                                        f"👍 [{posts_scanned}/{self.max_posts}] "
                                        f"Liked a post randomly."
                                    )
                            status_callback(
                                f"👁️ [{posts_scanned}/{self.max_posts}] "
                                f"\"{preview}...\" — {view_count:,} views (below {self.view_threshold:,} threshold)"
                            )
                            continue

                        # 15% chance: like the qualifying post before (or instead of) commenting
                        if random.random() < 0.15:
                            if self._try_like_post(page, post):
                                status_callback(
                                    f"👍 [{posts_scanned}/{self.max_posts}] "
                                    f"Liked qualifying post."
                                )
                        
                        # Check for Premium Account if enabled
                        if self.premium_only:
                            if not self._is_premium_account(post):
                                status_callback(
                                    f"⏭️ [{posts_scanned}/{self.max_posts}] "
                                    f"\"{preview}...\" — Not a Premium account, skipping."
                                )
                                continue

                        # This post qualifies!
                        status_callback(
                            f"🎯 [{posts_scanned}/{self.max_posts}] HIGH-VIEW POST FOUND! "
                            f"{view_count:,} views — \"{preview}...\""
                        )
                        logger.info(f"Qualifying post: {post_url} with {view_count} views")

                        # Enter the post's comment section and reply in a NEW TAB
                        new_page = page.context.new_page()
                        Stealth().apply_stealth_sync(new_page)
                        success = False
                        try:
                            if self.comment_strategy == "Mimic Top Comments":
                                success = self._mimic_top_comment_and_reply(new_page, post_url, tweet_content, view_count, status_callback)
                            elif self.comment_strategy == "Reply if Latest Comment Active":
                                success = self._reply_if_latest_comment_active(new_page, post_url, tweet_content, view_count, status_callback)
                            else:
                                success = self._enter_post_and_reply(new_page, post_url, tweet_content, view_count, status_callback)
                        except ModalDetectedException:
                            status_callback(f"⏭️ Skipping post: UI blocked by popup modal.")
                            success = False
                        except Exception as e:
                            status_callback(f"⚠️ Error in new tab processing: {str(e)[:80]}")
                        finally:
                            new_page.close()
 
                        if success:
                            comments_posted += 1
                            self.replied_urls.add(post_url)
                            status_callback(f"📊 Progress: {comments_posted}/{self.max_comments} comments posted")

                        # No need to navigate back to timeline, we never left it!
                        time.sleep(random.uniform(1, 2))

                    except Exception as e:
                        logger.error(f"Error processing post: {e}")
                        status_callback(f"⚠️ Error processing a post: {str(e)[:80]}")
                        continue

                # If no new posts were found in this batch, we need to scroll more
                if new_posts_found == 0:
                    no_new_posts_count += 1
                    if no_new_posts_count >= 5:
                        status_callback("⚠️ No new posts found after multiple scrolls. Ending scan.")
                        break
                else:
                    no_new_posts_count = 0

                # Scroll down to load more posts
                scroll_amount = random.randint(600, 1200)
                status_callback(f"📜 Scrolling for more posts... (scroll #{scroll_count})")
                try:
                    page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                except Exception as e:
                    if "closed" in str(e).lower():
                        status_callback("⚠️ Browser was closed. Stopping automation.")
                        break
                    logger.warning(f"Scroll error: {e}")
                time.sleep(random.uniform(2, 4))

            # Final summary
            status_callback(
                f"🏁 Done! Scanned {posts_scanned} posts, posted {comments_posted} comments. "
                f"Scrolled {scroll_count} times."
            )
            context.close()


def start_login_session(account_id="default"):
    """Open a browser for manual login, saving session to account-specific profile dir."""
    profile_dir = get_profile_dir(account_id)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            channel="msedge",
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=USER_AGENT,
            viewport={'width': 1920, 'height': 1080},
            locale="en-US"
        )
        page = context.pages[0] if context.pages else context.new_page()

        # Apply stealth
        Stealth().apply_stealth_sync(page)

        page.goto("https://x.com/home")

        print(f"Waiting 5 minutes for manual login (account: {account_id})... Browser will stay open.")
        try:
            page.wait_for_timeout(300000)  # 5 minutes
        except Exception as e:
            print(f"Browser closed or error: {e}")

        print("Closing session.")
        context.close()
