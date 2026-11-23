from dataclasses import dataclass, field
import os
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    data: Path = field(default_factory=lambda: Path(os.environ.get("SERVICE_DATA_ROOT", "/srv/element-autocheck/data")))
    hard_limit: int = field(default_factory=lambda: int(os.environ.get("MAX_CONCURRENT_CHECKS_HARD_LIMIT", "8")))
    default_cap: int = field(default_factory=lambda: int(os.environ.get("DEFAULT_MAX_CONCURRENT_CHECKS", "2")))
    values: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.values:
            path = Path(os.environ.get("SERVICE_CONFIG", ROOT / "deploy/autocheck/service.yaml"))
            self.values = yaml.safe_load(path.read_text())
        if not 1 <= self.default_cap <= self.hard_limit:
            raise ValueError("Invalid concurrency configuration")
        if not self.data.is_absolute() or self.data.is_symlink():
            raise ValueError("SERVICE_DATA_ROOT must be an absolute directory, not a symlink")

    def get(self, section, key):
        return self.values[section][key]

    def prepare(self):
        self.data.mkdir(parents=True, exist_ok=True, mode=0o2770)
        for name in ("staging", "checks", "work"):
            path = self.data / name
            if path.is_symlink():
                raise ValueError("Unsafe data directory")
            path.mkdir(exist_ok=True, mode=0o2770)
        if len({(self.data / name).stat().st_dev for name in ("staging", "checks", "work")}) != 1:
            raise ValueError("Data directories must share a filesystem")


def secret(name):
    value = Path(os.environ[name + "_FILE"]).read_text().strip()
    if not value or any(c in value for c in "\r\n\x00"):
        raise ValueError("Invalid secret file")
    return value
