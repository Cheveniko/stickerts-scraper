from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "scraper-config.json"
STICKERS_PATH = BASE_DIR / "stickers.json"

CARDS_MARKER = '\\"cards\\":['
SECTIONS_MARKER = '\\"sections\\":['
PREFIXED_CARD_NUMBER_RE = re.compile(r"(?P<prefix>[A-Z]+)\s*(?P<number>\d+)")

INTRO_LABELS = {
    "PANINI LOGO",
    "WC LOGO",
    "OFFICIAL MASCOTS",
    "OFFICIAL SLOGAN",
    "OFFICIAL BALL",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fetch_html(url: str) -> str:
    response = httpx.get(url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def extract_embedded_array(html: str, marker: str) -> list[dict[str, Any]]:
    marker_index = html.find(marker)
    if marker_index == -1:
        raise ValueError(f"Could not find marker {marker!r} in HTML")

    start_index = html.find("[", marker_index)
    if start_index == -1:
        raise ValueError(f"Could not find array start after marker {marker!r}")

    depth = 0

    for end_index in range(start_index, len(html)):
        char = html[end_index]

        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                raw_array = html[start_index : end_index + 1]
                decoded_array = raw_array.replace('\\"', '"').replace("\\/", "/")
                return json.loads(decoded_array)

    raise ValueError(f"Could not find array end for marker {marker!r}")


def normalize_code(section: str, raw_card_number: str, prefix: str) -> tuple[str, int]:
    raw_card_number = raw_card_number.strip()

    if section == "PANINI":
        if raw_card_number != "00":
            raise ValueError(f"Unexpected PANINI card number: {raw_card_number!r}")
        return "00", 0

    if raw_card_number.isdigit():
        sticker_number = int(raw_card_number)
        return f"{prefix}-{sticker_number}", sticker_number

    match = PREFIXED_CARD_NUMBER_RE.fullmatch(raw_card_number)
    if match is None:
        raise ValueError(f"Unsupported card number format: {raw_card_number!r}")

    raw_prefix = match.group("prefix")
    sticker_number = int(match.group("number"))
    if raw_prefix != prefix:
        raise ValueError(
            f"Card number prefix mismatch for section {section!r}: expected {prefix!r}, got {raw_prefix!r}"
        )

    return f"{prefix}-{sticker_number}", sticker_number


def infer_type(section: str, label: str) -> str:
    if section == "WORLD CUP HISTORY":
        return "history"

    if label == "BADGE":
        return "emblem"

    if label == "SQUAD":
        return "squad"

    if label in INTRO_LABELS or label.endswith("HOST COUNTRY EMBLEM"):
        return "intro"

    if label.strip():
        return "player"

    return "unknown"


def build_sticker(
    card: dict[str, Any],
    section_names_by_id: dict[int, str],
    section_abbreviations: dict[str, str],
) -> dict[str, Any]:
    section_id = card["section_id"]
    section = section_names_by_id.get(section_id)
    if section is None:
        raise ValueError(f"Unknown section id: {section_id}")

    prefix = section_abbreviations.get(section, "").strip()
    if not prefix:
        raise ValueError(f"Missing section abbreviation for {section!r}")

    code, sticker_number = normalize_code(section, card["card_number"], prefix)
    label = card["player_name"].strip()

    return {
        "code": code,
        "label": label,
        "section": section,
        "type": infer_type(section, label),
        "sticker_number": sticker_number,
        "album_page": None,
    }


def validate_stickers(
    stickers: list[dict[str, Any]],
    expected_total_items: int,
    section_names: set[str],
    section_abbreviations: dict[str, str],
) -> None:
    if len(stickers) != expected_total_items:
        raise ValueError(f"Expected {expected_total_items} stickers, got {len(stickers)}")

    duplicate_codes = [code for code, count in Counter(sticker["code"] for sticker in stickers).items() if count > 1]
    if duplicate_codes:
        preview = ", ".join(sorted(duplicate_codes)[:10])
        raise ValueError(f"Found duplicate sticker codes: {preview}")

    missing_sections = sorted(section_names - set(section_abbreviations))
    if missing_sections:
        raise ValueError(f"Missing sections in config: {', '.join(missing_sections)}")

    blank_abbreviations = sorted(section for section in section_names if not section_abbreviations.get(section, "").strip())
    if blank_abbreviations:
        raise ValueError(f"Blank section abbreviations in config: {', '.join(blank_abbreviations)}")


def scrape_stickers(config_path: Path = CONFIG_PATH, output_path: Path = STICKERS_PATH) -> None:
    config = load_json(config_path)
    html = fetch_html(config["source_url"])

    cards = extract_embedded_array(html, CARDS_MARKER)
    sections = extract_embedded_array(html, SECTIONS_MARKER)

    section_names_by_id = {section["id"]: section["name"] for section in sections}
    section_names = set(section_names_by_id.values())
    section_abbreviations = config["section_abbreviations"]

    stickers = [
        build_sticker(card, section_names_by_id, section_abbreviations)
        for card in cards
    ]

    validate_stickers(stickers, config["expected_total_items"], section_names, section_abbreviations)

    write_json(output_path, stickers)

    config["scraped_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    write_json(config_path, config)


def main() -> None:
    scrape_stickers()
    print(f"Wrote stickers to {STICKERS_PATH.name} and updated {CONFIG_PATH.name}")


if __name__ == "__main__":
    main()
