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

# 2FA Page Selectors
TWO_FA = {
    "code_input": [
        'input[name="approvals_code"]',
        'input[id="approvals_code"]',
        'input[type="text"]',
        'input[type="tel"]',
        'input[type="number"]',
    ],
    "submit_button": [
        'button[type="submit"]',
        'button:has-text("Continue")',
        'button:has-text("Submit")',
        'div[role="button"]:has-text("Continue")',
    ],
    "trust_device_checkbox": [
        'input[type="checkbox"]',
        'input[name="name_action_selected"]',
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
        'div[aria-label="Comment"]',
        'span:has-text("Comment")',
        'div[role="button"]:has-text("Comment")',
        'a[href*="comment"]',
    ],
    "comment_input": [
        'div[contenteditable="true"]',
        'textarea[name="comment_text"]',
        'div[aria-label*="Write a comment"]',
        'div[role="textbox"]',
    ],
    "comment_submit": [
        'div[aria-label="Post"]',
        'button:has-text("Post")',
        'div[role="button"]:has-text("Post")',
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
        "two_fa": TWO_FA,
        "feed": FEED,
        "reels": REELS,
        "comment": COMMENT,
        "notifications": NOTIFICATIONS,
        "nav": NAV,
        "page_state": PAGE_STATE,
    }
    return categories.get(category, {})
