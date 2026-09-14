"""The curated lists, validation, and the renderer itself."""
import python_avatars as pa

import appearance as ap


def test_every_offered_id_exists_in_the_installed_library():
    """The lists are built from enum names, so none may name a missing member."""
    enums = {"hair": pa.HairType, "eyes": pa.EyeType, "outfit": pa.ClothingType,
             "face": pa.MouthType, "skin": pa.SkinColor}
    for kind, entries in ap.options().items():
        have = {member.name for member in enums[kind]}
        assert {e["id"] for e in entries} <= have


def test_the_lists_are_the_curated_sizes():
    sizes = {kind: len(entries) for kind, entries in ap.options().items()}
    assert sizes == {"hair": 8, "eyes": 6, "outfit": 8, "face": 6, "skin": 7}


def test_an_outfit_is_never_none():
    assert all(e["id"] != "NONE" for e in ap.options()["outfit"])


def test_hair_offers_a_varied_spread():
    ids = {e["id"] for e in ap.options()["hair"]}
    assert "NONE" in ids                      # bald
    assert len(ids) == 8
    # Textured styles the library carries should be represented.
    assert ids & {"DREADS", "CORNROWS", "FRO"}


def test_headscarf_options_are_offered_when_the_library_has_them():
    """HIJAB/TURBAN are preferred by name; 1.4.1 has neither, later ones may."""
    have = {member.name for member in pa.HairType}
    offered = {e["id"] for e in ap.options()["hair"]}
    assert (have & {"HIJAB", "TURBAN"}) <= offered


def test_default_is_itself_valid():
    assert ap.normalise(ap.DEFAULT) == ap.DEFAULT


def test_normalise_fills_in_missing_keys():
    assert ap.normalise({}) == ap.DEFAULT


def test_normalise_drops_unknown_keys():
    assert "evil" not in ap.normalise({"evil": "yes"})


def test_normalise_rejects_a_name_that_exists_but_is_not_offered():
    """CHEF is a real ClothingType; it is not on the curated list."""
    assert "CHEF" not in {e["id"] for e in ap.options()["outfit"]}
    assert ap.normalise({"outfit": "CHEF"})["outfit"] == ap.DEFAULT["outfit"]


def test_normalise_survives_hostile_input():
    for junk in (None, "string", 42, [], {"hair": None}, {"hair": {"a": 1}},
                 {"skin": "__class__"}, {"face": "../../etc/passwd"}):
        assert ap.normalise(junk) == ap.normalise(ap.normalise(junk))
        assert set(ap.normalise(junk)) == set(ap.KINDS)


def test_random_appearance_is_always_valid():
    for _ in range(50):
        made = ap.random_appearance()
        assert ap.normalise(made) == made


def test_random_appearance_actually_varies():
    seen = {tuple(sorted(ap.random_appearance().items())) for _ in range(30)}
    assert len(seen) > 1


def test_render_svg_returns_svg_markup():
    svg = ap.render_svg(ap.DEFAULT)
    assert svg.lstrip().startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_render_svg_is_deterministic():
    """The render endpoint caches forever, which only holds if this is stable."""
    choice = {"hair": "BOB", "eyes": "HAPPY", "outfit": "HOODIE",
              "face": "SMILE", "skin": "BROWN"}
    assert ap.render_svg(choice) == ap.render_svg(choice)


def test_different_choices_render_differently():
    a = ap.render_svg({**ap.DEFAULT, "skin": "BROWN"})
    b = ap.render_svg({**ap.DEFAULT, "skin": "PALE"})
    assert a != b


def test_render_svg_never_raises_on_bad_input():
    for junk in (None, "x", {"hair": "NOPE"}, {"skin": 7}):
        assert ap.render_svg(junk).lstrip().startswith("<svg")


def test_rendering_reaches_no_network(monkeypatch):
    """The whole point of this library: it must work on a LAN with no uplink."""
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("rendering an avatar tried to open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert ap.render_svg(ap.random_appearance()).lstrip().startswith("<svg")


def test_the_rendered_svg_references_no_external_hosts():
    svg = ap.render_svg(ap.DEFAULT)
    assert "http://" not in svg.replace("http://www.w3.org", "")
    assert "https://" not in svg


# --- the layering the feature must not break -------------------------------

def test_the_engine_never_learns_about_appearance():
    """Appearance is presentation state; the rules must not see it."""
    import pathlib
    for path in pathlib.Path("engine").rglob("*.py"):
        text = path.read_text().lower()
        assert "appearance" not in text, f"{path} mentions appearance"
        assert "avatar" not in text, f"{path} mentions avatars"


def test_storage_does_not_import_the_engine_or_the_avatar_library():
    import ast
    import pathlib
    for path in pathlib.Path("storage").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert not roots & {"engine", "python_avatars", "appearance"}, (
                f"{path} imports {roots}")
