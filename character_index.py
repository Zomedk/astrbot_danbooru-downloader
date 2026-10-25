"""Ambiguity-aware index layered over the unchanged legacy mapping."""

import difflib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .mapping_parser import MappingEntry, read_mapping_entries, tokenize_query
else:  # Direct local tests
    from mapping_parser import MappingEntry, read_mapping_entries, tokenize_query


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).strip().casefold().split())


@dataclass(frozen=True)
class CharacterRecord:
    id: str
    display_name: str
    franchise_id: str
    franchise_name: str
    base_tag: str
    aliases: tuple[str, ...]
    tag_status: str = "unchecked"
    post_count: int | None = None


@dataclass(frozen=True)
class VariantRecord:
    id: str
    parent_character_id: str
    display_name: str
    tag: str
    aliases: tuple[str, ...]
    relation_verified: bool
    relation_source: str = ""
    manual_approved: bool = False


@dataclass(frozen=True)
class Resolved:
    character: CharacterRecord
    alternatives: tuple[CharacterRecord, ...] = ()
    defaulted: bool = False


@dataclass(frozen=True)
class Ambiguous:
    candidates: tuple[CharacterRecord, ...]


@dataclass(frozen=True)
class NotFound:
    suggestions: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class LegacyQuery:
    alias: str
    query: str


class CharacterIndex:
    def __init__(self, entries: list[MappingEntry], overrides: dict):
        self.entries = entries
        self.overrides = overrides
        self.franchises = overrides.get("franchises", {})
        self.characters: dict[str, CharacterRecord] = {}
        self.variants: dict[str, VariantRecord] = {}
        self.alias_to_candidates: dict[str, list[str]] = {}
        self.variant_alias_to_ids: dict[str, list[str]] = {}
        self.legacy_alias_to_query: dict[str, str] = {}
        self.alias_display: dict[str, str] = {}
        self.alias_defaults = {normalize(k): v for k, v in overrides.get("alias_defaults", {}).items()}
        self.alias_rejections = {normalize(k): set(v) for k, v in overrides.get("alias_rejections", {}).items()}
        self.do_not_auto_resolve = {normalize(x) for x in overrides.get("do_not_auto_resolve", [])}
        self._build()

    @classmethod
    def from_files(cls, mapping_path: str | Path, overrides_path: str | Path):
        entries = read_mapping_entries(mapping_path)
        overrides = json.loads(Path(overrides_path).read_text(encoding="utf-8"))
        return cls(entries, overrides)

    def _franchise_for_tag(self, tag: str) -> tuple[str, str] | None:
        for franchise_id, details in self.franchises.items():
            suffix = f"_({details['tag_suffix']})"
            if tag.endswith(suffix):
                return franchise_id, tag[:-len(suffix)]
        return None

    def _add_alias(self, alias: str, character_id: str) -> None:
        key = normalize(alias)
        if not key:
            return
        if character_id in self.alias_rejections.get(key, ()):
            return
        self.alias_display.setdefault(key, alias)
        bucket = self.alias_to_candidates.setdefault(key, [])
        if character_id not in bucket:
            bucket.append(character_id)

    def _build(self) -> None:
        curated_by_tag = {}
        for character_id, item in self.overrides.get("characters", {}).items():
            franchise_id = item["franchise"]
            record = CharacterRecord(character_id, item["display_name"], franchise_id,
                                     self.franchises[franchise_id]["display_name"], item["base_tag"],
                                     tuple(item.get("aliases", [])), item.get("tag_status", "unchecked"),
                                     item.get("post_count"))
            self.characters[character_id] = record
            curated_by_tag[record.base_tag] = character_id
        variant_by_tag = {}
        for variant_id, item in self.overrides.get("variants", {}).items():
            variant = VariantRecord(variant_id, item["parent_character_id"], item["display_name"],
                                    item["tag"], tuple(item.get("aliases", [])),
                                    bool(item.get("relation_verified")), item.get("relation_source", ""),
                                    bool(item.get("manual_approved", False)))
            if variant.parent_character_id not in self.characters:
                raise ValueError(f"未知变体本体: {variant_id}")
            self.variants[variant_id] = variant
            variant_by_tag[variant.tag] = variant_id
            if variant.relation_verified or variant.manual_approved:
                for alias in (variant.display_name, *variant.aliases):
                    key = normalize(alias)
                    self.variant_alias_to_ids.setdefault(key, []).append(variant_id)

        simple_tags = set()
        for entry in self.entries:
            tokens, complex_query = tokenize_query(entry.query)
            if not complex_query and len(tokens) == 1 and tokens[0].kind == "tag":
                simple_tags.add(tokens[0].value)
        inferred_variants = set()
        for tag in simple_tags:
            match = self._franchise_for_tag(tag)
            if not match:
                continue
            franchise_id, stem = match
            if stem.endswith(")") and "_(" in stem:
                base_stem = stem[:stem.rfind("_(")]
                suffix = self.franchises[franchise_id]["tag_suffix"]
                if f"{base_stem}_({suffix})" in simple_tags:
                    inferred_variants.add(tag)

        aliases_by_id: dict[str, list[str]] = {key: [] for key in self.characters}
        for entry in self.entries:
            tokens, complex_query = tokenize_query(entry.query)
            for alias in entry.aliases:
                key = normalize(alias)
                self.legacy_alias_to_query.setdefault(key, entry.query)
                self.alias_display.setdefault(key, alias)
            if complex_query or len(tokens) != 1 or tokens[0].kind != "tag":
                continue
            tag = tokens[0].value
            if tag in variant_by_tag or tag in inferred_variants:
                continue
            match = self._franchise_for_tag(tag)
            if not match:
                continue
            franchise_id, stem = match
            character_id = curated_by_tag.get(tag) or f"{franchise_id}:{stem}"
            if character_id not in self.characters:
                display = entry.aliases[0] if entry.aliases else stem
                self.characters[character_id] = CharacterRecord(
                    character_id, display, franchise_id,
                    self.franchises[franchise_id]["display_name"], tag, (),
                )
                aliases_by_id[character_id] = []
            for alias in entry.aliases:
                self._add_alias(alias, character_id)
                if alias not in aliases_by_id[character_id]:
                    aliases_by_id[character_id].append(alias)

        for character_id, record in list(self.characters.items()):
            aliases = list(aliases_by_id.get(character_id, []))
            for alias in (record.display_name, *record.aliases):
                self._add_alias(alias, character_id)
                if alias not in aliases:
                    aliases.append(alias)
            self.characters[character_id] = CharacterRecord(
                record.id, record.display_name, record.franchise_id, record.franchise_name,
                record.base_tag, tuple(aliases), record.tag_status, record.post_count,
            )

    def resolve_character(self, text: str, *, allow_legacy: bool = False):
        raw = normalize(text)
        qualifier = None
        name = raw
        if ":" in raw:
            candidate, remainder = raw.split(":", 1)
            for franchise_id, details in self.franchises.items():
                if candidate in {normalize(franchise_id), normalize(details["display_name"])}:
                    qualifier, name = franchise_id, remainder
                    break
        if name in self.do_not_auto_resolve and qualifier is None:
            return NotFound(reason="这是泛称或主题词，请使用明确人物名")
        ids = self.alias_to_candidates.get(name, ())
        if not ids and " " in name:
            compact = name.replace(" ", "")
            if compact in self.alias_to_candidates:
                name = compact
                ids = self.alias_to_candidates[name]
        records = tuple(self.characters[item] for item in ids
                        if qualifier is None or self.characters[item].franchise_id == qualifier)
        if not records:
            if allow_legacy and qualifier is None and name in self.legacy_alias_to_query:
                return LegacyQuery(self.alias_display[name], self.legacy_alias_to_query[name])
            suggestions = difflib.get_close_matches(name, self.alias_to_candidates.keys(), n=5, cutoff=0.55)
            return NotFound(tuple(self.alias_display[item] for item in suggestions))
        if len(records) == 1:
            return Resolved(records[0])
        preferred = self.alias_defaults.get(name)
        if preferred in {item.id for item in records}:
            chosen = self.characters[preferred]
            return Resolved(chosen, tuple(item for item in records if item.id != preferred), True)
        return Ambiguous(records)

    def resolve_variant(self, character_id: str, text: str):
        key = normalize(text)
        candidates = [self.variants[item] for item in self.variant_alias_to_ids.get(key, ())
                      if self.variants[item].parent_character_id == character_id]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def list_variants(self, character_id: str) -> list[VariantRecord]:
        return [item for item in self.variants.values()
                if item.parent_character_id == character_id and (item.relation_verified or item.manual_approved)]

    def eligible_characters(self, franchise: str) -> list[CharacterRecord]:
        key = normalize(franchise)
        franchise_id = next((item for item, details in self.franchises.items()
                             if key in {normalize(item), normalize(details["display_name"])}), None)
        if not franchise_id:
            return []
        return [item for item in self.characters.values()
                if item.franchise_id == franchise_id and item.tag_status == "active_exact"
                and (item.post_count or 0) > 0]
