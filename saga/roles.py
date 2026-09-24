"""Shared normalization for category-specific role names."""


def name_key(name):
    return " ".join(name.split()).casefold()
