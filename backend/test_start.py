from geelark_client import GeeLarkClient
import logging

logging.basicConfig(level=logging.INFO)
client = GeeLarkClient()

print("Fetching devices...")
devices = client.list_devices()
if devices:
    target = devices[0]
    print(f"Starting device: {target.name} ({target.id})")
    client.start_device(target.id)
else:
    print("No devices found")