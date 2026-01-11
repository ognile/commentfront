import os
import requests
import time

RAILWAY_TOKEN = os.getenv("RAILWAY_TOKEN")
PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID") or os.getenv("PROJECT_ID")
SERVICE_NAME = os.getenv("RAILWAY_SERVICE_NAME", "comment-bot-backend")

def deploy():
    if not RAILWAY_TOKEN:
        print("Missing RAILWAY_TOKEN env var.")
        return
    if not PROJECT_ID:
        print("Missing RAILWAY_PROJECT_ID (or PROJECT_ID) env var.")
        return

    print(f"Deploying to Railway Project: {PROJECT_ID}")
    
    # 1. We assume the project exists. We need to create a Service if it doesn't exist?
    # Actually, standard Railway flow via API usually requires linking a repo or CLI.
    # Without CLI, we can't easily "upload source code" via a simple REST endpoint unless we use the GraphQL API.
    
    # Let's check the project status first using GraphQL
    headers = {"Authorization": f"Bearer {RAILWAY_TOKEN}"}
    query = """
    query {
      project(id: \"%s\") {
        id
        name
        services {
          edges {
            node {
              id
              name
            }
          }
        }
      }
    }
    """ % PROJECT_ID
    
    resp = requests.post("https://backboard.railway.app/graphql/v2", json={"query": query}, headers=headers)
    
    if resp.status_code != 200:
        print(f"Failed to connect to Railway: {resp.text}")
        return

    data = resp.json()
    if "errors" in data:
        print(f"GraphQL Error: {data['errors']}")
        return
        
    project = data["data"]["project"]
    print(f"Connected to Project: {project['name']}")
    
    services = project["services"]["edges"]
    service_id = None
    
    # Find or Create Service
    for edge in services:
        svc = edge["node"]
        if svc["name"] == SERVICE_NAME:
            service_id = svc["id"]
            print(f"Found existing service: {SERVICE_NAME} ({service_id})")
            break
            
    if not service_id:
        print(f"Service '{SERVICE_NAME}' not found. Create it in the UI or link a repo.")
        print("Since we cannot upload local files via raw HTTP easily without the CLI,")
        print("the best path is to push this code to a GitHub repo linked to this Railway project.")
        return

    print("\nSetup Complete.")
    print(f"1. Make sure this backend code is committed to your GitHub repo.")
    print(f"2. Ensure Railway is watching that repo.")
    print(f"3. Add 'PROXY_URL' variable in Railway Dashboard for Service: {SERVICE_NAME}")
    print(f"4. The Dockerfile I created will automatically build the bot.")

if __name__ == "__main__":
    deploy()
