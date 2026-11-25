"""Format and check-digit rules for the identifiers in the synthetic data.

Test values must be well formed, or the pipeline would be tested on data no registry issues.
"""

import re

ALPHANUMERIC = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
UK_COMPANY_NUMBER = re.compile(r"\d{8}")
FRN = re.compile(r"\d{6,7}")
CIN = re.compile(r"[LU]\d{5}[A-Z]{2}\d{4}(PTC|PLC|OPC|NPL)\d{6}")  # listing, NIC, state, year...
COMPANY_PAN = re.compile(r"[A-Z]{3}C[A-Z]\d{4}[A-Z]")  # fourth letter C marks a company
DIN = re.compile(r"\d{8}")


def lei_is_valid(lei: str) -> bool:
    """ISO 17442: 20 characters whose ISO 7064 MOD 97-10 remainder is 1."""
    if not re.fullmatch(r"[A-Z0-9]{18}\d{2}", lei):
        return False
    return int("".join(str(ALPHANUMERIC.index(char)) for char in lei)) % 97 == 1


def gstin_is_valid(gstin: str, pan: str, state_code: str) -> bool:
    """State code + PAN + entity number + Z + a mod-36 check character."""
    if len(gstin) != 15 or gstin[:2] != state_code or gstin[2:12] != pan or gstin[13] != "Z":
        return False
    total = 0
    for position, char in enumerate(gstin[:14]):
        product = ALPHANUMERIC.index(char) * (2 if position % 2 else 1)
        total += product // 36 + product % 36
    return gstin[14] == ALPHANUMERIC[(36 - total % 36) % 36]
