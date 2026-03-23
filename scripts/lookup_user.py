"""
Helper script to look up Twitter user IDs for accounts.
Useful if the hardcoded IDs need to be updated.
"""

import requests
import os
import sys


def get_user_id(username: str, bearer_token: str) -> str:
    """
    Look up a Twitter user's ID from their username.
    
    Args:
        username: Twitter handle (without @)
        bearer_token: Twitter API Bearer Token
    
    Returns:
        User ID string
    """
    url = "https://api.twitter.com/2/users/by"
    params = {"usernames": username}
    headers = {"Authorization": f"Bearer {bearer_token}"}
    
    response = requests.get(url, params=params, headers=headers)
    response.raise_for_status()
    
    data = response.json()
    if "data" in data and len(data["data"]) > 0:
        return data["data"][0]["id"]
    else:
        raise ValueError(f"User @{username} not found")


def main():
    if len(sys.argv) < 2:
        # Show current known IDs
        print("Known account IDs:")
        print("  potus:         822215673726779392")
        print("  whitehouse:    786317602383623360")
        print("  realtimepotus: 1130475034")
        print("  vp:            897698993295314945")
        print("\nUsage: python lookup_user.py <username>")
        print("Example: python lookup_user.py potus")
        return
    
    username = sys.argv[1].replace("@", "")
    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
    
    if not bearer_token:
        print("Error: TWITTER_BEARER_TOKEN not set")
        print("Set it in your .env file or environment")
        sys.exit(1)
    
    try:
        user_id = get_user_id(username, bearer_token)
        print(f"@{username} -> {user_id}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()