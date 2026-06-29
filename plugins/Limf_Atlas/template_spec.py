# -*- coding: utf-8 -*-
"""Beskrivelse af mapbook-skabelonen.

Skabelonen (resources/mapbook_skabelon.qpt) er bygget med faste feltnavne
i sine dynamiske tekster og i atlas-opsætningen. Her samler vi:

  * Hvilke logiske pladsholdere skabelonen indeholder (navn, adresse, ...).
  * Hvilket feltnavn hver pladsholder bruger i skabelonens XML
    (det navn vi skal udskifte med brugerens valgte felt).
  * Hint til auto-matching mod brugerens datafelter.

Hvis skabelonen ændres, er det her opsætningen rettes.
"""


class Placeholder:
    """En logisk plads i skabelonen som skal kobles til et datafelt."""

    def __init__(self, key, label, template_field, match_hints,
                 required=True, generated=False):
        #: Intern nøgle (stabil identifikator).
        self.key = key
        #: Tekst vist til brugeren i mapping-dialogen.
        self.label = label
        #: Det feltnavn skabelonens XML refererer til, fx "EJER_NAVN".
        self.template_field = template_field
        #: Lowercase-substrings der bruges til auto-matching mod datafelter.
        self.match_hints = match_hints
        #: Skal pladsen udfyldes for at atlasset giver mening?
        self.required = required
        #: Kan pladsen genereres af pluginnet hvis den ikke findes i data?
        self.generated = generated


# Rækkefølgen styrer visningen i dialogen.
PLACEHOLDERS = [
    Placeholder(
        key="navn",
        label="Ejernavn",
        template_field="EJER_NAVN",
        match_hints=["ejernavn", "navn", "ejer_navn", "owner", "name"],
        required=True,
    ),
    Placeholder(
        key="adresse",
        label="Adresse",
        template_field="EJER_ADR",
        match_hints=["ejeradress", "adress", "adresse", "ejer_adr", "address"],
        required=True,
    ),
    Placeholder(
        key="postnr",
        label="Postnr. + by",
        template_field="EJER_POSTA",
        match_hints=["postadr", "postnr", "postnummer", "post", "by", "zip", "city"],
        required=False,
        generated=True,  # kan udledes af adressen hvis felt mangler
    ),
    Placeholder(
        key="lobenr",
        label="Løbenummer",
        template_field="Nr",
        match_hints=["nr", "lobenr", "løbenr", "lbnr", "id", "fid"],
        required=True,
        generated=True,  # genereres som 1,2,3,... hvis felt mangler
    ),
]


def placeholder_by_key(key):
    for ph in PLACEHOLDERS:
        if ph.key == key:
            return ph
    return None
