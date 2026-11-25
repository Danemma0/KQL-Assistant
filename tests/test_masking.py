"""Unit tests for masking.py — mask/unmask round-trips and edge cases."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from masking import mask, unmask, stream_unmask, count_masked, preview


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fresh():
    """Return a new empty session_map."""
    return {}


def roundtrip(text, extra_terms=None):
    """Mask then unmask; returned text must equal the original."""
    sm = fresh()
    masked = mask(text, sm, extra_terms)
    restored = unmask(masked, sm)
    return masked, restored


# ---------------------------------------------------------------------------
# Email / UPN
# ---------------------------------------------------------------------------

def test_email_basic():
    masked, restored = roundtrip("User is john.doe@contoso.com.")
    assert "john.doe@contoso.com" not in masked
    assert "EMAIL_1" in masked
    assert restored == "User is john.doe@contoso.com."


def test_email_upn():
    text = "Alert for alice_smith@corp.onmicrosoft.com accessing files."
    masked, restored = roundtrip(text)
    assert "@corp.onmicrosoft.com" not in masked
    assert restored == text


def test_multiple_emails_get_distinct_tokens():
    sm = fresh()
    text = "From a@x.com to b@x.com cc c@x.com."
    masked = mask(text, sm)
    assert "EMAIL_1" in masked
    assert "EMAIL_2" in masked
    assert "EMAIL_3" in masked
    assert unmask(masked, sm) == text


def test_same_email_same_token():
    sm = fresh()
    text = "user@example.com contacted user@example.com again."
    masked = mask(text, sm)
    tokens = set(t for t in masked.split() if t.startswith("EMAIL_"))
    assert len(tokens) == 1  # one unique email → one token
    assert unmask(masked, sm) == text


# ---------------------------------------------------------------------------
# IPv4
# ---------------------------------------------------------------------------

def test_ipv4_basic():
    masked, restored = roundtrip("Connected from 192.168.1.42.")
    assert "192.168.1.42" not in masked
    assert "IP_1" in masked
    assert restored == "Connected from 192.168.1.42."


def test_ipv4_inside_url():
    text = "Alert from http://10.0.0.5/api/v1"
    sm = fresh()
    masked = mask(text, sm)
    assert "10.0.0.5" not in masked
    assert unmask(masked, sm) == text


def test_multiple_ips():
    text = "Traffic from 10.1.1.1 to 10.1.1.2."
    masked, restored = roundtrip(text)
    assert "10.1.1.1" not in masked
    assert "10.1.1.2" not in masked
    assert restored == text


def test_same_ip_same_token():
    sm = fresh()
    text = "10.0.0.1 and 10.0.0.1 again."
    masked = mask(text, sm)
    parts = [p for p in masked.split() if p.startswith("IP_")]
    assert len(parts) == 2 and parts[0] == parts[1]


# ---------------------------------------------------------------------------
# DOMAIN\user
# ---------------------------------------------------------------------------

def test_domain_user():
    text = r"Login by CORP\jsmith."
    masked, restored = roundtrip(text)
    assert r"CORP\jsmith" not in masked
    assert "USER_1" in masked
    assert restored == text


# ---------------------------------------------------------------------------
# Hostname / FQDN
# ---------------------------------------------------------------------------

def test_fqdn():
    text = "Host machine01.corp.internal triggered the alert."
    masked, restored = roundtrip(text)
    assert "machine01.corp.internal" not in masked
    assert "HOST_1" in masked
    assert restored == text


# ---------------------------------------------------------------------------
# KQL blocks are preserved
# ---------------------------------------------------------------------------

def test_kql_block_untouched():
    text = (
        "Check this query:\n"
        "```kql\n"
        "DeviceNetworkEvents\n"
        "| where RemoteIP == '10.0.0.1'\n"
        "```\n"
        "The IP 10.0.0.1 is suspicious."
    )
    sm = fresh()
    masked = mask(text, sm)
    # IP in prose is masked
    assert "10.0.0.1" not in masked.split("```")[0]
    # KQL block is preserved verbatim
    kql_part = masked.split("```kql\n")[1].split("```")[0]
    assert "10.0.0.1" in kql_part
    assert unmask(masked, sm) == text


def test_multiple_kql_blocks():
    text = "```kql\nSignInLogs | where UserPrincipalName == 'a@test.com'\n``` then ```kql\nAuditLogs\n```"
    sm = fresh()
    masked = mask(text, sm)
    # Email inside KQL block is NOT masked
    assert "a@test.com" in masked
    # No tokens for it
    assert count_masked(sm) == 0


# ---------------------------------------------------------------------------
# Custom (always-mask) terms
# ---------------------------------------------------------------------------

def test_custom_terms():
    sm = fresh()
    text = "Project Nebula alert for Nebula team."
    masked = mask(text, sm, extra_terms=["Nebula"])
    assert "Nebula" not in masked
    assert "CUSTOM_1" in masked
    assert unmask(masked, sm) == text


def test_custom_term_repeated():
    sm = fresh()
    text = "Alpha Alpha Alpha"
    masked = mask(text, sm, extra_terms=["Alpha"])
    assert masked == "CUSTOM_1 CUSTOM_1 CUSTOM_1"
    assert unmask(masked, sm) == text


# ---------------------------------------------------------------------------
# Token persistence across calls (same session_map)
# ---------------------------------------------------------------------------

def test_token_persists_across_calls():
    sm = fresh()
    mask("user@example.com appeared.", sm)
    masked2 = mask("Again: user@example.com", sm)
    # Must reuse EMAIL_1, not create EMAIL_2
    assert "EMAIL_1" in masked2
    assert count_masked(sm) == 1


# ---------------------------------------------------------------------------
# stream_unmask
# ---------------------------------------------------------------------------

def test_stream_unmask_no_split():
    sm = fresh()
    text = "Alert for user@example.com."
    masked = mask(text, sm)
    chunks = list(masked)  # character-by-character worst case
    result = "".join(stream_unmask(chunks, sm))
    assert result == text


def test_stream_unmask_token_at_boundary():
    sm = fresh()
    text = "IP is 192.168.1.1 today."
    masked = mask(text, sm)
    # Split right in the middle of "IP_1"
    mid = masked.index("IP_")
    chunks = [masked[:mid + 1], masked[mid + 1:]]
    result = "".join(stream_unmask(chunks, sm))
    assert result == text


# ---------------------------------------------------------------------------
# preview — does not commit to session_map
# ---------------------------------------------------------------------------

def test_preview_no_mutation():
    sm = fresh()
    text = "user@example.com and 10.0.0.1"
    info = preview(text, sm)
    assert info["new_count"] == 2
    # Real session_map must be unchanged
    assert count_masked(sm) == 0


def test_preview_counts_correctly():
    sm = fresh()
    mask("user@example.com", sm)   # pre-existing
    text = "user@example.com and 10.0.0.1"
    info = preview(text, sm)
    assert info["count"] == 2       # total in temp_map
    assert info["new_count"] == 1   # only IP is new


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_string():
    masked, restored = roundtrip("")
    assert masked == ""
    assert restored == ""


def test_no_pii():
    text = "This alert involves no usernames or IPs."
    sm = fresh()
    masked = mask(text, sm)
    assert masked == text
    assert count_masked(sm) == 0


def test_already_masked_text_not_double_masked():
    sm = fresh()
    text = "user@example.com"
    m1 = mask(text, sm)       # → "EMAIL_1"
    m2 = mask(m1, sm)         # should stay "EMAIL_1" (not EMAIL_N again)
    assert m1 == m2
    assert count_masked(sm) == 1
