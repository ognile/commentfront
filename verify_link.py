from backend.url_utils import clean_facebook_url, resolve_facebook_redirect, is_url_safe_for_geelark
import logging
import sys

# Disable logging for clean output
logging.getLogger("URLUtils").setLevel(logging.ERROR)

url = "https://www.facebook.com/permalink.php?story_fbid=pfbid05PW4jAjxm88wTv6QeFGQuStyENytRAak8AKpJXmSNuMdRFFLakVuKvQjGr4c7DDml&id=61574636237654"

print(f"1. ORIGINAL LINK:")
print(f"   URL: {url}")
print(f"   Length: {len(url)} characters")
print(f"   Status: {'REJECTED (Too long)' if not is_url_safe_for_geelark(url) else 'ACCEPTED'}")
print("-" * 50)

# Step 1: Clean
cleaned = clean_facebook_url(url)
print(f"2. AFTER CLEANING:")
print(f"   URL: {cleaned}")
print(f"   Length: {len(cleaned)} characters")
print("-" * 50)

# Step 2: Resolve Redirect
print(f"3. RESOLVING REDIRECT (Contacting Facebook)...")
resolved = resolve_facebook_redirect(cleaned)

# Step 3: Final Clean (in case FB added params)
final = clean_facebook_url(resolved)

print(f"4. FINAL OPTIMIZED LINK:")
print(f"   URL: {final}")
print(f"   Length: {len(final)} characters")
print(f"   Status: {'✅ SUCCESS - COMPATIBLE WITH GEELARK' if is_url_safe_for_geelark(final) else '❌ STILL TOO LONG'}")
