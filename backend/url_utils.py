import re
import logging
import requests
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

logger = logging.getLogger("URLUtils")

def resolve_facebook_redirect(url: str, timeout: int = 10) -> str:
    """
    Follow redirects and scrape numeric IDs from Facebook page content.
    Converts pfbid format to numeric post IDs when possible.
    """
    if not url:
        return url

    # If it's already a short-form URL with numeric ID, don't resolve
    if 'posts/' in url and 'pfbid' not in url:
        return url

    try:
        # Use desktop user agent for better chance of finding numeric IDs
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        # Ensure url has scheme for requests
        fetch_url = url if '://' in url else f"https://{url}"

        resp = requests.get(fetch_url, allow_redirects=True, timeout=timeout, headers=headers)
        final_url = resp.url
        content = resp.text

        # Try to find Numeric Post ID from page content
        post_id = None
        patterns = [
            r'"post_id":"(\d+)"',
            r'"top_level_post_id":"(\d+)"',
            r'fb://post/(\d+)',
            r'"story_fbid":"(\d+)"',
            r'data-ft=\\"{\\"top_level_post_id\\":\\"(\d+)\\"',
            r'"identifier":"(\d+)"',  # Try JSON-LD
            r'/posts/(\d+)',  # From redirected URL
            r'"mf_story_key":"(\d+)"',  # Mobile feed story key
        ]
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                post_id = match.group(1)
                logger.info(f"Found numeric post_id: {post_id}")
                break

        # Extract page_id from URL or content
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        page_id = query.get('id', [None])[0]

        if not page_id:
            page_id_match = re.search(r'"page_id":"(\d+)"', content)
            if page_id_match:
                page_id = page_id_match.group(1)

        # If we found both numeric IDs, construct short URL
        if post_id and page_id:
            short_url = f"fb.com/{page_id}/posts/{post_id}"
            logger.info(f"Resolved to numeric URL: {short_url} ({len(short_url)} chars)")
            return short_url

        return final_url

    except Exception as e:
        logger.warning(f"Failed to resolve URL: {e}")
        return url

def clean_facebook_url(url: str) -> str:
    """
    NATIVE ULTRA-SHORTENER for GeeLark (Max 100 chars).
    
    TARGET FORMAT: fb.com/[PAGE_ID]/posts/[POST_ID]
    This is the shortest reliable format that includes context.
    """
    if not url:
        return url
        
    try:
        # Standardize input
        if '://' not in url:
            url = 'https://' + url
            
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        
        # Extract Component IDs
        fbid = None
        page_id = None
        
        # Strategy 1: From Query Params (permalink.php?story_fbid=...&id=...)
        if 'story_fbid' in query:
            fbid = query['story_fbid'][0]
        elif 'fbid' in query:
            fbid = query['fbid'][0]
            
        if 'id' in query:
            page_id = query['id'][0]
            
        # Strategy 2: From Path (facebook.com/PAGE_ID/posts/POST_ID)
        if not fbid or not page_id:
            path_parts = parsed.path.strip('/').split('/')
            if 'posts' in path_parts:
                idx = path_parts.index('posts')
                if idx + 1 < len(path_parts):
                    fbid = path_parts[idx + 1]
                if idx - 1 >= 0:
                    page_id = path_parts[idx - 1]

        # CONSTRUCT SHORT URL
        if fbid and page_id:
            # If fbid is a pfbid (base64), try to resolve to numeric ID first
            if fbid.startswith('pfbid'):
                logger.info(f"Attempting to resolve pfbid to numeric ID...")
                resolved = resolve_facebook_redirect(url)
                # Check if resolution gave us numeric IDs
                if resolved != url and 'pfbid' not in resolved:
                    logger.info(f"Successfully resolved to: {resolved}")
                    return resolved

            # Fallback: use pfbid format (99 chars)
            short_url = f"fb.com/{page_id}/posts/{fbid}"
            logger.info(f"Native Shortened: {len(url)} -> {len(short_url)} chars: {short_url}")
            return short_url

        # Fallback for other formats (just strip params)
        clean_url = url
        if len(url) > 100:
            clean_query = {}
            new_query_str = urlencode(clean_query, doseq=True)
            clean_url = urlunparse((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query_str,
                parsed.fragment
            ))
            # Remove protocol if still too long
            if len(clean_url) > 100:
                 clean_url = clean_url.replace('https://', '').replace('http://', '')

        return clean_url
        
    except Exception as e:
        logger.error(f"Failed to clean URL: {e}")
        return url

def is_url_safe_for_geelark(url: str, limit: int = 100) -> bool:
    return len(url) <= limit