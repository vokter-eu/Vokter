"""Safe FTS5 MATCH construction.

Raw user text must NEVER be handed to an FTS5 MATCH clause: characters like " * :
^ - ( ) and bare words AND / OR / NOT / NEAR are query OPERATORS, so a message such as
`price OR (secret:x)` would either error or match unintended columns. We instead extract
plain word/number tokens and quote each one — a quoted token is a literal phrase, never
an operator — then OR them so any term can hit (recall over precision; the vector arm and
the RRF fusion handle precision).
"""
import re

# Word/number runs, Unicode-aware (\w spans accented letters), so names like "Jordi" and
# ids like "4471" survive while punctuation/operators are dropped.
_TOKEN = re.compile(r"\w+", re.UNICODE)

# Common en+es function words. Dropped BEFORE building the MATCH so a question like
# "what is my favourite colour" doesn't keyword-hit every fact containing "is"/"my" — the
# keyword arm is for CONTENT terms (names, ids, nouns), the vector arm handles the rest.
# Purely a recall/precision filter; it can never affect the P2 gate or safety.
_STOPWORDS = frozenset((
    # English
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am", "do",
    "does", "did", "of", "to", "in", "on", "at", "for", "and", "or", "but", "with",
    "my", "your", "his", "her", "its", "our", "their", "i", "you", "he", "she", "it",
    "we", "they", "me", "him", "us", "them", "this", "that", "these", "those", "what",
    "which", "who", "whom", "whose", "when", "where", "why", "how", "about", "tell",
    "show", "give", "get", "can", "could", "would", "should", "will", "shall", "if",
    "then", "than", "so", "as", "from", "by", "not", "no", "yes", "please",
    # Spanish
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "en",
    "y", "o", "u", "que", "qué", "cual", "cuál", "quien", "quién", "cuando", "cuándo",
    "donde", "dónde", "por", "para", "con", "sin", "es", "son", "era", "fue", "ser",
    "mi", "mis", "tu", "tus", "su", "sus", "te", "se", "lo", "le", "les", "nos", "yo",
    "tú", "él", "ella", "ellos", "ellas", "este", "esta", "esto", "ese", "esa", "como",
    "cómo", "dime", "dile", "sobre", "cuánto", "cuanto",
))


def to_match_query(text: str, max_terms: int = 24) -> str | None:
    """Arbitrary text → a safe FTS5 MATCH expression, or None if it has no usable token
    (the caller then skips the keyword arm entirely). Stopwords are dropped; each surviving
    token is double-quoted so it is treated as a literal, never an FTS5 operator (a stray
    `"` inside a token is doubled)."""
    seen: list[str] = []
    for raw in _TOKEN.findall(text or ""):
        tok = raw.lower()
        if tok in _STOPWORDS:
            continue
        # keep tokens of 2+ chars (words, and multi-digit ids/years like 4471/2026); a
        # bare single char — including a lone digit from "2+2" — is noise, not a keyword.
        if len(tok) > 1 and tok not in seen:
            seen.append(tok)
            if len(seen) >= max_terms:
                break
    if not seen:
        return None
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in seen)
