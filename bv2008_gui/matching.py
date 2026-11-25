"""Candidate matching shared by GUI and CLI. Ambiguity requires a choice."""
from __future__ import annotations

from typing import Any

from .excel import ImportRow


def roster_name_masks(name: str) -> set[str]:
    clean = "".join(str(name).split())
    if not clean:
        return set()
    # Retain the older, compact forms alongside the site's current masks.
    masks = {"*" + clean[1:]}
    if len(clean) > 2:
        masks.add("*" + clean[-2:])
    if len(clean) == 4:
        masks.add("**" + clean[-2:])
    elif len(clean) > 4:
        masks.add(clean[0] + "*" * (len(clean) - 3) + clean[-2:])
    return masks


def roster_candidates(row: ImportRow, roster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    masks = roster_name_masks(row.name)
    matches: dict[str, dict[str, Any]] = {}
    for user in roster:
        uid = str(user.get("uid") or "").strip()
        if uid and str(user.get("nameSensitive") or "") in masks:
            matches.setdefault(uid, user)
    return list(matches.values())


def match_roster_user(row: ImportRow, roster: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    matches = roster_candidates(row, roster)
    if len(matches) == 1:
        return matches[0], "matched"
    if matches:
        return None, f"multiple roster matches: {len(matches)}"
    return None, "not found in roster"


def collect_candidates(row: ImportRow, users: list[dict[str, Any]], roster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Include both sources, deduplicate accounts, and retain membership status."""
    roster_uids = {str(user.get("uid")) for user in roster if user.get("uid")}
    matches: dict[str, dict[str, Any]] = {}
    for user in [*users, *roster_candidates(row, roster)]:
        uid = str(user.get("uid") or "").strip()
        if uid:
            matches.setdefault(uid, {**user, "uid": uid, "_in_roster": uid in roster_uids})
    return list(matches.values())


def candidate_summary(user: dict[str, Any]) -> dict[str, Any]:
    return {"uid": str(user["uid"]), "nameSensitive": str(user.get("nameSensitive") or ""),
            "userNumber": str(user.get("userNumber") or ""), "alreadyInPost": bool(user.get("_in_roster"))}
