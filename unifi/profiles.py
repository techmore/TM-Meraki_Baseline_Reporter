import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List

from .env import load_env


@dataclass(frozen=True)
class UniFiSiteProfile:
    key: str
    name: str
    api_key: str
    site_id: str = "default"
    console_id: str = ""
    base_url: str = ""
    verify_ssl: str = "0"

    @property
    def safe_name(self) -> str:
        clean = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in self.name.strip())
        return clean.strip("_") or self.key

    def env_updates(self) -> Dict[str, str]:
        updates = {
            "UNIFI_COLLECTION_MODE": "network",
            "UNIFI_NETWORK_API_KEY": self.api_key,
            "UNIFI_SITE_ID": self.site_id or "default",
            "UNIFI_VERIFY_SSL": self.verify_ssl or "0",
        }
        if self.base_url:
            updates["UNIFI_NETWORK_BASE_URL"] = self.base_url
        if self.console_id:
            updates["UNIFI_NETWORK_CONSOLE_ID"] = self.console_id
        return updates


def discover_site_profiles(*, load_files: bool = True) -> List[UniFiSiteProfile]:
    if load_files:
        load_env()

    indexes = sorted(
        {int(match.group(1)) for key in os.environ for match in [re.match(r"UNIFI_SITE(\d+)_", key)] if match}
    )
    profiles: List[UniFiSiteProfile] = []
    for index in indexes:
        prefix = f"UNIFI_SITE{index}_"
        api_key = os.getenv(f"{prefix}API_KEY", "")
        console_id = os.getenv(f"{prefix}CONSOLE_ID", "")
        base_url = os.getenv(f"{prefix}BASE_URL", "")
        if not api_key or not (console_id or base_url):
            continue
        profiles.append(
            UniFiSiteProfile(
                key=f"site{index}",
                name=os.getenv(f"{prefix}NAME", f"site{index}"),
                api_key=api_key,
                site_id=os.getenv(f"{prefix}SITE_ID", "default"),
                console_id=console_id,
                base_url=base_url,
                verify_ssl=os.getenv(f"{prefix}VERIFY_SSL", os.getenv("UNIFI_VERIFY_SSL", "0")),
            )
        )
    return profiles


def profile_by_key(profiles: Iterable[UniFiSiteProfile], selector: str) -> UniFiSiteProfile | None:
    wanted = selector.strip().lower()
    for profile in profiles:
        if wanted in {profile.key.lower(), profile.name.lower(), profile.safe_name.lower()}:
            return profile
    return None

