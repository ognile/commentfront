"""
Reddit mobile-web selectors and intent helpers.
"""

LOGIN = {
    "username_input": [
        'input[name="loginUsername"]',
        'input[name="username"]',
        'input[autocomplete="username"]',
        'input[type="text"]',
        'input[type="email"]',
    ],
    "password_input": [
        'input[name="loginPassword"]',
        'input[name="password"]',
        'input[type="password"]',
    ],
    "submit_button": [
        'button[type="submit"]',
        'button.login',
        'button:has-text("Log In")',
        'button:has-text("Continue")',
    ],
    "otp_input": [
        'input[name="otp"]',
        'input[name="code"]',
        'input[name="appOtp"]',
        'input[autocomplete="one-time-code"]',
    ],
    "otp_submit": [
        'button:has-text("Continue")',
        'button:has-text("Verify")',
        'button:has-text("Log In")',
    ],
    "modal_close": [
        'button[aria-label="Close"]',
        'button:has-text("close")',
    ],
}

COOKIE_BANNER = {
    "accept": [
        'button:has-text("Accept All")',
    ],
    "reject": [
        'button:has-text("Reject Optional Cookies")',
    ],
}

HOME = {
    "post_article": [
        "article",
    ],
    "post_link": [
        'article a[href*="/comments/"]',
    ],
    "upvote_button": [
        'button[aria-label*="upvote" i]',
        'button:has-text("Upvote")',
    ],
    "comment_link": [
        'a[href*="/comments/"]',
    ],
}

POST = {
    "title_input": [
        'textarea[placeholder*="Title" i]',
        'input[placeholder*="Title" i]',
        'input[name="title"]',
    ],
    "body_input": [
        'div[contenteditable="true"]',
        'textarea[name="body"]',
        'textarea[placeholder*="body" i]',
    ],
    "community_button": [
        'button:has-text("Choose a community")',
        'button[aria-label*="community" i]',
    ],
    "post_button": [
        'button:has-text("Post")',
        'button[aria-label="Post"]',
    ],
    "media_input": [
        'input[type="file"]',
    ],
}

COMMENT = {
    "composer_input": [
        'div[contenteditable="true"]',
        'textarea',
        'textarea[placeholder*="comment" i]',
        'textarea[aria-label*="comment" i]',
    ],
    "submit_button": [
        'button:has-text("Comment")',
        'button[aria-label*="comment" i]',
    ],
    "reply_button": [
        'button:has-text("Reply")',
        'button[aria-label*="reply" i]',
    ],
    "reply_input": [
        'div[contenteditable="true"]',
        'textarea',
        'textarea[placeholder*="reply" i]',
        'textarea[aria-label*="reply" i]',
    ],
}
