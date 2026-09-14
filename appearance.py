"""Avatar appearance: curated option lists, validation, and offline SVG rendering.

Appearance is *presentation* state, not game state, so this module lives outside
engine/ and engine/ never learns about it. Storage keeps it as an opaque JSON
blob; everything that interprets it lives here.

Every option list is built from the python_avatars enums *by name* at import
time, so a name the installed library does not carry is skipped rather than
blowing up at render time. Nothing here touches the network: python_avatars
ships its SVG parts as package data and renders locally.
"""
from __future__ import annotations

import random

import python_avatars as pa

# --- curated option lists --------------------------------------------------
# The library offers 51 hair options; a picker wants roughly eight. These are
# preference orders -- names absent from the installed library drop out quietly
# and the list is topped up from whatever else the enum carries, so a list is
# never short and never names a member that does not exist.

_HAIR_WANTED = [
    "NONE", "SHORT_CURLY", "BOB", "DREADS", "HIJAB", "TURBAN", "FRO",
    "CORNROWS", "LONG_NOT_TOO_LONG", "BUN", "BRAIDS", "PIXIE", "SHORT_FLAT",
    "CURLY", "HAT",
]
_EYES_WANTED = ["DEFAULT", "HAPPY", "SQUINT", "WINK", "SURPRISED", "SIDE",
                "CLOSED", "EYE_ROLL"]
_OUTFIT_WANTED = ["BLAZER_SHIRT", "BLAZER_SWEATER", "HOODIE", "OVERALL",
                  "SHIRT_CREW_NECK", "COLLAR_SWEATER", "GRAPHIC_SHIRT",
                  "SHIRT_V_NECK", "SHIRT_SCOOP_NECK"]
_FACE_WANTED = ["SMILE", "DEFAULT", "SERIOUS", "TWINKLE", "BIG_SMILE",
                "CONCERNED", "DISBELIEF"]
_SKIN_WANTED = ["LIGHT", "PALE", "TANNED", "BROWN", "DARK_BROWN", "BLACK",
                "YELLOW"]

# A hair option of NONE means bald, not "unset"; an outfit of NONE would be
# nudity, so ClothingType.NONE is deliberately never offered.
_LABEL_OVERRIDES = {("hair", "NONE"): "Bald"}


def _pick(enum, wanted: list, count: int) -> list:
    """Names from `wanted` that the installed enum actually has, topped up."""
    have = {member.name for member in enum}
    chosen = [name for name in wanted if name in have][:count]
    if len(chosen) < count:
        for member in enum:
            if member.name not in chosen and member.name != "NONE":
                chosen.append(member.name)
            if len(chosen) == count:
                break
    return chosen


def _label(kind: str, name: str) -> str:
    """SHORT_CURLY -> 'Short curly'."""
    override = _LABEL_OVERRIDES.get((kind, name))
    if override:
        return override
    return name.replace("_", " ").capitalize()


_ENUMS = {
    "hair": pa.HairType,
    "eyes": pa.EyeType,
    "outfit": pa.ClothingType,
    "face": pa.MouthType,
    "skin": pa.SkinColor,
}

KINDS = tuple(_ENUMS)

_IDS = {
    "hair": _pick(pa.HairType, _HAIR_WANTED, 8),
    "eyes": _pick(pa.EyeType, _EYES_WANTED, 6),
    "outfit": _pick(pa.ClothingType, _OUTFIT_WANTED, 8),
    "face": _pick(pa.MouthType, _FACE_WANTED, 6),
    "skin": _pick(pa.SkinColor, _SKIN_WANTED, 7),
}

# id -> enum member, resolved once. Lookups go through this dict, so a request
# parameter is never fed to getattr() on the library.
_MEMBERS = {kind: {name: _ENUMS[kind][name] for name in names}
            for kind, names in _IDS.items()}

DEFAULT = {kind: names[0] for kind, names in _IDS.items()}


def options() -> dict:
    """The picker payload: {kind: [{"id", "label"}, ...]}."""
    return {kind: [{"id": name, "label": _label(kind, name)} for name in names]
            for kind, names in _IDS.items()}


def valid(kind: str, value) -> bool:
    return isinstance(value, str) and value in _MEMBERS.get(kind, {})


def normalise(appearance) -> dict:
    """Coerce anything at all into a complete, valid appearance.

    Unknown keys are dropped and unknown values fall back to the default, so no
    request body -- however hostile -- can reach the renderer as-is.
    """
    if not isinstance(appearance, dict):
        appearance = {}
    return {kind: appearance.get(kind) if valid(kind, appearance.get(kind))
            else DEFAULT[kind] for kind in KINDS}


def random_appearance(rng=None) -> dict:
    """A random-but-valid appearance, for a player who did not choose one."""
    rng = rng or random
    return {kind: rng.choice(names) for kind, names in _IDS.items()}


def render_svg(appearance) -> str:
    """Render to an SVG string. Offline: the library carries its own parts."""
    chosen = normalise(appearance)
    avatar = pa.Avatar(
        style=pa.AvatarStyle.CIRCLE,
        top=_MEMBERS["hair"][chosen["hair"]],
        eyes=_MEMBERS["eyes"][chosen["eyes"]],
        clothing=_MEMBERS["outfit"][chosen["outfit"]],
        mouth=_MEMBERS["face"][chosen["face"]],
        skin_color=_MEMBERS["skin"][chosen["skin"]],
    )
    return avatar.render()
