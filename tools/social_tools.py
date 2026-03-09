"""
Meta Social Tools — Facebook page + Instagram post publishing.
Uses Meta Graph API v21.0 with system user token.

Page   : FACEBOOK_PAGE_ID (969852546211527)
IG     : INSTAGRAM_BUSINESS_ID (17841480650116183)
Token  : FACEBOOK_PAGE_ACCESS_TOKEN (system user, full scope)
"""
from typing import Optional

import httpx
import structlog

from config.settings import settings

log = structlog.get_logger()

GRAPH_BASE = "https://graph.facebook.com/v21.0"


class MetaSocialTools:
    """
    Publish content to Antigravity's Facebook page and Instagram.
    Raises ValueError if credentials are not configured.
    """

    def __init__(self):
        self.token = settings.facebook_page_access_token
        self.page_id = settings.facebook_page_id
        self.ig_id = settings.instagram_business_id

    def _require_config(self) -> None:
        if not self.token or not self.page_id:
            raise ValueError(
                "FACEBOOK_PAGE_ACCESS_TOKEN and FACEBOOK_PAGE_ID must be set in .env"
            )

    def post_to_facebook(self, message: str, link: Optional[str] = None) -> dict:
        """Publish a text post to the Facebook page."""
        self._require_config()
        payload = {"message": message, "access_token": self.token}
        if link:
            payload["link"] = link
        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{GRAPH_BASE}/{self.page_id}/feed", json=payload)
            resp.raise_for_status()
            result = resp.json()
            log.info("facebook_post_published", post_id=result.get("id"))
            return result

    def post_to_instagram(self, image_url: str, caption: str) -> dict:
        """
        Publish an image to Instagram.
        image_url must be a publicly accessible HTTPS URL.
        """
        self._require_config()
        if not self.ig_id:
            raise ValueError("INSTAGRAM_BUSINESS_ID must be set in .env")
        with httpx.Client(timeout=30) as client:
            # Step 1: Create media container
            container = client.post(
                f"{GRAPH_BASE}/{self.ig_id}/media",
                json={
                    "image_url": image_url,
                    "caption": caption,
                    "access_token": self.token,
                },
            )
            container.raise_for_status()
            container_id = container.json()["id"]
            # Step 2: Publish
            publish = client.post(
                f"{GRAPH_BASE}/{self.ig_id}/media_publish",
                json={"creation_id": container_id, "access_token": self.token},
            )
            publish.raise_for_status()
            result = publish.json()
            log.info("instagram_post_published", media_id=result.get("id"))
            return result

    def broadcast_milestone(self, message: str, hashtags: str = "#AntigravityOrchestra #JaiOS6") -> dict:
        """Post a milestone update to the Facebook page."""
        return self.post_to_facebook(f"🚀 {message}\n\n{hashtags}")

    def get_page_insights(self, metric: str = "page_impressions", period: str = "day") -> dict:
        """Fetch basic page analytics."""
        self._require_config()
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{GRAPH_BASE}/{self.page_id}/insights",
                params={"metric": metric, "period": period, "access_token": self.token},
            )
            resp.raise_for_status()
            return resp.json()
