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
        'button:has-text("Check code")',
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
    "composer_trigger": [
        'button:has-text("Join the conversation")',
        'textarea[placeholder*="join the conversation" i]',
        'input[placeholder*="join the conversation" i]',
        '[placeholder*="join the conversation" i]',
        '[aria-label*="join the conversation" i]',
    ],
    "composer_input": [
        'div[contenteditable="true"]',
        'div[contenteditable="plaintext-only"]',
        'textarea',
        'textarea[placeholder*="comment" i]',
        'textarea[aria-label*="comment" i]',
        'textarea[placeholder*="join the conversation" i]',
        'input[placeholder*="join the conversation" i]',
    ],
    "submit_button": [
        'button:has-text("Comment")',
        'button[aria-label*="comment" i]',
    ],
    "share_button": [
        'button:has-text("Share")',
        'button[aria-label*="share" i]',
    ],
    "search_comments_input": [
        'input[placeholder*="search comments" i]',
        'input[aria-label*="search comments" i]',
    ],
    "reply_button": [
        'button:has-text("Reply")',
        'button[aria-label*="reply" i]',
    ],
    "reply_submit_button": [
        'button:has-text("Reply")',
        'button[aria-label*="reply" i]',
        'button:has-text("Comment")',
        'button[aria-label*="comment" i]',
    ],
    "reply_input": [
        'div[contenteditable="true"]',
        'div[contenteditable="plaintext-only"]',
        'textarea',
        'textarea[placeholder*="reply" i]',
        'textarea[aria-label*="reply" i]',
        'textarea[placeholder*="join the conversation" i]',
        'input[placeholder*="join the conversation" i]',
    ],
}

SUBREDDIT = {
    "join_button": [
        'button:has-text("Join")',
        'button[aria-label*="join" i]',
    ],
    "joined_button": [
        'button:has-text("Joined")',
        'button[aria-label*="joined" i]',
    ],
}
