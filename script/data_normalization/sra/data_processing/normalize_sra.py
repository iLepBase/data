#!/usr/bin/env python3
"""
Normalize Tissue, Sex, and Stage columns in an SRA transcriptome metadata CSV.

This script is self-contained: all normalization rules are embedded in code and
it does not depend on external correction dictionaries or txt vocab files.

Output columns:
- Tissue1.Corr
- Tissue2.Corr
- Sex.Corr
- Stage1.Corr
- Stage2.Corr

Default paths are relative to the repository root:
- input: `data/clean/SRA/transcriptome_data_20260323.csv`
- review-dir: `data/clean/SRA`
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path

DEFAULT_INPUT_PATH = Path("data/clean/SRA/transcriptome_data_20260323.csv")
DEFAULT_REVIEW_DIR = Path("data/clean/SRA")


GENERAL_MISSING_VALUES = {
    "",
    "n/a",
    "na",
    "nan",
    "none",
    "null",
    "missing",
    "not applicable",
    "not collected",
    "not provided",
    "not available",
    "unknown",
}

TISSUE_UNKNOWN = "unknown"
TISSUE_CANONICAL = (
    "midgut",
    "antenna",
    "head",
    "silk gland",
    "leg",
    "wing",
    "fat body",
    "brain",
    "mouthpart",
    "epidermis",
    "ovary",
    "abdomen",
    "thorax",
    "testis",
    "reproductive system",
    "reproductive tract",
    "ejaculatory duct",
    "oviduct",
    "gut",
    "hemolymph",
    "malpighian tubule",
    "pheromone gland",
    "accessory gland",
    "labial gland",
    "salivary gland",
    "muscle",
    "eye",
    "foot",
    "nervous system",
    "serosa",
    "foregut",
    "hindgut",
    "horn",
    "prothoracic gland",
    "trachea",
    "heart",
    "whole body",
    "carcass",
    "unknown",
)
TISSUE_CANONICAL_LOOKUP = {value: value for value in TISSUE_CANONICAL}
TISSUE_CANONICAL_ORDER = {value: index for index, value in enumerate(TISSUE_CANONICAL)}

TISSUE_LEVEL1_MAP = {
    "midgut": "digestive",
    "foregut": "digestive",
    "hindgut": "digestive",
    "gut": "digestive",
    "salivary gland": "digestive",
    "labial gland": "digestive",
    "silk gland": "digestive",
    "head": "head_neck",
    "mouthpart": "head_neck",
    "thorax": "thorax",
    "leg": "thorax",
    "wing": "thorax",
    "foot": "thorax",
    "abdomen": "abdomen",
    "ovipositor": "abdomen",
    "epidermis": "body_wall",
    "horn": "body_wall",
    "hemolymph": "circulatory",
    "heart": "circulatory",
    "fat body": "circulatory",
    "malpighian tubule": "excretory",
    "trachea": "respiratory",
    "muscle": "musculoskeletal",
    "brain": "nervous",
    "nervous system": "nervous",
    "antenna": "sensory",
    "eye": "sensory",
    "ovary": "reproductive",
    "testis": "reproductive",
    "reproductive system": "reproductive",
    "reproductive tract": "reproductive",
    "ejaculatory duct": "reproductive",
    "oviduct": "reproductive",
    "accessory gland": "reproductive",
    "pheromone gland": "endocrine",
    "prothoracic gland": "endocrine",
    "whole body": "whole_body",
    "carcass": "other",
    "serosa": "other",
    "unknown": "unknown",
}

TISSUE_CELL_LINE_MARKERS = (
    "cell line",
    "cell-line",
    "cell_line",
    "cultured cell",
    "cultured cells",
    "cell culture",
    "cells",
    "bmn",
    "bme",
)

TISSUE_WHOLE_BODY_EXACT_VALUES = {
    "body",
    "whole",
    "whole individual",
    "individual",
    "immature individual",
    "full body",
    "total body",
    "entire organism",
    "entirety",
    "the whole",
    "whole adult",
    "adult",
    "adults",
    "adult female",
    "adult male",
    "adult, body",
    "larval",
    "imago",
    "eggs",
    "holl body",
    "all",
    "individuls",
    "entire",
    "entire insect",
    "whole silkworm",
    "whole animals",
    "complete worm",
    "insect body",
    "body of beet armyworm",
    "all the insect body",
    "the whole adult",
    "the whole female aduit",
    "the whole male aduit",
    "adult body",
    "total tissue",
    "all tissue",
    "all tissues",
}

TISSUE_WHOLE_BODY_PATTERNS = (
    r"\bwhole body\b",
    r"\bwholebody\b",
    r"\bwhole organism\b",
    r"\bwhole specimen\b",
    r"\bwhole animal\b",
    r"\bwhole insect\b",
    r"\bwhole insects\b",
    r"\bwhole worm\b",
    r"\bwhole worm\d+\b",
    r"\bwhole animals\b",
    r"\bwhole larva\b",
    r"\bwhole larvae\b",
    r"\bwhole larvae x \d+\b",
    r"\bwhole larve\b",
    r"\bwhole embryos\b",
    r"\bwhole bodies\b",
    r"\bwhole organisms, pooled\b",
    r"\bwhole silkworm\b",
    r"\bentire body\b",
    r"\bentire insect\b",
    r"\ball body\b",
    r"\bthe whole body\b",
    r"\bthe whole insect\b",
    r"\bfemale adult whole worm\b",
    r"\badult moth\b",
    r"\blarval body\b",
    r"\bwhole insect tissue\b",
    r"\bwhole b\.?mori larva\b",
    r"\bwhole l\d+ larvae\b",
    r"\bwhole body\d+\b",
    r"\bbody\d+\b",
    r"\begg\d+\b",
    r"\blarvae\d+\b",
    r"\b\d+larvae\b",
    r"\badults?\d+\b",
    r"\bwhole\b.*\bbody\b",
    r"\bwhole bady\b",
    r"\bwhole\b.*\borganism\b",
    r"\binsect body\b",
    r"\bbody of beet armyworm\b",
    r"\b(?:male|female)\.body\b",
    r"\b(?:male|female)\s+adults?\b",
    r"\badult body\b",
    r"\badult female\b",
    r"\badult male\b",
    r"\bthe whole adult\b",
    r"\bthe whole female aduit\b",
    r"\bthe whole male aduit\b",
    r"\bmoth\b",
    r"\blarva\b",
    r"\blarvae\b",
    r"\bpupa\b",
    r"\bpupae\b",
    r"\bembryo\b",
    r"\bembryos\b",
    r"\bembyro\b",
    r"\begg\b",
    r"\beggs\b",
)

TISSUE_CANONICAL_PATTERNS = {
    "midgut": (
        r"\bmid[\s_-]*gut(?:\d+)?\b",
        r"\bmigdut\b",
        r"\bmiddut\b",
    ),
    "foregut": (r"\bfore[\s_-]*gut\b",),
    "hindgut": (r"\bhind[\s_-]*gut\b",),
    "gut": (
        r"\bgut\b",
        r"\balimentary tract\b",
        r"\balimentary tracts\b",
        r"\balimentary canal\b",
        r"\bintestinal tissue\b",
        r"\bmesenteron\b",
    ),
    "fat body": (
        r"\bfat[\s_-]*body\b",
        r"\bfatbody\b",
        r"\bfatbody\d+\b",
        r"\bfat\b",
        r"\bfatt[\s_-]*body\b",
        r"\bfatty[\s_-]*body\b",
        r"\bfatabody\b",
        r"\bfatbady\d*\b",
    ),
    "antenna": (
        r"\bantenna\b",
        r"\bantennae\b",
        r"\bantennae\d+(?:-\d+)?\b",
        r"\bantennal\b",
        r"\bantenae\b",
        r"\bantena\b",
        r"\bantennea\b",
        r"\bfeeler\b",
    ),
    "head": (r"\bhead(?:s)?(?:\d+(?:-\d+)?)?\b",),
    "silk gland": (
        r"\bsilk[\s_-]*gland\b",
        r"\bsilk[\s_-]*glands\b",
        r"\bsilkgland\d+\b",
        r"\banterior[\s_-]*silk[\s_-]*gland\b",
        r"\banterior[\s_-]*silk[\s_-]*glands\b",
        r"\banterior[\s_-]*silk[\s_-]*gland\d+\b",
        r"\bposterior[\s_-]*silk[\s_-]*gland\b",
        r"\bposterior[\s_-]*silk[\s_-]*glands\b",
        r"\bposterior[\s_-]*silk[\s_-]*gland\d+\b",
        r"\bmiddle[\s_-]*silk[\s_-]*gland\b",
        r"\bmiddle[\s_-]*silk[\s_-]*glands\b",
        r"\bmiddle[\s_-]*silk[\s_-]*gland\d+\b",
        r"\bpostsilk[\s_-]*gland\b",
        r"\bsilk glandl\b",
        r"\bpsg\b",
    ),
    "leg": (
        r"\bleg\b",
        r"\blegs\b",
        r"\bforeleg\b",
        r"\bforelegs\b",
        r"\bhindleg\b",
        r"\bhindlegs\b",
    ),
    "wing": (
        r"\bwing\b",
        r"\bwings\b",
        r"\bforewing\d*\b",
        r"\bforewings\b",
        r"\bhindwing\d*\b",
        r"\bhindwings\b",
        r"\bpupal forewings\b",
        r"\bwing disc\b",
        r"\bwing sector\b",
        r"\beyespot\b",
    ),
    "brain": (
        r"\bbrain(?:s)?(?:\d+)?\b",
        r"\bbrian-\d+\b",
        r"\bbrian\d+\b",
        r"\bganglion\b",
    ),
    "mouthpart": (
        r"\bmouthpart\b",
        r"\bmouthparts\b",
        r"\bproboscis\b",
        r"\bproboscises\b",
        r"\bmaxilla\b",
        r"\bmaxlla\b",
        r"\bmaxillae\b",
        r"\bmaxillary galea\b",
        r"\bmaxillary gelea\b",
        r"\bepipharynx\b",
        r"\blabrum\b",
        r"\btentacle\b",
        r"\bmaxillary palp\b",
        r"\bmaxillary palps\b",
        r"\bpalp\b",
        r"\bpalps\b",
    ),
    "epidermis": (
        r"\bepidermis\b",
        r"\bintegument\b",
        r"\bcuticle\b",
        r"\bepithelium\b",
        r"\bskin\b",
    ),
    "ovary": (
        r"\bovary\b",
        r"\bovaries\b",
        r"\bovarian\b",
        r"\bovariole\b",
        r"\bovery\b",
        r"\boogamete\b",
    ),
    "abdomen": (r"\babdomen\b", r"\babdominal\b", r"\bsegment a\d+\b", r"\bmid body\b"),
    "thorax": (r"\bthorax\b", r"\bthoracic\b", r"\bthroax\b", r"\bchest\b"),
    "testis": (r"\btestis\b", r"\btestes\b", r"\btesticles\b", r"\btestis\d+\b"),
    "reproductive system": (
        r"\breproductive system\b",
        r"\bgonad\b",
        r"\bgonads\b",
        r"\bsperm bundles\b",
        r"\bgermline\b",
    ),
    "reproductive tract": (
        r"\breproductive tract\b",
        r"\breproductive organ\b",
        r"\breproductive organs\b",
        r"\bbursa\b",
    ),
    "ejaculatory duct": (
        r"\bejaculatory duct\b",
        r"\bductus ejaculatorius simplex\b",
    ),
    "oviduct": (r"\boviduct\b",),
    "hemolymph": (
        r"\bhemolymph\b",
        r"\bhaemolymph(?:\s+\d+)?\b",
        r"\bhemocyte\b",
        r"\bhemocytes\b",
        r"\bhemacyte\b",
        r"\bblood cell\b",
        r"\badipoleucocyte\b",
        r"\bblood\b",
    ),
    "malpighian tubule": (
        r"\bmalpighian tubule\b",
        r"\bmalpighian tubules\b",
        r"\bmalpighian tube\b",
        r"\bmalpighian tubes\b",
        r"\bmalpighian tubes?\d+\b",
        r"\bmalpighian[\s_-]*tubule\b",
        r"\bmalphigian tubules\b",
        r"\bmalph\. tubules\b",
        r"\bmalpighi[_ ]replacate\b",
        r"\bmarkov tube\b",
    ),
    "pheromone gland": (
        r"\bpheromone gland\b",
        r"\bpheromone glands\b",
        r"\bpheomone gland\b",
        r"\bpheromonal gland\b",
    ),
    "accessory gland": (
        r"\baccessory gland\b",
        r"\baccessory glands\b",
        r"\bsex accessory gland\b",
        r"\bbursal gland\b",
        r"\bsex gland\b",
        r"\bverson's glands?\b",
    ),
    "labial gland": (r"\blabial glands?\b", r"\blabila glands?\b"),
    "salivary gland": (r"\bsalivary glands?\b",),
    "muscle": (r"\bmuscle\b", r"\bmuscles\b"),
    "eye": (r"\beye\b", r"\beyes\b", r"\bretina\b", r"\bsensillae\b"),
    "ovipositor": (r"\bovipositor\b", r"\bovipositors\b"),
    "foot": (
        r"\bfoot\b",
        r"\bfeet\b",
        r"\btarsi\b",
        r"\btarsus\b",
        r"\bforetarsi\b",
        r"\bmidtarsi\b",
        r"\bhindtarsi\b",
        r"\bproleg\b",
    ),
    "nervous system": (
        r"\bcentral nervous system\b",
        r"\bnervous system\b",
        r"\blarval nervous system\b",
        r"\bventral nerve cord\b",
        r"\bnerve cord\b",
        r"\bnerve chord\b",
        r"\bventral nerve\b",
        r"\bnerve\b",
    ),
    "serosa": (r"\bserosa\b",),
    "prothoracic gland": (r"\bprothoracic gland\b", r"\bprothoracic glands\b", r"\bpg\b"),
    "trachea": (r"\btrachea\b", r"\btracheal\b"),
    "heart": (r"\bheart\b", r"\bdorsal vessel\b"),
    "horn": (r"\bhorn\b", r"\bscoli\b", r"\bscolus\b"),
    "carcass": (r"\bcarcass\b", r"\bresidual body\b"),
    "whole body": TISSUE_WHOLE_BODY_PATTERNS,
}

SEX_MISSING_VALUES = {
    "",
    "n/a",
    "na",
    "nan",
    "none",
    "null",
    "missing",
    "not applicable",
    "not collected",
    "not determined",
    "not provided",
    "not available",
    "unknown",
    "unknown sex",
    "restricted access",
    "undetermined",
}

SEX_MIXED_VALUES = {
    "pooled male and female",
    "male and female",
    "female and male",
    "mixed",
    "intersex",
}

STAGE_CANONICAL = (
    "egg",
    "embryo",
    "larva",
    "instar",
    "prepupa",
    "pupa",
    "adult",
    "mixed",
    "unknown",
)
STAGE_CANONICAL_LOOKUP = {value: value for value in STAGE_CANONICAL}

STAGE_MISSING_VALUES = {
    "",
    "n/a",
    "na",
    "nan",
    "none",
    "null",
    "missing",
    "not applicable",
    "not collected",
    "not provided",
    "not available",
    "unknown",
    "undetermined",
    "restricted access",
}

ORDINAL_WORDS = (
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
)
CARDINAL_WORDS = (
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
)
WORD_TO_NUM = {
    "one": 1,
    "first": 1,
    "two": 2,
    "second": 2,
    "three": 3,
    "third": 3,
    "four": 4,
    "fourth": 4,
    "forth": 4,
    "five": 5,
    "fifth": 5,
    "fith": 5,
    "six": 6,
    "sixth": 6,
    "seven": 7,
    "seventh": 7,
    "eight": 8,
    "eighth": 8,
    "nine": 9,
    "ninth": 9,
    "ten": 10,
    "tenth": 10,
    "eleven": 11,
    "eleventh": 11,
    "twelve": 12,
    "twelfth": 12,
}
ROMAN_TO_NUM = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
    "xi": 11,
    "xii": 12,
}


def normalize_text(value: str) -> str:
    text = value.strip().lower()
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def looks_like_coordinate(value: str) -> bool:
    text = normalize_text(value)
    return bool(
        re.search(r"\b\d+(?:\.\d+)?\s*[ns]\b.*\b\d+(?:\.\d+)?\s*[ew]\b", text)
        or re.search(r"\b\d+(?:\.\d+)?\s*[ew]\b.*\b\d+(?:\.\d+)?\s*[ns]\b", text)
    )


def collapse_tissue_level1(tissue2_value: str) -> str:
    parts = [part.strip() for part in tissue2_value.split(";") if part.strip()]
    categories: list[str] = []
    for part in parts:
        category = TISSUE_LEVEL1_MAP.get(part, "unknown")
        if category not in categories:
            categories.append(category)
    return ";".join(categories) if categories else "unknown"


def infer_tissue(raw_value: str) -> tuple[str, str]:
    normalized = normalize_text(raw_value)

    if normalized in GENERAL_MISSING_VALUES:
        return TISSUE_UNKNOWN, "missing"
    if looks_like_coordinate(normalized):
        return TISSUE_UNKNOWN, "coordinate_like"
    if any(marker in normalized for marker in TISSUE_CELL_LINE_MARKERS):
        return TISSUE_UNKNOWN, "cell_line_or_cells"
    if normalized in TISSUE_WHOLE_BODY_EXACT_VALUES:
        return "whole body", "pattern:whole body"
    if normalized in TISSUE_CANONICAL_LOOKUP:
        return TISSUE_CANONICAL_LOOKUP[normalized], "exact"

    hits: list[str] = []
    for canonical, patterns in TISSUE_CANONICAL_PATTERNS.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            hits.append(canonical)

    if "midgut" in hits and "gut" in hits:
        hits.remove("gut")
    if "foregut" in hits and "gut" in hits:
        hits.remove("gut")
    if "hindgut" in hits and "gut" in hits:
        hits.remove("gut")
    if "ejaculatory duct" in hits and "reproductive tract" in hits:
        hits.remove("reproductive tract")
    if "oviduct" in hits and "reproductive tract" in hits:
        hits.remove("reproductive tract")
    if "ovary" in hits and "reproductive system" in hits:
        hits.remove("reproductive system")
    if "testis" in hits and "reproductive system" in hits:
        hits.remove("reproductive system")
    if "reproductive tract" in hits and "reproductive system" in hits:
        hits.remove("reproductive system")
    if normalized.startswith("head") and "without antenna" in normalized and "antenna" in hits:
        hits.remove("antenna")
    if "foot" in hits and "leg" in hits and "tarsi" not in normalized and "foot" not in normalized and "feet" not in normalized:
        hits.remove("foot")
    if "whole body" in hits and len(hits) > 1 and not any(
        token in normalized for token in ("without", "minus", "apart from")
    ):
        hits.remove("whole body")

    unique_hits = sorted(set(hits), key=lambda tissue: TISSUE_CANONICAL_ORDER.get(tissue, 10**9))
    if len(unique_hits) == 1:
        return unique_hits[0], f"pattern:{unique_hits[0]}"
    if len(unique_hits) > 1:
        return ";".join(unique_hits), "multi_match:" + "|".join(unique_hits)
    return TISSUE_UNKNOWN, "no_match"


def normalize_sex(raw_value: str) -> tuple[str, str]:
    text = raw_value.strip()
    normalized = normalize_text(text)
    if normalized in SEX_MISSING_VALUES:
        return "unknown", "missing_or_unknown"
    if normalized == "neuter":
        return "unknown", "missing_or_unknown"
    if normalized in SEX_MIXED_VALUES:
        return "mixed", "mixed_or_pooled"
    tokens = set(re.findall(r"[a-z]+", normalized))
    has_male = "male" in tokens
    has_female = "female" in tokens
    if has_male and has_female:
        return "mixed", "mixed_or_pooled"
    if "mixed" in tokens or "pooled" in tokens or "intersex" in tokens:
        return "mixed", "mixed_or_pooled"
    if has_male:
        return "male", "male"
    if has_female:
        return "female", "female"
    return "unknown", "missing_or_unknown"


def parse_stage_number(token: str) -> int | None:
    token = token.strip().lower()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in WORD_TO_NUM:
        return WORD_TO_NUM[token]
    if token in ROMAN_TO_NUM:
        return ROMAN_TO_NUM[token]
    return None


def to_l_code(value: int) -> str:
    return f"L{value}"


def to_p_code(value: int) -> str:
    return f"P{value}"


def to_l_day_code(instar: int, day: int) -> str:
    return f"L{instar}D{day}"


def detect_stage_flags(text: str) -> dict[str, bool]:
    return {
        "egg": contains_any(text, (r"\begg\b", r"\beggs\b", r"\begg mass\b", r"\begg stage\b")),
        "embryo": contains_any(
            text,
            (
                r"\bembryo\b",
                r"\bembryos\b",
                r"\bembyro\b",
                r"\bwhole embryos\b",
                r"\bembryonic\b",
                r"\bhpo\b",
                r"\bpost oviposition\b",
                r"\bafter oviposition\b",
                r"\b\d+\s*hour post oviposition\b",
            ),
        ),
        "larva": contains_any(
            text,
            (
                r"\blarva\b",
                r"\blarvae\b",
                r"\blarve\b",
                r"\blarvae\d+\b",
                r"\blarval\b",
                r"\bcaterpillar\b",
                r"\bneonate\b",
                r"\bpost hatch\b",
                r"\bmature larva\b",
                r"\bwanderer\b",
                r"\bwandering\b",
            ),
        ),
        "instar": contains_any(
            text,
            (
                r"\binstar\b",
                r"\binstars\b",
                r"\binstar[a-z0-9]+\b",
                r"\binatsr\b",
                r"\bl\d+(?:d\d+)?\b",
                r"\b\d+l(?:\d+d?)?\b",
                r"\b\d+(?:st|nd|rd|th)\s+instar\b",
                r"\b(?:"
                + "|".join(ORDINAL_WORDS)
                + r")\s+instar\b",
                r"\b\d+(?:st|nd|rd|th)?-day fifth-?instar\b",
                r"\b(?:"
                + "|".join(ORDINAL_WORDS)
                + r"|\d+(?:st|nd|rd|th)?)\s+age\b",
                r"\b(?:"
                + "|".join(CARDINAL_WORDS)
                + r"|\d+(?:st|nd|rd|th)?)\s+ages?\b",
                r"\bmolting stage [ivx]+\b",
            ),
        ),
        "prepupa": contains_any(
            text,
            (
                r"\bprepupa\b",
                r"\bprepupae\b",
                r"\bprepupal\b",
                r"\bpre-pupa\b",
                r"\bpre pupa\b",
                r"\bexact prepupa\b",
                r"\bpp\b",
            ),
        ),
        "pupa": contains_any(
            text,
            (
                r"\bpupa\b",
                r"\bpupae\b",
                r"\bpupal\b",
                r"\bchrysalis\b",
                r"\bpost-pupation\b",
                r"\bpost pupation\b",
                r"\bafter pupation\b",
                r"\bfrom pupation\b",
                r"\bday ?\d+.*pupa\b",
                r"\bp\d+\b",
                r"\blate-pupal\b",
                r"\b\d+% pupa\b",
            ),
        ),
        "adult": contains_any(
            text,
            (
                r"\badult\b",
                r"\badults\b",
                r"\badults\d+\b",
                r"\badut\b",
                r"\bimago\b",
                r"\bmoth\b",
                r"\bmoths\b",
                r"\bemerg(?:e|ed|ence|ing)\b",
                r"\beclos(?:e|ed|ion)\b",
                r"\beclosion\b",
                r"\bfreshly eclosed\b",
                r"\bvirgin\b",
            ),
        ),
        "explicit_mixed": contains_any(text, (r"\bmixed\b", r"\bmixture\b", r"\bvarious\b", r"\bmultiple\b")),
    }


def major_phases(flags: dict[str, bool]) -> set[str]:
    phases: set[str] = set()
    if flags["egg"] or flags["embryo"]:
        phases.add("early")
    if flags["larva"] or flags["instar"]:
        phases.add("larval")
    if flags["prepupa"]:
        phases.add("prepupa")
    if flags["pupa"]:
        phases.add("pupal")
    if flags["adult"]:
        phases.add("adult")
    return phases


def extract_instar_numbers(text: str) -> set[int]:
    numbers: set[int] = set()

    for match in re.finditer(r"\binstar\s*([ivx]+|\d+)\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    for match in re.finditer(r"\binstar([ivx]+|\d+)\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    for match in re.finditer(r"\b(\d+)\s*(?:st|nd|rd|th)?[-\s]*(?:inst(?:ar|er|asr|atsr)|inatsr)\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    for match in re.finditer(
        r"\b(" + "|".join(WORD_TO_NUM.keys()) + r")[-\s]*(?:inst(?:ar|er|asr|atsr)|inatsr)\b",
        text,
    ):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    for match in re.finditer(r"\bl(\d+)(?:d\d+)?\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    for match in re.finditer(r"\b(\d+)l(?:\d+d?)?\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    for match in re.finditer(r"(?<!day[-\s])\b(\d+)\s*(?:st|nd|rd|th)?\s*larva(?:e)?\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    for match in re.finditer(r"\b(" + "|".join(WORD_TO_NUM.keys()) + r")\s+larva(?:e)?\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    for match in re.finditer(
        r"\b(" + "|".join(WORD_TO_NUM.keys()) + r"|\d+(?:st|nd|rd|th)?)\s+ages?\b",
        text,
    ):
        value = parse_stage_number(re.sub(r"(st|nd|rd|th)$", "", match.group(1)))
        if value is not None:
            numbers.add(value)
    for match in re.finditer(r"\b([ivx]+)\s*-\s*([ivx]+)\s+larvae?\s+instar\b", text):
        for group in match.groups():
            value = parse_stage_number(group)
            if value is not None:
                numbers.add(value)

    return {value for value in numbers if 1 <= value <= 20}


def extract_day_numbers(text: str) -> set[int]:
    numbers: set[int] = set()

    for match in re.finditer(r"\bl\d+d(\d+)\b", text):
        numbers.add(int(match.group(1)))
    for match in re.finditer(r"\b\d+l(\d+)d\b", text):
        numbers.add(int(match.group(1)))
    for match in re.finditer(r"\bday[-\s]?(\d+)\b", text):
        numbers.add(int(match.group(1)))
    for match in re.finditer(r"\b(\d+)(?:st|nd|rd|th)?[-\s]*day\b", text):
        numbers.add(int(match.group(1)))
    for match in re.finditer(r"\b(" + "|".join(WORD_TO_NUM.keys()) + r")[-\s]*day\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    for match in re.finditer(r"\b(\d+)[-\s]*day[-\s]*old\b", text):
        numbers.add(int(match.group(1)))
    for match in re.finditer(r"\b(\d+)\s*days?\s*old\b", text):
        numbers.add(int(match.group(1)))
    for match in re.finditer(r"\b(" + "|".join(WORD_TO_NUM.keys()) + r")\s+days?\s+old\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)

    return {value for value in numbers if 0 <= value <= 200}


def extract_week_numbers(text: str) -> set[int]:
    numbers: set[int] = set()
    for match in re.finditer(r"\b(\d+)\s*weeks?\b", text):
        numbers.add(int(match.group(1)))
    for match in re.finditer(r"\b(" + "|".join(WORD_TO_NUM.keys()) + r")\s+weeks?\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    return {value for value in numbers if 0 <= value <= 52}


def extract_hour_day_numbers(text: str) -> set[int]:
    numbers: set[int] = set()
    for match in re.finditer(r"\b(\d+)\s*(?:hours?|hrs?|hr|h)\b", text):
        hours = int(match.group(1))
        if hours % 24 == 0:
            numbers.add(hours // 24)
    return {value for value in numbers if 0 <= value <= 200}


def normalize_stage1(raw_value: str) -> tuple[str, str]:
    normalized = normalize_text(raw_value)
    if normalized in STAGE_MISSING_VALUES:
        return "unknown", "missing_or_unknown"
    if normalized in STAGE_CANONICAL_LOOKUP:
        value = STAGE_CANONICAL_LOOKUP[normalized]
        return ("larva" if value == "instar" else value), "exact"

    flags = detect_stage_flags(normalized)
    phases = major_phases(flags)
    has_delimiter = any(delimiter in normalized for delimiter in (",", ";", "|", "/", " and "))

    if flags["explicit_mixed"]:
        return "mixed", "mixed_explicit"
    if len(phases) >= 2 and has_delimiter:
        return "mixed", "mixed_multi_phase"
    if "adult" in phases and len(phases) >= 2:
        return "mixed", "mixed_multi_phase"
    if "pupal" in phases and ("larval" in phases or "early" in phases):
        return "mixed", "mixed_multi_phase"
    if flags["prepupa"]:
        return "prepupa", "pattern:prepupa"
    if flags["adult"]:
        return "adult", "pattern:adult"
    if flags["pupa"]:
        return "pupa", "pattern:pupa"
    if flags["instar"]:
        return "larva", "pattern:instar"
    if flags["larva"]:
        return "larva", "pattern:larva"
    if flags["embryo"]:
        return "embryo", "pattern:embryo"
    if flags["egg"]:
        return "egg", "pattern:egg"
    return "unknown", "no_match"


def normalize_stage2(raw_value: str, stage1_value: str) -> tuple[str, str]:
    normalized = normalize_text(raw_value)
    day_numbers = extract_day_numbers(normalized)
    week_numbers = extract_week_numbers(normalized)
    hour_day_numbers = extract_hour_day_numbers(normalized)

    def resolve_time_only_code() -> tuple[str, str] | None:
        if len(day_numbers) == 1:
            return f"D{next(iter(day_numbers))}", "time_only_day"
        if not day_numbers and len(week_numbers) == 1:
            return f"W{next(iter(week_numbers))}", "time_only_week"
        if not day_numbers and not week_numbers and len(hour_day_numbers) == 1:
            return f"D{next(iter(hour_day_numbers))}", "time_only_hour_as_day"
        return None

    if stage1_value == "unknown":
        time_only = resolve_time_only_code()
        return time_only if time_only is not None else ("", "blank_no_specific_detail")
    if stage1_value == "egg":
        time_only = resolve_time_only_code()
        return time_only if time_only is not None else ("", "blank_no_specific_detail")
    if stage1_value == "embryo":
        time_only = resolve_time_only_code()
        return time_only if time_only is not None else ("", "blank_no_specific_detail")
    if stage1_value == "adult":
        time_only = resolve_time_only_code()
        return time_only if time_only is not None else ("", "blank_no_specific_detail")
    if stage1_value == "mixed":
        return "Mixed", "same_as_stage1_corr"
    if stage1_value == "prepupa":
        return "PP", "same_as_stage1_corr"
    if stage1_value == "pupa":
        for match in re.finditer(r"\bp(\d+)\b", normalized):
            return to_p_code(int(match.group(1))), "specific_pupa_code"
        for match in re.finditer(r"\b(?:day\s*|pupa day\s*|pupal day\s*|day)(\d+)\b", normalized):
            return to_p_code(int(match.group(1))), "specific_pupa_day"
        for match in re.finditer(r"\b(\d+)\s*days?\s*(?:after|from)\s*pupation\b", normalized):
            return to_p_code(int(match.group(1))), "specific_pupa_day"
        for match in re.finditer(r"\b(\d+)\s*days?\s*post[- ]pupa(?:tion)?\b", normalized):
            return to_p_code(int(match.group(1))), "specific_pupa_day"
        for match in re.finditer(r"\b(\d+)\s*[- ]?day[- ]old pupa\b", normalized):
            return to_p_code(int(match.group(1))), "specific_pupa_day"
        for match in re.finditer(r"\b(\d+)\s*(?:hours?|hrs?|hr|h)\s*(?:after|from)?\s*pupa(?:tion)?\b", normalized):
            hours = int(match.group(1))
            return to_p_code(0 if hours < 24 else max(1, round(hours / 24))), "specific_pupa_hour"
        if re.search(r"\bearly pupa\b|\bday ?0\b|\bpupa day 0\b|\bp0\b", normalized):
            return "P0", "specific_pupa_code"
        return "", "blank_no_specific_detail"

    # larva
    if any(token in normalized for token in (";", "|", " and ")) and ("instar" in normalized or "larva" in normalized):
        numbers = extract_instar_numbers(normalized)
        if len(numbers) >= 2:
            return "Mixed", "mixed_multi_instar"
    instar_numbers = extract_instar_numbers(normalized)
    if len(instar_numbers) >= 2:
        return "Mixed", "mixed_multi_instar"
    if "neonate" in normalized and instar_numbers:
        return "Mixed", "mixed_multi_instar"
    if len(instar_numbers) == 1:
        instar = next(iter(instar_numbers))
        if len(day_numbers) == 1:
            return to_l_day_code(instar, next(iter(day_numbers))), "specific_instar_day"
        return to_l_code(instar), "specific_instar"
    if any(
        phrase in normalized
        for phrase in ("last instar", "last inster", "late instar", "ultimate instar", "final stage", "mature larva")
    ):
        return "Last instar", "last_instar"
    if "neonate" in normalized or "post hatch" in normalized:
        if len(day_numbers) == 1:
            return to_l_day_code(1, next(iter(day_numbers))), "neonate_to_first_instar_day"
        return "L1", "neonate_to_first_instar"
    time_only = resolve_time_only_code()
    return time_only if time_only is not None else ("", "blank_no_specific_detail")


def insert_after(columns: list[str], base_column: str, new_columns: list[str]) -> list[str]:
    filtered = [column for column in columns if column not in new_columns]
    index = filtered.index(base_column)
    filtered[index + 1:index + 1] = new_columns
    return filtered


def resolve_output_paths(
    input_path: Path,
    output_path: Path | None,
    review_dir: Path | None,
    in_place: bool,
) -> tuple[Path, Path]:
    if in_place:
        csv_output = input_path
    elif output_path is not None:
        csv_output = output_path
    else:
        csv_output = input_path.with_name(input_path.stem + ".normalized.csv")

    if review_dir is not None:
        review_dir.mkdir(parents=True, exist_ok=True)
        base_name = csv_output.stem
        return csv_output, review_dir / base_name

    return csv_output, csv_output.with_suffix("")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_PATH,
        help=f"Input CSV path. Default: {DEFAULT_INPUT_PATH}",
    )
    parser.add_argument("--output", type=Path, help="Output CSV path.")
    parser.add_argument(
        "--review-dir",
        type=Path,
        default=DEFAULT_REVIEW_DIR,
        help=f"Directory for review TSVs. Default: {DEFAULT_REVIEW_DIR}",
    )
    parser.add_argument("--in-place", action="store_true", help="Overwrite the input CSV.")
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    csv_output, review_prefix = resolve_output_paths(args.input, args.output, args.review_dir, args.in_place)
    temp_csv = csv_output.with_suffix(csv_output.suffix + ".tmp")

    tissue_review: Counter[tuple[str, str, str, str]] = Counter()
    sex_review: Counter[tuple[str, str, str]] = Counter()
    stage_review: Counter[tuple[str, str, str, str, str]] = Counter()

    with args.input.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {args.input}")

        required = {"Tissue", "Sex", "Stage"}
        missing = sorted(required.difference(reader.fieldnames))
        if missing:
            raise ValueError("Input CSV is missing required columns: " + ", ".join(missing))

        output_fields = list(reader.fieldnames)
        output_fields = [field for field in output_fields if field not in {
            "Tissue.Corr", "Tissue1.Corr", "Tissue2.Corr",
            "Sex.Corr",
            "Stage.Corr", "Stage1.Corr", "Stage2.Corr",
        }]
        output_fields = insert_after(output_fields, "Tissue", ["Tissue1.Corr", "Tissue2.Corr"])
        output_fields = insert_after(output_fields, "Sex", ["Sex.Corr"])
        output_fields = insert_after(output_fields, "Stage", ["Stage1.Corr", "Stage2.Corr"])

        with temp_csv.open("w", encoding="utf-8-sig", newline="") as dst:
            writer = csv.DictWriter(dst, fieldnames=output_fields)
            writer.writeheader()

            for row in reader:
                raw_tissue = (row.get("Tissue") or "").strip()
                raw_sex = (row.get("Sex") or "").strip()
                raw_stage = (row.get("Stage") or "").strip()

                for key in ("Tissue.Corr", "Tissue1.Corr", "Tissue2.Corr", "Sex.Corr", "Stage.Corr", "Stage1.Corr", "Stage2.Corr"):
                    row.pop(key, None)

                tissue2_corr, tissue_reason = infer_tissue(raw_tissue)
                tissue1_corr = collapse_tissue_level1(tissue2_corr)
                sex_corr, sex_reason = normalize_sex(raw_sex)
                stage1_corr, stage_reason = normalize_stage1(raw_stage)
                stage2_corr, stage2_reason = normalize_stage2(raw_stage, stage1_corr)

                row["Tissue1.Corr"] = tissue1_corr
                row["Tissue2.Corr"] = tissue2_corr
                row["Sex.Corr"] = sex_corr
                row["Stage1.Corr"] = stage1_corr
                row["Stage2.Corr"] = stage2_corr
                writer.writerow(row)

                tissue_review[(raw_tissue or "N/A", tissue1_corr, tissue2_corr, tissue_reason)] += 1
                sex_review[(raw_sex or "N/A", sex_corr, sex_reason)] += 1
                stage_review[(raw_stage or "N/A", stage1_corr, stage2_corr, stage_reason, stage2_reason)] += 1

    temp_csv.replace(csv_output)

    tissue_review_path = review_prefix.parent / f"{review_prefix.name}.tissue_corr_review.tsv"
    sex_review_path = review_prefix.parent / f"{review_prefix.name}.sex_corr_review.tsv"
    stage_review_path = review_prefix.parent / f"{review_prefix.name}.stage_corr_review.tsv"

    with tissue_review_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(["raw_tissue", "count", "tissue1_corr", "tissue2_corr", "reason"])
        for (raw_tissue, tissue1_corr, tissue2_corr, reason), count in sorted(
            tissue_review.items(),
            key=lambda item: (-item[1], item[0][2], item[0][1], item[0][0]),
        ):
            writer.writerow([raw_tissue, count, tissue1_corr, tissue2_corr, reason])

    with sex_review_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(["raw_sex", "count", "sex_corr", "reason"])
        for (raw_sex, sex_corr, reason), count in sorted(
            sex_review.items(),
            key=lambda item: (-item[1], item[0][1], item[0][0]),
        ):
            writer.writerow([raw_sex, count, sex_corr, reason])

    with stage_review_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(["raw_stage", "count", "stage1_corr", "stage2_corr", "reason", "stage2_reason"])
        for (raw_stage, stage1_corr, stage2_corr, reason, stage2_reason), count in sorted(
            stage_review.items(),
            key=lambda item: (-item[1], item[0][1], item[0][2], item[0][0]),
        ):
            writer.writerow([raw_stage, count, stage1_corr, stage2_corr, reason, stage2_reason])

    print(f"Wrote normalized CSV: {csv_output}")
    print(f"Wrote tissue review TSV: {tissue_review_path}")
    print(f"Wrote sex review TSV: {sex_review_path}")
    print(f"Wrote stage review TSV: {stage_review_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
