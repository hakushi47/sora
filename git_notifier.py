import os
import requests
import sys
from dotenv import load_dotenv
import subprocess

def get_latest_commit_info():
    """Gets the latest commit author and message."""
    print("Fetching latest commit info...")
    try:
        author = subprocess.check_output(['git', 'log', '-1', '--pretty=%an']).decode('utf-8').strip()
        message = subprocess.check_output(['git', 'log', '-1', '--pretty=%B']).decode('utf-8').strip()
        print(f"Author: {author}")
        print(f"Message: {message}")
        return author, message
    except Exception as e:
        print(f"Error getting commit info: {e}")
        return "Unknown", f"Error: {e}"

def send_discord_notification(webhook_url, author, message):
    """Sends a notification to the Discord webhook."""
    data = {
        "embeds": [
            {
                "title": "🚀 New Commit to Sora",
                "description": f"**{message}**",
                "color": 5814783, # A nice blue color
                "fields": [
                    {
                        "name": "Author",
                        "value": author,
                        "inline": True
                    }
                ]
            }
        ]
    }
    response = requests.post(webhook_url, json=data)
    if response.status_code not in [200, 204]:
        print(f"Error sending Discord notification: {response.status_code}, {response.text}")

if __name__ == "__main__":
    load_dotenv()
    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        print("DISCORD_WEBHOOK_URL not found in .env file.")
        sys.exit(1)
    
    commit_author, commit_message = get_latest_commit_info()
    send_discord_notification(url, commit_author, commit_message)
    print("Git commit notification sent to Discord.")
