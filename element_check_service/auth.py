import hashlib
import hmac
import json
from pathlib import Path
import re
from .webhooks import webhook_url
from .common import ServiceError

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SCOPES = {"checks:read", "checks:write", "settings:read", "settings:write", "delivery:retry"}


class Registry:
    """Reload atomic replacements; keep the last validated snapshot on bad rotation."""
    def __init__(self, path, *, webhook=False):
        self.path, self.webhook = Path(path), webhook
        self.clients, self.signature = None, None
        self.reload()

    def reload(self):
        try:
            stat = self.path.stat()
            signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if signature == self.signature:
                return
            if stat.st_size > 1024 * 1024:
                raise ValueError("Registry too large")
            data = json.loads(self.path.read_text())
            clients, digests = {}, set()
            for client in data["clients"]:
                identity = client["clientId"]
                if not IDENTIFIER.fullmatch(identity) or identity in clients:
                    raise ValueError("Invalid clientId")
                if self.webhook:
                    if 'url' in client:  # Legacy default for requests without webhookUrl.
                        webhook_url(client['url'])
                    if not client["keys"].get(client["currentKeyId"]) or any(len(v) < 32 for v in client["keys"].values()):
                        raise ValueError("Invalid signing keys")
                else:
                    if set(client["allowedRunModes"]) - {"isolated", "integration"}:
                        raise ValueError("Invalid run modes")
                    for token in client["tokens"]:
                        digest = token["tokenSha256"]
                        if not re.fullmatch("[0-9a-f]{64}", digest) or digest in digests or set(token["scopes"]) - SCOPES:
                            raise ValueError("Invalid tokens")
                        if type(token["enabled"]) is not bool or not IDENTIFIER.fullmatch(token["keyId"]):
                            raise ValueError("Invalid key")
                        digests.add(digest)
                clients[identity] = client
            self.clients, self.signature = clients, signature
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            if self.clients is None:
                raise ValueError("No valid client registry") from None

    def authorize(self, header, scope):
        self.reload()
        if not header or not header.startswith("Bearer ") or len(header) > 1024:
            raise ServiceError(401, "unauthorized")
        digest = hashlib.sha256(header[7:].encode()).hexdigest()
        match = None
        for client in self.clients.values():
            for token in client["tokens"]:
                if hmac.compare_digest(digest, token["tokenSha256"]) and token["enabled"]:
                    match = (client, token)
        if match is None:
            raise ServiceError(401, "unauthorized")
        client, token = match
        if scope not in token["scopes"]:
            raise ServiceError(403, "forbidden")
        return client
