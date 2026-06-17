import re


def strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace. Drops script/style content entirely."""
    html = re.sub(
        r"<(script|style|head)[^>]*>.*?</\1>", " ", html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()
