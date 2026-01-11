# Facebook Login - What Actually Works

## Working Click Method

**`dispatch_event('click')` on locators:**

```python
async def click_dispatch(page, selector, timeout=3000):
    loc = page.locator(selector)
    await loc.wait_for(state="visible", timeout=timeout)
    await loc.dispatch_event('click')
```

## What DOESN'T Work on AdsPower CDP
- `locator.click()` - hangs at "performing click action"
- `page.mouse.click(x, y)` - also hangs via CDP
- `element.click()` via JS - doesn't trigger React handlers

## Login Flow

### 1. Fill Credentials
```python
await page.locator('input[name="email"]').fill(fb_id)
await page.locator('input[name="pass"]').fill(fb_password)
```

### 2. Click Login
```python
await page.get_by_text("Log in", exact=True).dispatch_event('click')
```

### 3. Handle 2FA Method Selection
```python
# If "Try another way" appears
await page.get_by_text("Try another way", exact=True).dispatch_event('click')

# Select Authentication app radio
await page.locator('[role="radio"]:has-text("Authentication")').dispatch_event('click')

# Click Continue
await page.locator('[role="button"]:has-text("Continue")').dispatch_event('click')
```

### 4. Enter TOTP Code
```python
code = pyotp.TOTP(totp_secret).now()

# Focus and type
await page.evaluate('document.querySelector("input")?.focus()')
await page.keyboard.type(code, delay=40)

# Submit
await page.locator('[role="button"]:has-text("Continue")').dispatch_event('click')
```

### 5. Handle Save Device
```python
await page.get_by_text("Save", exact=True).dispatch_event('click')
```

### 6. Dismiss Popups
```python
await page.get_by_text("Not now", exact=True).dispatch_event('click')
```

## Key Points
1. **Use `dispatch_event('click')`** - only method that works on AdsPower CDP
2. **Use `keyboard.type()`** for TOTP - proper React input handling
3. **Check page content** to determine current state
4. **Short waits** - 0.3-2s between steps, not more
5. **Verify with feed content** - check for "What's on your mind" text
