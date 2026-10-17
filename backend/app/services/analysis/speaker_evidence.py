"""Conservative name checks; rejecting a name must not discard speech turns."""

import re


def name_has_direct_evidence(name: str, texts: list[str], context: dict) -> bool:
    if not name or any(char in name for char in "[]\n\r"):
        return False
    aliases = {name}
    for entity in context.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        values = {entity.get("canonical", ""), *(entity.get("aliases") or [])}
        if name in values:
            aliases.update(value for value in values if isinstance(value, str) and value)
    candidates = {
        row.get("name") for row in context.get("speaker_candidates") or []
        if isinstance(row, dict)
    }
    for text in texts:
        for alias in aliases:
            escaped = re.escape(alias)
            # A quoted self-introduction is evidence for the quoted person,
            # not proof that the current voice belongs to that person.
            if re.search(r"[\"“「『]", text):
                continue
            if re.search(
                rf"(?:^|[。！？.!?]\s*|\]\s*)(?:(?:大家好|感谢收听|谢谢大家)[，,]?\s*|hello[,.]?\s*)?"
                rf"(?:我是|我叫|本人是|my name is\s+|i am\s+|i['’]m\s+){escaped}"
                rf"(?=$|[\s，。！？、,.!?；;：:]|很高兴|今天)", text, re.IGNORECASE,
            ):
                return True
            if name in candidates and re.match(rf"^\[{escaped}\]\s*", text):
                return True
    return False
