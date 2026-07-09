#!/usr/bin/env python3
"""
IMQ2 Gmail OAuth Setup
Run this once on your-pi to authorize Q2's Gmail account.
It opens a browser for the Google sign-in flow and saves a token file
that Q2 uses for all future email operations without any user interaction.

Usage:
    source ~/.venv/bin/activate
    python credentials/setup_gmail_oauth.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from integrations.gmail_oauth import get_service

print("Starting Gmail OAuth setup for your-agent-email@gmail.com...")
print("A browser window will open — sign in as your-agent-email@gmail.com and grant access.")
print()

try:
    service = get_service()
    # Test it actually works
    profile = service.users().getProfile(userId="me").execute()
    print(f"✓ Authenticated as: {profile.get('emailAddress')}")
    print(f"✓ Token saved to: {Path(__file__).parent / 'gmail_token.json'}")
    print()
    print("Setup complete. Q2 can now send and read email.")
except Exception as e:
    print(f"✗ Setup failed: {e}")
    sys.exit(1)
