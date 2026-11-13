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
import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

DEFAULT_INPUT_PATH = Path("data/clean/SRA/transcriptome_data_20260323.csv")
DEFAULT_REVIEW_DIR = Path("data/clean/SRA")
DEFAULT_CACHE_DB = Path("data/clean/SRA/ncbi_request_cache.sqlite")
DEFAULT_SYNONYM_OUTPUT = Path("data/raw/SRA/biosample/synonym_groups.tsv")
CANONICAL_MISSING_MARKER = "N/A"
NORMALIZED_TO_CANONICAL_MISSING = {
    "n/a": CANONICAL_MISSING_MARKER,
    "na": CANONICAL_MISSING_MARKER,
    "nan": CANONICAL_MISSING_MARKER,
    "none": CANONICAL_MISSING_MARKER,
    "null": CANONICAL_MISSING_MARKER,
    "missing": CANONICAL_MISSING_MARKER,
    "not applicable": CANONICAL_MISSING_MARKER,
    "not collected": CANONICAL_MISSING_MARKER,
    "not provided": CANONICAL_MISSING_MARKER,
    "not available": CANONICAL_MISSING_MARKER,
    "not determined": CANONICAL_MISSING_MARKER,
    "undetermined": CANONICAL_MISSING_MARKER,
    "restricted access": CANONICAL_MISSING_MARKER,
    "unknown": CANONICAL_MISSING_MARKER,
    "unknown sex": CANONICAL_MISSING_MARKER,
}


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
TISSUE_MISSING_VALUES = {
    "n/a",
    "na",
    "missing",
    "not applicable",
    "not collected",
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
    "mixed",
    "whole body",
    "other",
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
    "mixed": "mixed",
    "whole body": "whole_body",
    "other": "other",
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
    "cell",
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
    "adult, body",
    "holl body",
    "all",
    "individuls",
    "entire",
    "entire insect",
    "whole silkworm",
    "whole animals",
    "complete worm",
    "insect body",
    "full organism",
    "somatic body tissue",
    "somatic body tissues",
    "other somatic animal tissue",
    "**other somatic animal tissue**",
    "body of beet armyworm",
    "all the insect body",
    "the whole adult",
    "the whole female aduit",
    "the whole male aduit",
    "adult body",
    "total tissue",
    "all tissue",
    "all tissues",
    "all tissued",
}

TISSUE_OTHER_EXACT_VALUES = {
    "other somatic animal tissue",
    "**other somatic animal tissue**",
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
    r"\bwhole adult\b",
    r"\bwhole adults\b",
    r"\bwhole larva\b",
    r"\bwhole larvae\b",
    r"\bwhole larvae x \d+\b",
    r"\bwhole larve\b",
    r"\bwhole pupa\b",
    r"\bwhole pupae\b",
    r"\bwhole (?:male|female) pupa\b",
    r"\bwhole (?:male|female) pupae\b",
    r"\bwhole embryos\b",
    r"\bwhole bodies\b",
    r"\bwhole organisms, pooled\b",
    r"\bwhole silkworm\b",
    r"\bentire body\b",
    r"\bentire insect\b",
    r"\ball body\b",
    r"\bthe whole body\b",
    r"\bthe whole insect\b",
    r"\bthe whole pupae\b",
    r"\bthe whole (?:male|female) pupae\b",
    r"\bfemale adult whole worm\b",
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
    r"\bfull organism\b",
    r"\bsomatic body tissues?\b",
    r"\bother somatic animal tissue\b",
    r"\bbody of beet armyworm\b",
    r"\b(?:male|female)\.body\b",
    r"\b(?:male|female)\s+adults?\b",
    r"\badult body\b",
    r"\badult female\b",
    r"\badult male\b",
    r"\bthe whole adult\b",
    r"\bthe whole female aduit\b",
    r"\bthe whole male aduit\b",
)

TISSUE_CANONICAL_PATTERNS = {
    "mixed": (
        r"\bmixed tissue\b",
        r"\bmixed tissues\b",
        r"\bmultiple tissues\b",
        r"\bpooled tissues\b",
        r"\bpooled tissue\b",
        r"\bmixed sample\b",
        r"\bpooled sample\b",
        r"\bmulti[- ]tissue\b",
        r"\bvarious tissues\b",
    ),
    "midgut": (
        r"\bmid[\s_-]*gut(?:\d+)?\b",
        r"\bmid[\s_-]*guts\b",
        r"\bmigdut\b",
        r"\bmiddut\b",
    ),
    "foregut": (r"\bfore[\s_-]*gut\b", r"\bfore[\s_-]*guts\b"),
    "hindgut": (r"\bhind[\s_-]*gut\b", r"\bhind[\s_-]*guts\b"),
    "gut": (
        r"\bgut\b",
        r"\bguts\b",
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

MONTH_NAME_TO_NUM = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def normalize_text(value: str) -> str:
    text = value.strip().lower()
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_collection_date(raw_value: str) -> str:
    cleaned = raw_value.strip()
    if not cleaned:
        return ""

    preserved = preserved_missing_marker(cleaned, GENERAL_MISSING_VALUES)
    if preserved is not None:
        return preserved

    normalized = normalize_text(cleaned)
    if normalized.startswith("missing:") or normalized.startswith("not "):
        return CANONICAL_MISSING_MARKER

    if "/" in cleaned:
        parts = [part.strip() for part in cleaned.split("/") if part.strip()]
        if len(parts) >= 2:
            normalized_parts = [normalize_collection_date(part) for part in parts]
            if all(part and part != CANONICAL_MISSING_MARKER for part in normalized_parts):
                return "/".join(normalized_parts)

    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    if re.fullmatch(r"\d{4}", cleaned):
        return cleaned
    if re.fullmatch(r"\d{4}-\d{2}", cleaned):
        return cleaned
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return cleaned

    month_year_match = re.fullmatch(r"([A-Za-z]{3,9})-(\d{4})", cleaned)
    if month_year_match:
        month = MONTH_NAME_TO_NUM.get(month_year_match.group(1).lower()[:3])
        if month is not None:
            return f"{month_year_match.group(2)}-{month:02d}"

    return cleaned


def normalize_geographic_location(raw_value: str) -> str:
    cleaned = raw_value.strip()
    if not cleaned:
        return ""

    preserved = preserved_missing_marker(cleaned, GENERAL_MISSING_VALUES)
    if preserved is not None:
        return preserved

    if parse_coordinate_pair(cleaned) is not None or looks_like_coordinate(cleaned):
        return CANONICAL_MISSING_MARKER

    normalized = re.sub(r"\s+", " ", cleaned)
    normalized = re.sub(r"\s*:\s*", ": ", normalized)
    normalized = re.sub(r"\s*,\s*", ", ", normalized)
    normalized = re.sub(r"\bfield population sample\s+\d+\b", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bsample\s+\d+\b", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b([A-Za-z][A-Za-z' -]+)-\d+\b", r"\1", normalized)
    normalized = re.sub(r"\b([A-Za-z][A-Za-z' -]+)\s+\d+\b$", r"\1", normalized)
    normalized = re.sub(r"\s*,\s*$", "", normalized)
    normalized = re.sub(r"\s{2,}", " ", normalized).strip(" ,;")
    return normalized


TISSUE_CONTEXT_MARKERS = (
    "whole",
    "entire",
    "full",
    "complete",
    "body",
    "tissue",
    "organ",
    "organism",
    "insect",
    "worm",
    "gland",
    "gut",
    "tract",
    "head",
    "thorax",
    "abdomen",
    "leg",
    "wing",
    "antenna",
    "brain",
    "eye",
    "mouthpart",
    "epidermis",
    "ovary",
    "testis",
    "hemolymph",
    "tubule",
    "muscle",
    "foot",
    "nervous",
    "serosa",
    "trachea",
    "heart",
    "horn",
    "carcass",
    "ovipositor",
    "reproductive",
    "salivary",
    "labial",
    "accessory",
    "prothoracic",
    "pheromone",
    "silk",
)

WHOLE_BODY_QUALIFIER_MARKERS = (
    "whole",
    "full",
    "all",
    "entire",
    "complete",
    "individual",
    "individuls",
    "organism",
)

STAGE_ONLY_TOKENS = (
    "egg",
    "eggs",
    "embryo",
    "embryos",
    "embyro",
    "larva",
    "larvae",
    "larval",
    "instar",
    "pupa",
    "pupae",
    "prepupa",
    "adult",
    "adults",
    "imago",
    "moth",
    "moths",
    "neonate",
    "nymph",
)


def is_stage_only_tissue_value(normalized: str) -> bool:
    if not any(token in normalized for token in STAGE_ONLY_TOKENS):
        return False
    if any(marker in normalized for marker in WHOLE_BODY_QUALIFIER_MARKERS):
        return False
    return not any(marker in normalized for marker in TISSUE_CONTEXT_MARKERS)


def is_non_tissue_metadata_value(normalized: str) -> bool:
    if looks_like_coordinate(normalized):
        return False
    if any(marker in normalized for marker in TISSUE_CELL_LINE_MARKERS):
        return False
    if any(marker in normalized for marker in TISSUE_CONTEXT_MARKERS):
        return False
    if any(token in normalized for token in STAGE_ONLY_TOKENS):
        return True
    sex_corr, _ = normalize_sex(normalized)
    return sex_corr in {"male", "female", "mixed"}


def preserved_missing_marker(raw_value: str, missing_values: set[str]) -> str | None:
    cleaned = raw_value.strip()
    if not cleaned:
        return None
    normalized = normalize_text(cleaned)
    if normalized in missing_values:
        return NORMALIZED_TO_CANONICAL_MISSING.get(normalized, cleaned)
    return None


def contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def looks_like_coordinate(value: str) -> bool:
    text = normalize_text(value)
    return bool(
        re.search(r"\b\d+(?:\.\d+)?\s*[ns]\b.*\b\d+(?:\.\d+)?\s*[ew]\b", text)
        or re.search(r"\b\d+(?:\.\d+)?\s*[ew]\b.*\b\d+(?:\.\d+)?\s*[ns]\b", text)
        or re.fullmatch(r"\s*[-+]?\d+(?:\.\d+)?\s*[,;/]\s*[-+]?\d+(?:\.\d+)?\s*", text)
    )


INVALID_COORDINATE_VALUES = {
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


def clean_coordinate_text(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if normalize_text(text) in INVALID_COORDINATE_VALUES:
        return None
    return text


def format_coordinate_value(value: float) -> str:
    formatted = f"{value:.8f}".rstrip("0").rstrip(".")
    return formatted if formatted not in {"-0", "+0", ""} else "0"


def is_valid_coordinate_pair(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180


def parse_coordinate_component(value: str, direction: str | None = None) -> float | None:
    cleaned = clean_coordinate_text(value)
    if cleaned is None:
        return None
    try:
        numeric = float(cleaned)
    except ValueError:
        return None
    if direction and direction.upper() in {"S", "W"}:
        numeric = -abs(numeric)
    return numeric


def parse_coordinate_pair(value: str) -> str | None:
    cleaned = clean_coordinate_text(value)
    if cleaned is None:
        return None

    cardinal_match = re.fullmatch(
        r"\s*([-+]?\d+(?:\.\d+)?)\s*([NS])\s+([-+]?\d+(?:\.\d+)?)\s*([EW])\s*",
        cleaned,
        flags=re.IGNORECASE,
    )
    if cardinal_match:
        lat = parse_coordinate_component(cardinal_match.group(1), cardinal_match.group(2))
        lon = parse_coordinate_component(cardinal_match.group(3), cardinal_match.group(4))
        if lat is not None and lon is not None and is_valid_coordinate_pair(lat, lon):
            return f"{format_coordinate_value(lat)}, {format_coordinate_value(lon)}"

    decimal_pair = re.fullmatch(
        r"\s*([-+]?\d+(?:\.\d+)?)\s*[,;/]\s*([-+]?\d+(?:\.\d+)?)\s*",
        cleaned,
    )
    if decimal_pair:
        lat = parse_coordinate_component(decimal_pair.group(1))
        lon = parse_coordinate_component(decimal_pair.group(2))
        if lat is not None and lon is not None and is_valid_coordinate_pair(lat, lon):
            return f"{format_coordinate_value(lat)}, {format_coordinate_value(lon)}"

    return None


def normalize_geographic_coordinates(raw_value: str) -> str:
    cleaned = raw_value.strip()
    if not cleaned:
        return ""

    preserved = preserved_missing_marker(cleaned, INVALID_COORDINATE_VALUES)
    if preserved is not None:
        return preserved

    normalized = parse_coordinate_pair(cleaned)
    return normalized if normalized is not None else CANONICAL_MISSING_MARKER


def backfill_geographic_coordinates(row: dict[str, str]) -> str:
    direct = parse_coordinate_pair(row.get("Geographic coordinates", ""))
    if direct is not None:
        return direct

    priority_fields = (
        "Geographic location",
        "Tissue",
        "Stage",
    )
    for field in priority_fields:
        candidate = parse_coordinate_pair(row.get(field, ""))
        if candidate is not None:
            return candidate

    for field, value in row.items():
        if field in {"Geographic coordinates", *priority_fields}:
            continue
        candidate = parse_coordinate_pair(value)
        if candidate is not None:
            return candidate
    return row.get("Geographic coordinates", "")


def collapse_tissue_level1(tissue2_value: str) -> str:
    preserved = preserved_missing_marker(tissue2_value, TISSUE_MISSING_VALUES)
    if preserved is not None:
        return preserved
    if not tissue2_value.strip():
        return ""
    if tissue2_value == "other":
        return CANONICAL_MISSING_MARKER
    parts = [part.strip() for part in tissue2_value.split(";") if part.strip()]
    categories: list[str] = []
    for part in parts:
        category = TISSUE_LEVEL1_MAP.get(part, CANONICAL_MISSING_MARKER)
        if category == "other":
            category = CANONICAL_MISSING_MARKER
        if category not in categories:
            categories.append(category)
    if not categories:
        return CANONICAL_MISSING_MARKER
    if len(categories) == 1:
        return categories[0]
    return "mixed"


def infer_tissue(raw_value: str) -> tuple[str, str]:
    if raw_value.strip() == "":
        return "", "blank"
    preserved = preserved_missing_marker(raw_value, TISSUE_MISSING_VALUES)
    if preserved is not None:
        return preserved, "preserved_missing_marker"
    normalized = normalize_text(raw_value)

    if looks_like_coordinate(normalized):
        return CANONICAL_MISSING_MARKER, "coordinate_like"
    if any(marker in normalized for marker in TISSUE_CELL_LINE_MARKERS):
        return CANONICAL_MISSING_MARKER, "cell_line_or_cells"
    if is_non_tissue_metadata_value(normalized):
        return CANONICAL_MISSING_MARKER, "non_tissue_metadata"
    if normalized in TISSUE_OTHER_EXACT_VALUES:
        return CANONICAL_MISSING_MARKER, "exact:other"
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
    return CANONICAL_MISSING_MARKER, "no_match"


def normalize_sex(raw_value: str) -> tuple[str, str]:
    if raw_value.strip() == "":
        return "", "blank"
    preserved = preserved_missing_marker(raw_value, SEX_MISSING_VALUES)
    if preserved is not None:
        return preserved, "preserved_missing_marker"
    text = raw_value.strip()
    normalized = normalize_text(text)
    if normalized == "neuter":
        return CANONICAL_MISSING_MARKER, "mapped_to_missing"
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
    return CANONICAL_MISSING_MARKER, "mapped_to_missing"


def normalize_sex_strict(raw_value: str) -> tuple[str, str]:
    if raw_value.strip() == "":
        return "", "blank"
    preserved = preserved_missing_marker(raw_value, SEX_MISSING_VALUES)
    if preserved is not None:
        return preserved, "preserved_missing_marker"

    normalized = normalize_text(raw_value)
    if normalized in SEX_MIXED_VALUES:
        return "mixed", "mixed_or_pooled"
    if normalized in {"male", "female"}:
        return normalized, normalized

    tokens = set(re.findall(r"[a-z]+", normalized))
    sex_tokens = {"male", "female", "mixed", "pooled", "intersex"}
    if not tokens:
        return CANONICAL_MISSING_MARKER, "mapped_to_missing"
    if tokens.issubset(sex_tokens):
        return normalize_sex(raw_value)
    return CANONICAL_MISSING_MARKER, "non_sex_metadata_in_field"


def resolve_sex_from_sources(raw_sex: str, raw_tissue: str, raw_stage: str) -> tuple[str, str]:
    sex_corr, sex_reason = normalize_sex_strict(raw_sex)
    if sex_corr not in {"", CANONICAL_MISSING_MARKER}:
        return sex_corr, sex_reason

    tissue_sex_corr, tissue_sex_reason = normalize_sex(raw_tissue)
    if tissue_sex_corr not in {"", CANONICAL_MISSING_MARKER}:
        return tissue_sex_corr, f"fallback_from_tissue:{tissue_sex_reason}"

    stage_sex_corr, stage_sex_reason = normalize_sex(raw_stage)
    if stage_sex_corr not in {"", CANONICAL_MISSING_MARKER}:
        return stage_sex_corr, f"fallback_from_stage:{stage_sex_reason}"

    return (CANONICAL_MISSING_MARKER if sex_corr == CANONICAL_MISSING_MARKER else ""), sex_reason


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


def to_day_code(value: int) -> str:
    return f"{value}d"


def to_day_range_code(start: int, end: int) -> str:
    return f"{start}-{end}d"


def to_l_day_code(instar: int, day: int) -> str:
    return f"L{instar},{day}d"


def to_l_day_range_code(instar: int, start: int, end: int) -> str:
    return f"L{instar},{start}-{end}d"


def to_hour_code(value: int) -> str:
    return f"{value}h"


def to_week_code(value: int) -> str:
    return f"{value}w"


def to_p_code(value: int) -> str:
    if not 0 <= value <= 99:
        raise ValueError(f"Pupa code out of range: {value}")
    return f"P{value}"


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
                r"\b\d+(?:st|nd|rd|th)[-\s]+instar\b",
                r"\b(?:"
                + "|".join(ORDINAL_WORDS)
                + r")\s+instar\b",
                r"\b(?:"
                + "|".join(ORDINAL_WORDS)
                + r")[-\s]+instar\b",
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
    for match in re.finditer(r"\b(\d+)\s*days?\b", text):
        numbers.add(int(match.group(1)))
    for match in re.finditer(r"\b(" + "|".join(WORD_TO_NUM.keys()) + r")\s+days?\s+old\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    for match in re.finditer(r"\b(\d+)\s*d\.?p\.?i\.?\b", text):
        numbers.add(int(match.group(1)))

    return {value for value in numbers if 0 <= value <= 200}


def extract_day_range(text: str) -> tuple[int, int] | None:
    range_patterns = (
        r"\b(\d+)\s*(?:to|-)\s*(\d+)\s*days?\b",
        r"\bday\s*(\d+)\s*(?:to|-)\s*(\d+)\b",
        r"\b(\d+)\s*(?:to|-)\s*(\d+)\s*day[-\s]*old\b",
        r"\b(\d+)\s*(?:to|-)\s*(\d+)\s*d\.?p\.?i\.?\b",
    )
    for pattern in range_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2))
        if 0 <= start <= 200 and 0 <= end <= 200:
            return (start, end) if start <= end else (end, start)
    return None


def extract_week_numbers(text: str) -> set[int]:
    numbers: set[int] = set()
    for match in re.finditer(r"\b(\d+)\s*weeks?\b", text):
        numbers.add(int(match.group(1)))
    for match in re.finditer(r"\b(" + "|".join(WORD_TO_NUM.keys()) + r")\s+weeks?\b", text):
        value = parse_stage_number(match.group(1))
        if value is not None:
            numbers.add(value)
    return {value for value in numbers if 0 <= value <= 52}


def extract_hour_numbers(text: str) -> set[int]:
    numbers: set[int] = set()
    for match in re.finditer(r"\b(\d+)\s*(?:hours?|hrs?|hr|h)\b", text):
        numbers.add(int(match.group(1)))
    return {value for value in numbers if 0 <= value <= 200}


def normalize_stage1(raw_value: str) -> tuple[str, str]:
    if raw_value.strip() == "":
        return "", "blank"
    preserved = preserved_missing_marker(raw_value, STAGE_MISSING_VALUES)
    if preserved is not None:
        return preserved, "preserved_missing_marker"
    normalized = normalize_text(raw_value)
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
    return CANONICAL_MISSING_MARKER, "mapped_to_missing"


def normalize_stage2(raw_value: str, stage1_value: str) -> tuple[str, str]:
    if stage1_value == "":
        return "", "blank_no_specific_detail"
    preserved = preserved_missing_marker(raw_value, STAGE_MISSING_VALUES)
    if preserved is not None:
        return preserved, "preserved_missing_marker"
    normalized = normalize_text(raw_value)
    day_range = extract_day_range(normalized)
    day_numbers = extract_day_numbers(normalized)
    week_numbers = extract_week_numbers(normalized)
    hour_numbers = extract_hour_numbers(normalized)

    def resolve_time_only_code() -> tuple[str, str] | None:
        if day_range is not None:
            return to_day_range_code(*day_range), "time_only_day_range"
        if len(day_numbers) == 1:
            return to_day_code(next(iter(day_numbers))), "time_only_day"
        if not day_numbers and len(week_numbers) == 1:
            return to_week_code(next(iter(week_numbers))), "time_only_week"
        if not day_numbers and not week_numbers and len(hour_numbers) == 1:
            return to_hour_code(next(iter(hour_numbers))), "time_only_hour"
        return None

    if stage1_value == CANONICAL_MISSING_MARKER:
        time_only = resolve_time_only_code()
        return time_only if time_only is not None else ("", "blank_missing_stage1")

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
        return "", "blank_mixed_stage1"
    if stage1_value == "prepupa":
        return "PP", "same_as_stage1_corr"
    if stage1_value == "pupa":
        for match in re.finditer(r"\bp(\d{1,2})\b", normalized):
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
            return to_hour_code(hours), "specific_pupa_hour"
        if re.search(r"\bearly pupa\b|\bday ?0\b|\bpupa day 0\b|\bp0\b", normalized):
            return "P0", "specific_pupa_code"
        return "", "blank_no_specific_detail"

    # larva
    if any(token in normalized for token in (";", "|", " and ")) and ("instar" in normalized or "larva" in normalized):
        numbers = extract_instar_numbers(normalized)
        if len(numbers) >= 2:
            return "", "blank_mixed_multi_instar"
    instar_numbers = extract_instar_numbers(normalized)
    if len(instar_numbers) >= 2:
        return "", "blank_mixed_multi_instar"
    if "neonate" in normalized and instar_numbers:
        return "", "blank_mixed_multi_instar"
    if len(instar_numbers) == 1:
        instar = next(iter(instar_numbers))
        if day_range is not None:
            return to_l_day_range_code(instar, *day_range), "specific_instar_day_range"
        if len(day_numbers) == 1:
            return to_l_day_code(instar, next(iter(day_numbers))), "specific_instar_day"
        return to_l_code(instar), "specific_instar"
    if any(
        phrase in normalized
        for phrase in ("last instar", "last inster", "late instar", "ultimate instar", "final stage", "mature larva")
    ):
        return "Last instar", "last_instar"
    if "neonate" in normalized or "post hatch" in normalized:
        if day_range is not None:
            return to_l_day_range_code(1, *day_range), "neonate_to_first_instar_day_range"
        if len(day_numbers) == 1:
            return to_l_day_code(1, next(iter(day_numbers))), "neonate_to_first_instar_day"
        return "L1", "neonate_to_first_instar"
    time_only = resolve_time_only_code()
    return time_only if time_only is not None else ("", "blank_no_specific_detail")


def resolve_stage_from_sources(raw_stage: str, raw_tissue: str) -> tuple[str, str, str, str]:
    stage1_corr, stage_reason = normalize_stage1(raw_stage)
    stage2_corr, stage2_reason = normalize_stage2(raw_stage, stage1_corr)

    tissue_stage1_corr, tissue_stage_reason = normalize_stage1(raw_tissue)
    tissue_stage2_corr, tissue_stage2_reason = normalize_stage2(raw_tissue, tissue_stage1_corr)

    if stage1_corr == "" and tissue_stage1_corr not in {"", "other"}:
        return (
            tissue_stage1_corr,
            tissue_stage2_corr,
            f"fallback_from_tissue:{tissue_stage_reason}",
            f"fallback_from_tissue:{tissue_stage2_reason}",
        )

    if (
        stage1_corr not in {"", "other"}
        and not stage2_corr
        and tissue_stage1_corr == stage1_corr
        and tissue_stage2_corr
    ):
        return (
            stage1_corr,
            tissue_stage2_corr,
            stage_reason,
            f"supplement_from_tissue:{tissue_stage2_reason}",
        )

    return stage1_corr, stage2_corr, stage_reason, stage2_reason


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
        csv_output = input_path

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
    parser.add_argument(
        "--cache-db",
        type=Path,
        default=DEFAULT_CACHE_DB,
        help=f"SQLite cache DB for BioSample backfill before normalization. Default: {DEFAULT_CACHE_DB}",
    )
    parser.add_argument(
        "--synonym-output",
        type=Path,
        default=DEFAULT_SYNONYM_OUTPUT,
        help=f"Synonym-group TSV updated during BioSample backfill. Default: {DEFAULT_SYNONYM_OUTPUT}",
    )
    parser.add_argument(
        "--skip-cache-backfill",
        action="store_true",
        help="Skip the BioSample cache backfill step and only run normalization.",
    )
    parser.add_argument("--in-place", action="store_true", help="Overwrite the input CSV.")
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    csv_output, review_prefix = resolve_output_paths(args.input, args.output, args.review_dir, args.in_place)
    working_input = args.input
    temp_backfill_dir: Path | None = None

    if not args.skip_cache_backfill and args.cache_db.exists():
        from reextract_biosample_fields import (
            load_cached_xml_by_request_accession,
            update_transcriptome_csv,
            write_synonym_groups,
        )

        cached_xml_by_request_accession = load_cached_xml_by_request_accession(args.cache_db)
        write_synonym_groups(args.synonym_output, cached_xml_by_request_accession)

        if args.in_place:
            update_transcriptome_csv(args.input, cached_xml_by_request_accession)
        else:
            temp_backfill_dir = Path(tempfile.mkdtemp(prefix="normalize_sra_backfill_"))
            working_input = temp_backfill_dir / args.input.name
            shutil.copy2(args.input, working_input)
            update_transcriptome_csv(working_input, cached_xml_by_request_accession)

    temp_csv = csv_output.with_suffix(csv_output.suffix + ".tmp")

    tissue_review: Counter[tuple[str, str, str, str]] = Counter()
    sex_review: Counter[tuple[str, str, str]] = Counter()
    stage_review: Counter[tuple[str, str, str, str, str]] = Counter()

    with working_input.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {working_input}")

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
        if "Geographic location" in output_fields:
            output_fields = insert_after(output_fields, "Geographic location", ["Geographic coordinates"])
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
                sex_corr, sex_reason = resolve_sex_from_sources(raw_sex, raw_tissue, raw_stage)
                stage1_corr, stage2_corr, stage_reason, stage2_reason = resolve_stage_from_sources(
                    raw_stage,
                    raw_tissue,
                )

                row["Tissue1.Corr"] = tissue1_corr
                row["Tissue2.Corr"] = tissue2_corr
                row["Sex.Corr"] = sex_corr
                row["Stage1.Corr"] = stage1_corr
                row["Stage2.Corr"] = stage2_corr
                row["Collection Date"] = normalize_collection_date(row.get("Collection Date", ""))
                row["Geographic location"] = normalize_geographic_location(row.get("Geographic location", ""))
                row["Geographic coordinates"] = normalize_geographic_coordinates(backfill_geographic_coordinates(row))
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
    if temp_backfill_dir is not None:
        shutil.rmtree(temp_backfill_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
