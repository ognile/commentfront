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
