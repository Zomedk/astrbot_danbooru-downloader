"""Read the legacy mapping as data while retaining duplicate keys and line numbers."""

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MappingEntry:
    line: int
    aliases: tuple[str, ...]
    query: str


@dataclass(frozen=True)
class QueryToken:
    value: str
    kind: str  # tag, metatag, operator


METATAGS = {
    "rating", "score", "age", "date", "id", "filetype", "filesize",
    "width", "height", "status", "is", "favcount", "random", "order",
    "limit", "tagcount", "mpixels", "parent", "child", "pixiv_id",
}


def read_mapping_entries(path: str | Path) -> list[MappingEntry]:
    source = Path(path).read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "RAW_DICT_GENSHIN_IMPACT"
                           for target in node.targets)]
    if len(assignments) != 1 or not isinstance(assignments[0].value, ast.Dict):
        raise ValueError("mapping.py 缺少预期的字面量字典")
    result = []
    for key, value in zip(assignments[0].value.keys, assignments[0].value.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str) or not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            raise ValueError(f"mapping.py 第 {getattr(key, 'lineno', '?')} 行不是字符串字面量")
        aliases = tuple(alias.strip() for alias in key.value.split("|") if alias.strip())
        result.append(MappingEntry(key.lineno, aliases, value.value.strip()))
    return result


def tokenize_query(query: str) -> tuple[tuple[QueryToken, ...], bool]:
    tokens = []
    complex_query = False
    for raw in query.split():
        if raw.lower() == "or" or raw == "~":
            tokens.append(QueryToken(raw, "operator"))
            complex_query = True
            continue
        if raw.startswith(("~", "-")):
            complex_query = True
        value = raw.lstrip("~-+")
        prefix = value.split(":", 1)[0].lower() if ":" in value else ""
        kind = "metatag" if prefix in METATAGS else "tag"
        tokens.append(QueryToken(value, kind))
    return tuple(tokens), complex_query
