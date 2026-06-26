#!/usr/bin/env python3
"""
ugig_client.py — API client wrapper for ugig.net
================================================
Handles auth, onboarding, fetching gigs, applying (claiming), and sending deliverables.
"""

import os
import sys
import logging
import requests
from pathlib import Path

# Add directory to sys.path for importing job_scoring if needed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from job_scoring import Job

log = logging.getLogger("ugig_client")
UGIG_BASE_URL = "https://ugig.net"

class UgigClient:
    """Client for programmatically interacting with ugig.net."""

    def __init__(self, api_key: str | None = None, bearer_token: str | None = None):
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        elif self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def signup(self, email: str, password: str, username: str, agent_name: str, description: str) -> bool:
        """Register a new agent account on ugig.net."""
        url = f"{UGIG_BASE_URL}/api/auth/signup"
        payload = {
            "email": email,
            "password": password,
            "username": username,
            "account_type": "agent",
            "agent_name": agent_name,
            "agent_description": description,
            "agent_version": "1.0.0",
            "agent_operator_url": "https://github.com/BCR-AgentOn",
            "agent_source_url": "https://github.com/BCR-AgentOn/AgentOn"
        }
        try:
            r = self.session.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                log.info(f"Successfully signed up agent account for {username} ({email})")
                return True
            else:
                log.warning(f"Signup failed: {r.status_code} - {r.text}")
                return False
        except Exception as e:
            log.error(f"Signup request error: {e}")
            return False

    def login(self, email: str, password: str) -> bool:
        """Log in and retrieve Bearer JWT session token."""
        url = f"{UGIG_BASE_URL}/api/auth/login"
        payload = {"email": email, "password": password}
        try:
            r = self.session.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                data = r.json()
                self.bearer_token = data.get("access_token")
                log.info("Successfully logged in to ugig.net")
                return True
            else:
                log.warning(f"Login failed: {r.status_code} - {r.text}")
                return False
        except Exception as e:
            log.error(f"Login request error: {e}")
            return False

    def list_jobs(self, limit: int = 50) -> list[Job]:
        """Fetch active gigs and return them as Job dataclass instances."""
        url = f"{UGIG_BASE_URL}/api/gigs"
        params = {"limit": limit, "sort": "newest"}
        
        try:
            r = self.session.get(url, headers=self._headers(), params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            
            # Gigs can be a list or wrapped in an object {"gigs": [...]}
            gigs_list = data.get("gigs", data) if isinstance(data, dict) else data
            if not isinstance(gigs_list, list):
                gigs_list = []
                
            jobs = []
            for g in gigs_list:
                jid = g.get("id")
                title = g.get("title", "Untitled Gig")
                desc = g.get("description", "")
                cat = g.get("category", "other")
                budget_max = float(g.get("budget_max") or g.get("budget_min") or 0.0)
                
                job = Job(
                    id=str(jid),
                    platform="ugig",
                    title=title,
                    description=desc,
                    reward_usd=budget_max,
                    raw=g
                )
                jobs.append(job)
                
            return jobs
        except Exception as e:
            log.error(f"Failed to list jobs from ugig.net: {e}")
            return []

    def claim_job(self, job_id: str, cover_letter: str, proposed_rate: float | None = None) -> str | None:
        """
        Apply to a gig on ugig.net (the agent equivalent of claiming it).
        Includes the completed deliverable in the cover_letter for instant-delivery.
        Returns the application ID on success, or None on failure.
        """
        url = f"{UGIG_BASE_URL}/api/applications"
        payload = {
            "gig_id": job_id,
            "cover_letter": cover_letter,
            "portfolio_items": [],
            "ai_tools_to_use": ["google/gemini-2.5-flash", "python-scripts"]
        }
        if proposed_rate is not None:
            payload["proposed_rate"] = proposed_rate
            
        try:
            r = self.session.post(url, json=payload, headers=self._headers(), timeout=20)
            if r.status_code in (200, 201):
                data = r.json()
                app_id = data.get("application", {}).get("id") or data.get("id")
                log.info(f"Successfully applied to gig {job_id}. Application ID: {app_id}")
                return str(app_id)
            else:
                log.warning(f"Application failed: {r.status_code} - {r.text}")
                return None
        except Exception as e:
            log.error(f"Application request error for gig {job_id}: {e}")
            return None

    def submit_result(self, job_id: str, deliverable: str) -> bool:
        """
        Submit a deliverable for an accepted gig.
        Finds the messaging conversation associated with the gig and sends a message containing the work.
        """
        # Step 1: List conversations
        conv_url = f"{UGIG_BASE_URL}/api/conversations"
        try:
            r = self.session.get(conv_url, headers=self._headers(), timeout=20)
            r.raise_for_status()
            convs = r.json()
            if not isinstance(convs, list):
                convs = convs.get("conversations", convs) if isinstance(convs, dict) else []
            
            # Step 2: Find the conversation corresponding to this gig
            conv_id = None
            for c in convs:
                if str(c.get("gig_id")) == str(job_id):
                    conv_id = c.get("id")
                    break
            
            # If no conversation is active yet, try creating one
            if not conv_id:
                log.info(f"No active conversation found for gig {job_id} — attempting to open one...")
                r_create = self.session.post(conv_url, json={"gig_id": job_id}, headers=self._headers(), timeout=20)
                if r_create.status_code in (200, 201):
                    conv_id = r_create.json().get("id")
                    
            if not conv_id:
                log.warning(f"Could not locate or open conversation for gig {job_id}")
                return False
                
            # Step 3: Send message containing the deliverable
            msg_url = f"{UGIG_BASE_URL}/api/conversations/{conv_id}/messages"
            payload = {"content": f"✅ BCR-AgentOn completed deliverable submission:\n\n{deliverable}"}
            r_msg = self.session.post(msg_url, json=payload, headers=self._headers(), timeout=20)
            if r_msg.status_code in (200, 201):
                log.info(f"Successfully submitted deliverable message to conversation {conv_id}")
                return True
            else:
                log.warning(f"Failed to send deliverable message: {r_msg.status_code} - {r_msg.text}")
                return False
                
        except Exception as e:
            log.error(f"Error submitting result to conversation for gig {job_id}: {e}")
            return False
