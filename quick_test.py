import requests
import json
import sys

BASE_URL = "http://localhost:8000"
LONG_URL = "https://www.facebook.com/permalink.php?story_fbid=pfbid05PW4jAjxm88wTv6QeFGQuStyENytRAak8AKpJXmSNuMdRFFLakVuKvQjGr4c7DDml&id=61574636237654"
COMMENT = "Testing Native Short Link 🚀"

def run_test():
    print("1. Fetching devices...")
    try:
        resp = requests.get(f"{BASE_URL}/geelark/devices", timeout=5)
        devices = resp.json()
    except Exception as e:
        print(f"❌ Failed to get devices: {e}")
        return

    if not devices:
        print("❌ No devices found via API.")
        return

    # Pick the first device (or one that looks online)
    target_device = devices[0]
    print(f"✅ Found device: {target_device['name']} (ID: {target_device['id']})")

    print("\n2. Sending Comment Task...")
    payload = {
        "device_id": target_device['id'],
        "post_url": LONG_URL,
        "comment": COMMENT,
        "wait_for_completion": False 
    }
    
    try:
        # We don't wait for full completion to avoid script hanging, we just want to see it accepted
        resp = requests.post(f"{BASE_URL}/geelark/comment", json=payload, timeout=10)
        result = resp.json()
        
        if resp.status_code == 200:
            print("✅ Task Accepted!")
            print(f"Task ID: {result.get('task_id')}")
            print(f"Status: {result.get('task_status')}")
            print("Check your GeeLark dashboard/phone now.")
        else:
            print(f"❌ Failed: {resp.text}")
            
    except Exception as e:
        print(f"❌ Request failed: {e}")

if __name__ == "__main__":
    run_test()
