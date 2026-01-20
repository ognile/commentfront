---
paths:
  - "backend/workflows.py"
  - "backend/gemini_image_gen.py"
---

# Workflow Rules

Workflows combine multiple capabilities into single API calls.

## Update Profile Photo

Generates AI photo (Gemini 3 Pro Image) + uploads via Adaptive Agent.

### Endpoint
```
POST /workflow/update-profile-photo
Header: X-API-Key
```

### Request Format
```json
{
  "profile_name": "Priscilla Hicks",
  "persona_description": "friendly middle-aged white woman with brown hair"
}
```

### Example
```bash
curl -X POST "https://commentbot-production.up.railway.app/workflow/update-profile-photo" \
  -H "X-API-Key: $CLAUDE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"profile_name": "Priscilla Hicks", "persona_description": "friendly middle-aged white woman"}'
```

### How It Works
1. `gemini_image_gen.py` generates iPhone-style selfie (1:1, candid, realistic)
2. `adaptive_agent.py` navigates to profile → edit → upload
3. File chooser handler auto-selects the image
4. Agent confirms/saves

### Image Generation Details
- Model: `gemini-3-pro-image-preview`
- Prompt: iPhone 15 Pro selfie, natural lighting, no AI tells
- Output: `/tmp/profile_photos/{profile}_{timestamp}.png`

### Persona Description Tips
- Include gender: "woman", "man"
- Include age range: "middle-aged", "young", "older"
- Include ethnicity if needed: "white", "black", "asian", "latina"
- Include style details: "casual", "professional", "friendly smile"

---

## Regenerate Profile Photo (Identity Preservation)

Generates new photo of the **same person** in a different pose/setting using existing profile picture as reference.

### Single Profile Endpoint
```
POST /workflow/regenerate-profile-photo
Header: X-API-Key
```

### Request Format
```json
{
  "profile_name": "adele_hamilton",
  "pose_name": "coffee_shop"  // optional - random if not specified
}
```

### Available Poses
| Pose Name | Description |
|-----------|-------------|
| `beach` | Beach selfie with ocean background |
| `gym_mirror` | Gym mirror selfie in workout clothes |
| `coffee_shop` | Cozy coffee shop holding a latte |
| `car` | Car selfie in driver seat |
| `kitchen` | Kitchen at home, morning light |
| `living_room` | Relaxing on couch at home |
| `outdoor_walk` | Walking in park, sunny day |
| `with_dog` | With dog, happy expression |
| `restaurant` | Nice restaurant, evening |
| `bathroom_mirror` | Bathroom mirror selfie |
| `hiking` | Hiking with nature background |
| `pool` | By the pool, sunny day |

### Example
```bash
curl -X POST "https://commentbot-production.up.railway.app/workflow/regenerate-profile-photo" \
  -H "X-API-Key: $CLAUDE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"profile_name": "adele_hamilton", "pose_name": "beach"}'
```

### How It Works
1. Loads current `profile_picture` (base64) from session file
2. Sends reference image + pose prompt to Gemini 3 Pro Image
3. Gemini generates new image preserving the person's identity
4. Adaptive Agent uploads to Facebook
5. Session file updated with new `profile_picture`

---

## Batch Regenerate All Imported Profiles

Regenerates photos for all profiles tagged with "imported".

### Endpoint
```
POST /workflow/regenerate-all-imported-photos
Header: X-API-Key
```

### Request Format
No body required.

### Example
```bash
curl -X POST "https://commentbot-production.up.railway.app/workflow/regenerate-all-imported-photos" \
  -H "X-API-Key: $CLAUDE_API_KEY"
```

### How It Works
1. Finds all sessions with `"imported"` tag
2. Assigns unique random pose to each profile
3. Processes sequentially (one at a time)
4. Returns summary with success/failure counts

### Response
```json
{
  "total": 20,
  "successful": 19,
  "failed": 1,
  "results": [...]
}
```

### Common Failures
- **Account locked**: Profile needs manual unlock first
- **Gemini empty response**: Pose may have been blocked (e.g., pool/swimwear)
- **Upload timeout**: Facebook slow to respond
