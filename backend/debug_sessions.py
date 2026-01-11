from fb_session import list_saved_sessions
import logging
import os

# Setup logging to see errors
logging.basicConfig(level=logging.DEBUG)

print(f"Current working directory: {os.getcwd()}")
print("Listing saved sessions...")

try:
    sessions = list_saved_sessions()
    print(f"Found {len(sessions)} sessions:")
    for s in sessions:
        print(f" - {s}")
except Exception as e:
    print(f"ERROR: {e}")
