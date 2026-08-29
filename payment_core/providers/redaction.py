from collections.abc import Mapping, Sequence

REDACTED = "<redacted>"
_SENSITIVE_KEY_FRAGMENTS = frozenset({
    "authorization", "token", "secret", "password", "merchant", "card", "pan", "cvv",
})


def _is_sensitive_key(key):
    normalized = str(key).lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def sanitize_provider_data(value, *, max_depth=6, max_string_length=500):
    def clean(item, depth):
        if depth > max_depth:
            return "<max-depth>"
        if isinstance(item, Mapping):
            return {
                str(key): REDACTED if _is_sensitive_key(key) else clean(child, depth + 1)
                for key, child in item.items()
            }
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return [clean(child, depth + 1) for child in item]
        if isinstance(item, str) and len(item) > max_string_length:
            return item[:max_string_length] + "..."
        if item is None or isinstance(item, (str, int, float, bool)):
            return item
        return str(item)

    return clean(value, 0)
