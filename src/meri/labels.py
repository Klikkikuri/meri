"""
Kubernetes-inspired Label Selector DSL parser and canonical label matching engine.

Supports:
  - Presence: 'paywalled', 'com.github.klikkikuri/paywalled'
  - Absence: '!paywalled'
  - Equality: 'paywalled=true', 'article-type = opinion', 'sponsored == true'
  - Inequality: 'article-type != opinion-analysis'
  - Set membership: 'article-type in (opinion, analysis, review)'
  - Set non-membership: 'article-type notin (article, feature)'
  - Comma-separated requirements (AND logic): 'paywalled=true, article-type = opinion'
"""

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Iterable, Literal, Protocol


class InvalidLabelSelectorError(ValueError):
    """Raised when a label selector expression has invalid syntax."""

    pass


@dataclass(frozen=True)
class Label:
    """
    Canonical representation of a single label key-value pair.
    """

    full_key: str
    short_key: str
    value: str

    @classmethod
    def parse(cls, label_str: str) -> "Label":
        """
        Parse a raw label string or enum into a canonical Label object.

        :param label_str: String representation of a label.
        :return: Label instance with full_key, short_key, and value.
        :raises ValueError: If label string or key is empty/malformed.
        """
        s = str(label_str).strip()
        if not s:
            raise ValueError("Label string cannot be empty")

        if "=" in s:
            k, v = s.split("=", 1)
            k, v = k.strip(), v.strip()
        else:
            k, v = s, "true"

        if not k or k.startswith("/") or k.endswith("/"):
            raise ValueError(f"Invalid label key: {label_str!r}")

        short_key = k.split("/", 1)[1].strip() if "/" in k else k.strip()
        if not short_key:
            raise ValueError(f"Invalid label with empty short_key: {label_str!r}")

        return cls(full_key=k, short_key=short_key, value=v)


class LabelSet:
    """
    Canonical collection of an article's labels.
    """

    def __init__(self, raw_labels: Iterable[str | Enum]):
        self.labels: list[Label] = []
        for raw in raw_labels:
            val = raw.value if isinstance(raw, Enum) else str(raw)
            self.labels.append(Label.parse(val))

    def get_values(self, key_query: str) -> list[str]:
        """
        Lookup label values for a given key query.

        Explicit lookup rules & short-key collision semantics:
          - If key_query contains '/', match strictly against full_key.
          - If key_query does not contain '/', match explicitly against short_key across all namespaces.
          - When multiple labels share the same short_key (short-key collision), all matching values
            are collected and sorted deterministically by (full_key, value).
          - Requirement evaluation semantics on collisions:
            * Equality (key = val): True if ANY matching label value equals val.
            * Inequality (key != val): True if NO matching label value equals val.
            * Set membership (key in (...)): True if ANY matching label value is in the set.
            * Set non-membership (key notin (...)): True if NO matching label value is in the set.

        :param key_query: Label key to query.
        :return: Deterministically sorted list of values associated with the matching labels.
        """
        if "/" in key_query:
            matched = [lbl for lbl in self.labels if lbl.full_key == key_query]
        else:
            matched = [lbl for lbl in self.labels if lbl.short_key == key_query]

        matched_sorted = sorted(matched, key=lambda lbl: (lbl.full_key, lbl.value))
        return [lbl.value for lbl in matched_sorted]

    def has_key(self, key_query: str) -> bool:
        """
        Check if any label matches the given key query.

        :param key_query: Label key to check for presence.
        :return: True if at least one matching label exists, False otherwise.
        """
        return len(self.get_values(key_query)) > 0


class Requirement(Protocol):
    """Protocol for label selector requirements."""

    def matches(self, label_set: LabelSet) -> bool:
        ...


@dataclass(frozen=True)
class PresenceRequirement:
    """
    Requirement checking key presence or absence in LabelSet.

    - key (e.g. 'sponsored'): True if key is present in LabelSet.
    - !key (e.g. '!sponsored'): True if key is ABSENT from LabelSet.
    Note: !sponsored means key is absent; it does NOT mean key=false.
    """

    key: str
    negated: bool = False

    def matches(self, label_set: LabelSet) -> bool:
        has = label_set.has_key(self.key)
        return not has if self.negated else has


@dataclass(frozen=True)
class EqualityRequirement:
    """
    Requirement checking value equality or inequality.

    - 'key = val' or 'key == val': True if a matching label value equals val.
    - 'key != val': True if no matching label value equals val.
    """

    key: str
    operator: Literal["=", "==", "!="]
    value: str

    def matches(self, label_set: LabelSet) -> bool:
        values = label_set.get_values(self.key)
        if self.operator in ("=", "=="):
            return self.value in values
        else:
            return self.value not in values


@dataclass(frozen=True)
class SetRequirement:
    """
    Requirement checking set membership or non-membership.

    - 'key in (v1, v2)': True if any matching label value is in values.
    - 'key notin (v1, v2)': True if no matching label value is in values.
    """

    key: str
    operator: Literal["in", "notin"]
    values: frozenset[str]

    def matches(self, label_set: LabelSet) -> bool:
        values = label_set.get_values(self.key)
        if self.operator == "in":
            return any(v in self.values for v in values)
        else:
            return not any(v in self.values for v in values)


def parse_single_requirement(req_str: str) -> Requirement:
    """
    Parse a single requirement string into a Requirement instance.

    :param req_str: Single requirement expression string.
    :return: Requirement instance.
    :raises InvalidLabelSelectorError: If the expression has invalid syntax.
    """
    s = req_str.strip()
    if not s:
        raise InvalidLabelSelectorError("Empty requirement string")

    # 1. Set operators: "notin" or "in"
    tokens = s.split()
    for op_name in ("notin", "in"):
        if op_name in tokens:
            op_idx = tokens.index(op_name)
            key = " ".join(tokens[:op_idx]).strip()
            val_part = " ".join(tokens[op_idx + 1 :]).strip()

            if not key:
                raise InvalidLabelSelectorError(f"Missing key in set requirement: {req_str!r}")
            if not (val_part.startswith("(") and val_part.endswith(")")):
                raise InvalidLabelSelectorError(f"Set requirement values must be enclosed in parentheses: {req_str!r}")

            inner = val_part[1:-1].strip()
            if not inner:
                raise InvalidLabelSelectorError(f"Empty values in set requirement: {req_str!r}")

            vals = frozenset(v.strip() for v in inner.split(",") if v.strip())
            set_op: Literal["in", "notin"] = "notin" if op_name == "notin" else "in"
            return SetRequirement(key=key, operator=set_op, values=vals)

    # 2. Equality operators: "!=", "==", "=" (longest first)
    for op in ("!=", "==", "="):
        if op in s:
            key, val = s.split(op, 1)
            key, val = key.strip(), val.strip()
            if not key or not val:
                raise InvalidLabelSelectorError(f"Invalid key or value in equality requirement: {req_str!r}")
            if any(c in key for c in ("=", "!", "<", ">", "~", " ")):
                raise InvalidLabelSelectorError(f"Invalid key syntax in equality requirement: {req_str!r}")
            if val.startswith(("=", "!", "<", ">", "~")):
                raise InvalidLabelSelectorError(f"Invalid leading operator character in value for requirement: {req_str!r}")
            eq_op: Literal["=", "==", "!="] = "!=" if op == "!=" else ("==" if op == "==" else "=")
            return EqualityRequirement(key=key, operator=eq_op, value=val)

    # 3. Presence/absence requirement
    if s.startswith("!"):
        key = s[1:].strip()
        if not key or "!" in key or " " in key:
            raise InvalidLabelSelectorError(f"Invalid negation key in requirement: {req_str!r}")
        return PresenceRequirement(key=key, negated=True)
    else:
        if " " in s:
            raise InvalidLabelSelectorError(f"Invalid requirement syntax: {req_str!r}")
        return PresenceRequirement(key=s, negated=False)


def split_selector_requirements(expression: str) -> list[str]:
    """
    Split a selector expression by commas, ignoring commas inside parentheses.
    Validates parenthesis balancing and raises InvalidLabelSelectorError if parentheses are unmatched.
    Example: 'paywalled=true, article-type in (opinion, review)'
      -> ['paywalled=true', 'article-type in (opinion, review)']
    """
    requirements = []
    current = []
    paren_depth = 0
    for char in expression:
        if char == "(":
            paren_depth += 1
            current.append(char)
        elif char == ")":
            paren_depth -= 1
            if paren_depth < 0:
                raise InvalidLabelSelectorError(f"Unmatched closing parenthesis in selector expression: {expression!r}")
            current.append(char)
        elif char == "," and paren_depth == 0:
            req = "".join(current).strip()
            if req:
                requirements.append(req)
            current = []
        else:
            current.append(char)

    if paren_depth != 0:
        raise InvalidLabelSelectorError(f"Unclosed parenthesis in selector expression: {expression!r}")

    if current:
        req = "".join(current).strip()
        if req:
            requirements.append(req)
    return requirements


@lru_cache(maxsize=128)
def _parse_selector_cached(expression: str) -> "LabelSelector":
    """
    Internal cached parser for label selector expressions.
    """
    if not expression or not expression.strip():
        raise InvalidLabelSelectorError("Label selector expression cannot be empty")

    req_strs = split_selector_requirements(expression)
    if not req_strs:
        raise InvalidLabelSelectorError(f"Invalid empty selector: {expression!r}")

    requirements = [parse_single_requirement(r) for r in req_strs]
    return LabelSelector(expression, requirements)


class LabelSelector:
    """
    Parses and evaluates Kubernetes-style label selector expressions.

    A single selector string contains one or more comma-separated requirements,
    which are evaluated with AND logic.
    """

    def __init__(self, raw_expression: str, requirements: list[Requirement]):
        self.raw_expression = raw_expression
        self.requirements = requirements

    @classmethod
    def parse(cls, expression: str) -> "LabelSelector":
        """
        Parse a comma-separated selector string into a LabelSelector instance.
        Results are cached to avoid re-parsing identical selector strings per article.

        :param expression: Selector expression string.
        :return: LabelSelector instance.
        :raises InvalidLabelSelectorError: If the expression has invalid syntax.
        """
        return _parse_selector_cached(expression)

    def matches(self, label_set: LabelSet) -> bool:
        """
        Evaluate if all requirements in this selector match the given LabelSet.

        :param label_set: LabelSet instance representing an article's labels.
        :return: True if all requirements match (AND logic), False otherwise.
        """
        return all(req.matches(label_set) for req in self.requirements)
