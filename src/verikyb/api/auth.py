ROLES = ("analyst", "reviewer", "admin")


def permission_denied_message(role: str) -> str:
    article = "an" if role[:1].lower() in "aeiou" else "a"
    return f"{article} {role} cannot do this"
