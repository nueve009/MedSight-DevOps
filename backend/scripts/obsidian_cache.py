"""
Cache layer implemented as an Obsidian vault: every cached medicine is a
markdown note with YAML frontmatter, sitting in cache_vault/. This means:

  - You (or anyone) can open cache_vault/ directly as an Obsidian vault to
    browse, tag, or manually correct cached descriptions.
  - Repeat lookups of the same generic+brand+dosage skip the paid
    description-agent API call entirely (the whole point per your note #1).

Lookups don't scan every markdown file - index.json maps cache_key -> note
filename, so a hit is a dict lookup + single file read.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app import config
from app.utils import safe_slug

import os

VAULT_DIR = "cache_vault"
os.makedirs(VAULT_DIR, exist_ok=True)

class ObsidianCache:
    def __init__(self, vault_dir: Path = config.CACHE_VAULT_DIR, index_path: Path = config.CACHE_INDEX_PATH):
        self.vault_dir = vault_dir
        self.index_path = index_path
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self._index = self._load_index()

    def _load_index(self) -> dict:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_index(self) -> None:
        self.index_path.write_text(json.dumps(self._index, indent=2), encoding="utf-8")

    def get(self, cache_key: str) -> Optional[dict]:
        """Returns the cached record dict, or None on a miss."""
        filename = self._index.get(cache_key)
        if not filename:
            return None
        note_path = self.vault_dir / filename
        if not note_path.exists():
            # index drifted from disk state; treat as a miss
            del self._index[cache_key]
            self._save_index()
            return None
        return self._parse_note(note_path)

    def set(self, cache_key: str, generic_name: str, brand_name: str, dosage: str,
            description: str, grounded: bool, source_url: Optional[str], confidence: float) -> Path:
        """Writes a new markdown note for this medicine and indexes it."""
        slug = safe_slug(brand_name or generic_name or cache_key)
        filename = f"{slug}-{cache_key}.md"
        note_path = self.vault_dir / filename

        frontmatter = {
            "cache_key": cache_key,
            "generic_name": generic_name,
            "brand_name": brand_name,
            "dosage": dosage,
            "grounded": grounded,
            "source_url": source_url,
            "confidence": confidence,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        note_path.write_text(self._render_note(frontmatter, description), encoding="utf-8")

        self._index[cache_key] = filename
        self._save_index()
        return note_path

    @staticmethod
    def _render_note(frontmatter: dict, description: str) -> str:
        lines = ["---"]
        for k, v in frontmatter.items():
            if isinstance(v, str):
                v = v.replace('"', "'")
                lines.append(f'{k}: "{v}"')
            else:
                lines.append(f"{k}: {json.dumps(v)}")
        lines.append("---")
        lines.append("")
        lines.append(f"# {frontmatter.get('brand_name') or frontmatter.get('generic_name') or 'Unknown medicine'}")
        lines.append("")
        lines.append(description)
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _parse_note(note_path: Path) -> dict:
        text = note_path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        record = {}
        if len(parts) >= 3:
            frontmatter_raw, body = parts[1], parts[2]
            for line in frontmatter_raw.strip().splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key, value = key.strip(), value.strip()
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value in ("true", "false"):
                    value = value == "true"
                elif value == "null":
                    value = None
                else:
                    try:
                        value = float(value) if "." in value else int(value)
                    except ValueError:
                        pass
                record[key] = value
            record["description"] = body.strip().split("\n\n", 1)[-1].strip() if body.strip() else ""
        return record
