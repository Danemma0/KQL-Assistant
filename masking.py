"""Sensitive-data masking for outbound API calls.

Masks emails/UPNs, IPv4, IPv6, hostnames/FQDNs, and DOMAIN\\user patterns.
KQL code blocks (```...```) are left untouched so queries stay valid.

Each unique value gets a consistent, reversible token within a session:
  EMAIL_1, IP_2, HOST_3, USER_4, CUSTOM_5, …

Usage:
    session_map = {}
    masked = mask(text, session_map, extra_terms=["ProjectX", "Alice"])
    reply  = call_claude(masked)
    clean  = unmask(reply, session_map)
"""
import re

# ---------------------------------------------------------------------------
# Compiled patterns — ordered from most specific to least specific so that
# email addresses are captured before their domain is matched as a hostname.
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b')

_DOMAIN_USER = re.compile(
    r'\b[A-Za-z][A-Za-z0-9\-]{0,15}\\[a-zA-Z0-9._\-]+\b'
)

_IPV4 = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}'
    r'(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
)

# Conservative IPv6: full 8-group, compressed (::), or link-local.
_IPV6 = re.compile(
    r'(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}'
    r'|(?:[0-9a-fA-F]{1,4}:){1,7}:'
    r'|::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}'
    r'|::1\b'
    r'|fe80(?::[0-9a-fA-F]{0,4}){0,4}(?:%[0-9a-zA-Z]+)?'
)

# Hostnames / FQDNs with at least one dot and a known TLD/suffix.
_FQDN = re.compile(
    r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.){1,}'
    r'(?:com|org|net|edu|gov|mil|biz|info|local|corp|internal|test|dev|'
    r'io|co|ai|cloud|onmicrosoft|windows|azure|office|sharepoint)\b',
    re.IGNORECASE,
)

# Sentinel ``` blocks — content inside is never touched.
_KQL_BLOCK = re.compile(r'```[\s\S]*?```')

# Token pattern — used by unmask to find tokens in Claude's reply.
_TOKEN_RE = re.compile(r'\b(?:EMAIL|IP|HOST|USER|CUSTOM)_\d+\b')


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_token(value: str, prefix: str, session_map: dict) -> str:
    """Return the existing token for *value*, or mint a new one."""
    v2t: dict = session_map.setdefault('v2t', {})
    if value not in v2t:
        cnt: dict = session_map.setdefault('cnt', {})
        cnt[prefix] = cnt.get(prefix, 0) + 1
        v2t[value] = f'{prefix}_{cnt[prefix]}'
    return v2t[value]


def _replace_with(pattern: re.Pattern, prefix: str, text: str, sm: dict) -> str:
    return pattern.sub(lambda m: _get_token(m.group(0), prefix, sm), text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def mask(text: str, session_map: dict, extra_terms: list | None = None) -> str:
    """Mask PII/sensitive values in *text* and return the masked string.

    *session_map* is updated in place with every new token created.
    Pass *extra_terms* for custom values that should always be masked
    (e.g. a project code-name or a known employee surname).
    KQL code fences (```…```) are preserved verbatim.
    """
    # 1. Protect KQL blocks so their content is never touched.
    blocks: list[str] = []

    def _save_block(m: re.Match) -> str:
        blocks.append(m.group(0))
        return f'\x00KQLB{len(blocks) - 1}\x00'

    text = _KQL_BLOCK.sub(_save_block, text)

    # 2. Custom exact-match terms (case-sensitive).
    for term in (extra_terms or []):
        term = term.strip()
        if term and term in text:
            token = _get_token(term, 'CUSTOM', session_map)
            text = text.replace(term, token)

    # 3. Apply regexes most-specific first.
    text = _replace_with(_EMAIL, 'EMAIL', text, session_map)
    text = _replace_with(_DOMAIN_USER, 'USER', text, session_map)
    text = _replace_with(_IPV4, 'IP', text, session_map)
    text = _replace_with(_IPV6, 'IP', text, session_map)
    text = _replace_with(_FQDN, 'HOST', text, session_map)

    # 4. Restore KQL blocks unchanged.
    for i, block in enumerate(blocks):
        text = text.replace(f'\x00KQLB{i}\x00', block)

    return text


def unmask(text: str, session_map: dict) -> str:
    """Replace every token in *text* with its original value.

    Safe to call on partial chunks from a streaming response — tokens that
    appear in full will be replaced; partial tokens are left as-is and must
    be carried in a buffer (see *stream_unmask*).
    """
    v2t: dict = session_map.get('v2t', {})
    # Longest tokens first to avoid partial matches (e.g. EMAIL_10 vs EMAIL_1).
    for orig, token in sorted(v2t.items(), key=lambda x: -len(x[1])):
        text = text.replace(token, orig)
    return text


def stream_unmask(chunks, session_map: dict):
    """Generator: unmask a stream of text chunks safely.

    Buffers up to *_MAX_TOKEN_LEN* characters at the tail so a token that
    spans two consecutive chunks is never split mid-replacement.
    """
    _MAX_TOKEN_LEN = 32  # longest possible token: CUSTOM_999 is 10 chars; headroom is fine
    buf = ''
    for chunk in chunks:
        buf += chunk
        if len(buf) > _MAX_TOKEN_LEN:
            safe, buf = buf[:-_MAX_TOKEN_LEN], buf[-_MAX_TOKEN_LEN:]
            yield unmask(safe, session_map)
    if buf:
        yield unmask(buf, session_map)


def count_masked(session_map: dict) -> int:
    """Return the number of unique values masked in this session."""
    return len(session_map.get('v2t', {}))


def preview(text: str, session_map: dict, extra_terms: list | None = None) -> dict:
    """Mask *text* on a temporary copy of *session_map* and return info
    without committing new tokens to the real session.

    Returns:
        {
          "masked_text": str,
          "count": int,          # unique values that would be masked
          "new_count": int,      # newly masked values not yet in session_map
        }
    """
    import copy
    temp_map: dict = copy.deepcopy(session_map)
    before = count_masked(session_map)
    masked_text = mask(text, temp_map, extra_terms)
    after = count_masked(temp_map)
    return {
        'masked_text': masked_text,
        'count': after,
        'new_count': after - before,
    }
