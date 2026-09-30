import re

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize(text: str) -> str:
    text = (text or "").translate(ARABIC_DIGITS).strip().lower()
    text = text.replace("ـ", "")
    text = re.sub(r"\s+", " ", text)
    return text


def parse_duration(text: str) -> tuple[int, str] | None:
    """Return (seconds, human_arabic) or None.

    Supported examples: ساعة، ساعتين، 30 دقيقة، نص ساعة، 2h، 1d، 1w.
    Telegram treats restrictions shorter than 30 seconds or longer than 366 days
    as forever, so this parser intentionally keeps durations in a safe range.
    """
    t = normalize(text)
    if not t:
        return None

    fixed = {
        "نص ساعة": (30 * 60, "30 دقيقة"),
        "نصف ساعة": (30 * 60, "30 دقيقة"),
        "دقيقة": (60, "دقيقة واحدة"),
        "دقيقتين": (2 * 60, "دقيقتين"),
        "دقيقتان": (2 * 60, "دقيقتين"),
        "ساعة": (60 * 60, "ساعة واحدة"),
        "ساعه": (60 * 60, "ساعة واحدة"),
        "ساعتين": (2 * 60 * 60, "ساعتين"),
        "ساعتان": (2 * 60 * 60, "ساعتين"),
        "يوم": (24 * 60 * 60, "يوم واحد"),
        "يومين": (2 * 24 * 60 * 60, "يومين"),
        "اسبوع": (7 * 24 * 60 * 60, "أسبوع واحد"),
        "أسبوع": (7 * 24 * 60 * 60, "أسبوع واحد"),
        "اسبوعين": (14 * 24 * 60 * 60, "أسبوعين"),
        "أسبوعين": (14 * 24 * 60 * 60, "أسبوعين"),
    }
    if t in fixed:
        return fixed[t]

    compact = re.fullmatch(r"(\d+)\s*([mhdw])", t)
    if compact:
        n = int(compact.group(1))
        unit = compact.group(2)
        factors = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
        labels = {"m": "دقيقة", "h": "ساعة", "d": "يوم", "w": "أسبوع"}
        seconds = n * factors[unit]
        if 60 <= seconds <= 365 * 86400:
            return seconds, f"{n} {labels[unit]}"
        return None

    m = re.fullmatch(
        r"(\d+)\s*(دقيقه|دقيقة|دقائق|ساعة|ساعه|ساعات|يوم|ايام|أيام|اسبوع|أسبوع|اسابيع|أسابيع)",
        t,
    )
    if not m:
        return None

    n = int(m.group(1))
    unit = m.group(2)
    if unit in {"دقيقه", "دقيقة", "دقائق"}:
        seconds, label = n * 60, "دقيقة"
    elif unit in {"ساعة", "ساعه", "ساعات"}:
        seconds, label = n * 3600, "ساعة"
    elif unit in {"يوم", "ايام", "أيام"}:
        seconds, label = n * 86400, "يوم"
    else:
        seconds, label = n * 604800, "أسبوع"

    if not 60 <= seconds <= 365 * 86400:
        return None
    return seconds, f"{n} {label}"


def extract_self_mute_duration(text: str) -> tuple[int, str] | None:
    t = normalize(text)
    prefix = "برايد اكتمني"
    if not t.startswith(prefix):
        return None
    rest = t[len(prefix):].strip()
    return parse_duration(rest)
