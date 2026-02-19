"""
Mobile Facebook CSS Selectors
Pre-mapped selectors for fast element detection on m.facebook.com
"""

# Login Page Selectors
LOGIN = {
    # Input fields
    "email_input": [
        'input[name="email"]',
        'input[type="email"]',
        'input[type="text"][autocomplete="username"]',
    ],
    "password_input": [
        'input[name="pass"]',
        'input[type="password"]',
    ],
    # Login button - multiple possible selectors
    "login_button": [
        'button[name="login"]',
        'button[type="submit"]',
        'button:has-text("Log in")',
        'button:has-text("Log In")',
        'div[role="button"]:has-text("Log in")',
    ],
}

# Signup/Welcome Page Selectors (when Facebook redirects away from login)
SIGNUP_PROMPT = {
    "already_have_account": [
        'div[role="button"][aria-label="I already have an account"]',
        'div[role="button"]:has-text("already have an account")',
        'button:has-text("already have an account")',
        'a:has-text("already have an account")',
        'div:has-text("I already have an account"):visible',
    ],
}

# 2FA Page Selectors
TWO_FA = {
    # 2FA method selection (choosing authenticator app)
    "auth_app_option": [
        'div[role="button"]:has-text("Authenticator")',
        'div[role="button"]:has-text("authentication app")',
        'div[role="button"]:has-text("Code Generator")',
        'div:has-text("Authenticator"):visible',
        'div:has-text("Code Generator"):visible',
        'span:has-text("Authenticator")',
        'span:has-text("Code Generator")',
        '[data-sigil*="auth"]',
    ],
    # Code input field
    "code_input": [
        'input[name="approvals_code"]',
        'input[id="approvals_code"]',
        'input[placeholder*="Enter code"]',
        'input[placeholder*="enter the 6"]',
        'input[type="text"]',
        'input[type="tel"]',
        'input[type="number"]',
    ],
    # Submit/Continue button after entering code
    "submit_button": [
        'button[type="submit"]',
        'button:has-text("Continue")',
        'button:has-text("Submit")',
        'div[role="button"]:has-text("Continue")',
        'div[role="button"]:has-text("Submit")',
        '[data-sigil*="submit"]',
    ],
    # Trust device checkbox
    "trust_device_checkbox": [
        'input[type="checkbox"]',
        'input[name="name_action_selected"]',
        'label:has-text("Trust")',
        'label:has-text("Remember")',
    ],
    # Trust device button
    "trust_device_button": [
        'div[role="button"]:has-text("Trust")',
        'button:has-text("Trust")',
        'div[role="button"]:has-text("Remember")',
        'button:has-text("Remember")',
        'div[role="button"]:has-text("Save")',
    ],
}

# Feed/Home Page Selectors
FEED = {
    "feed_container": [
        'div[role="feed"]',
        'div[data-pagelet="FeedUnit"]',
        '#m_newsfeed_stream',
    ],
    "post": [
        'div[data-pagelet^="FeedUnit"]',
        'article',
        'div[role="article"]',
    ],
    "like_button": [
        'div[aria-label="Like"]',
        'span:has-text("Like")',
        'div[role="button"]:has-text("Like")',
    ],
}

# Reels Selectors
REELS = {
    "reels_tab": [
        'a[href*="/reel"]',
        'a[href*="reels"]',
        'div[aria-label="Reels"]',
        'span:has-text("Reels")',
    ],
    "reel_video": [
        'video',
        'div[data-pagelet="Reels"]',
    ],
    "next_reel": [
        'div[aria-label="Next"]',
        'button[aria-label="Next"]',
    ],
}

# Comment Selectors
COMMENT = {
    "comment_button": [
        # Icon-based selector - ONLY comment button starts with 󰍹 icon
        # Verified pattern: 󰍹comment, 󰍹 1comments, 󰍹 2comments
        'div[role="button"][aria-label^="󰍹"]',
        # Backup: partial match with exclusion to avoid reactions counter
        'div[role="button"][aria-label*="omment"]:not([aria-label*="reacted"])',
        # Legacy selectors (kept for compatibility)
        'div[aria-label="Comment"]',
        'div[aria-label="Leave a comment"]',
        '[data-sigil*="comment"]',
    ],
    "comment_input": [
        # Facebook mobile comment input - various possible selectors
        'div[aria-label="Write a comment..."]',  # Exact match (Gemini suggested)
        'div[aria-label*="Write a comment"]',    # Partial match
        'div[contenteditable="true"]',
        'div[role="textbox"]',
        'textarea[role="combobox"]',             # Some posts render textarea instead of div
        'textarea[name="comment_text"]',
        'input[placeholder*="Write a comment"]',
        'div[data-placeholder*="comment"]',
        '[contenteditable][aria-label*="comment"]',
    ],
    "comment_submit": [
        # Facebook mobile send/post button
        'div[aria-label="Post a comment"]',  # Found via element audit
        'div[aria-label="Post"]',
        'div[aria-label="Send"]',
        'div[aria-label="Submit"]',
        'button:has-text("Post")',
        'div[role="button"]:has-text("Post")',
        '[data-sigil*="submit-comment"]',
        'button[type="submit"]',
    ],
}

# Notifications
NOTIFICATIONS = {
    "bell_icon": [
        'a[href*="/notifications"]',
        'div[aria-label="Notifications"]',
        'span:has-text("Notifications")',
    ],
    "notification_item": [
        'div[role="listitem"]',
        'a[href*="notif"]',
    ],
}

# Navigation
NAV = {
    "home": [
        'a[href="/"]',
        'a[aria-label="Home"]',
    ],
    "menu": [
        'div[aria-label="Menu"]',
        'a[href*="/menu"]',
    ],
}

# State detection - to check what page we're on
PAGE_STATE = {
    "logged_in_indicators": [
        'div[aria-label="Create a post"]',
        'a[href*="/notifications"]',
        'div[role="feed"]',
        'input[placeholder*="Search"]',
    ],
    "login_page_indicators": [
        'input[name="email"]',
        'button[name="login"]',
    ],
    "two_fa_indicators": [
        'input[name="approvals_code"]',
        'text="authentication app"',
        'text="Enter the 6-digit code"',
    ],
    "checkpoint_indicators": [
        'text="checkpoint"',
        'text="secure your account"',
        'text="confirm your identity"',
    ],
}


def get_selectors(category: str) -> dict:
    """Get selectors by category name"""
    categories = {
        "login": LOGIN,
        "signup_prompt": SIGNUP_PROMPT,
        "two_fa": TWO_FA,
        "feed": FEED,
        "reels": REELS,
        "comment": COMMENT,
        "notifications": NOTIFICATIONS,
        "nav": NAV,
        "page_state": PAGE_STATE,
    }
    return categories.get(category, {})
