"""Keep creator access closed until a dedicated Mac profile and read are verified."""
import re

PHONE_TYPES = ("browser_grants", "browser_grant_prepare", "browser_grant", "browser_grant_revoke")


class CreatorGrants:
    def __init__(self, host):
        self.host = host

    def clear(self):
        """There are no grants to clear in this release."""

    async def on_phone(self, session, obj):
        action, request = obj.get("t"), obj.get("r")
        if (action not in PHONE_TYPES or not isinstance(request, str) or not re.fullmatch(r"[0-9a-f]{8,64}", request)
                or not getattr(session, "p33", False) or not self.host._paired_live(session)):
            return
        if action == "browser_grants":
            return await self.host._ctl_res(session, request, action, True, available=False, grants=[])
        return await self.host._ctl_res(session, request, action, False, why="site_unavailable")

    async def from_agent(self, req, peer):
        return {"ok": False, "reason": "site_unavailable"}
