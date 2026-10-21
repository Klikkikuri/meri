"""
The provider manifest format.
=============================

A manifest is a list of resources. Each resource has a `type`, a consumer reads the types it knows and ignores the
others, and each type has one merge rule for several documents of that type. `docs/provider-manifest.md`
describes the format; `docs/manifest.schema.json` is generated from these models.

The models describe documents as they are written: an `href` stays a string, and nothing here reads one. This
module imports only the standard library and pydantic.
"""

import json
import re
from collections.abc import Callable
from functools import reduce
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    NonNegativeInt,
    Tag,
    WithJsonSchema,
    field_validator,
    model_validator,
)

SCHEMA_URL = "https://raw.githubusercontent.com/Klikkikuri/meri/refs/heads/main/docs/manifest.schema.json"
#: The version of the manifest and `sites.json` formats. Major version 0: the formats are in initial development.
SCHEMA_VERSION = "0.1.0"
MAX_RESOURCES = 32

#: Registered link relations. Any `x-` name is also valid.
LINK_RELS = ("about", "privacy-policy", "terms-of-service", "license", "icon", "x-publisher-policy")

_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.+-]+)?")


def _parse_semver(version: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(version)
    if not match:
        raise ValueError(f"schema_version {version!r} is not a semantic version")
    return int(match[1]), int(match[2]), int(match[3])


def check_major(version: str, major: int) -> str:
    """
    Accept a semantic version with the given major version, as Paatti's `validRahtiData` does for Rahti data.

    :raises ValueError: If `version` is not a semantic version, or has another major version.
    """
    if _parse_semver(version)[0] != major:
        raise ValueError(f"schema_version {version!r} is not supported: major version {major} expected")
    return version


def check_compatible(version: str, supported: str) -> str:
    """
    Accept a semantic version that a reader of `supported` can read, by the caret rule of npm and Cargo.

    The major version must be the same. While the major version is 0 the format is in initial development, so the
    minor version must be the same too: a reader of `0.1.0` reads `0.1.x`, and refuses `0.2.0`.

    :raises ValueError: If `version` is not a semantic version, or is not compatible.
    """
    major, minor, _ = _parse_semver(version)
    supported_major, supported_minor, _ = _parse_semver(supported)
    if major != supported_major or (supported_major == 0 and minor != supported_minor):
        expected = f"0.{supported_minor}.x" if supported_major == 0 else f"{supported_major}.x.x"
        raise ValueError(f"schema_version {version!r} is not supported: {expected} expected")
    return version


def _http_url(value: str) -> str:
    if not re.match(r"https?://[^/]", value):
        raise ValueError(f"not an http(s) URL: {value!r}")
    return value


def _origin(value: str) -> str:
    # No host wildcards: without a Public Suffix List, `*.github.io` cannot be told apart from `*.example.fi`.
    if not re.fullmatch(r"https?://[^/*]+/.*", value):
        raise ValueError(
            f"origin {value!r} must be an http(s) match pattern with an exact host, such as https://www.example.fi/*"
        )
    return value


HttpUrl = Annotated[str, AfterValidator(_http_url), WithJsonSchema({"type": "string", "pattern": "^https?://[^/]"})]
Origin = Annotated[str, AfterValidator(_origin), WithJsonSchema({"type": "string", "pattern": r"^https?://[^/*]+/"})]


class _Document(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: str | None = Field(None, alias="$schema", description="URL of the JSON schema of this document.")


class Link(BaseModel):
    """A typed link, as in Atom and WebFinger. Consumers ignore a `rel` they do not know."""

    rel: Annotated[str, WithJsonSchema({"anyOf": [{"enum": list(LINK_RELS)}, {"type": "string", "pattern": "^x-"}]})]
    href: HttpUrl
    hreflang: str | None = None
    type: str | None = Field(default=None, description="Media type of the link target.")
    title: str | None = Field(default=None, description="Label, used only for a `rel` the consumer has no label for.")


class SiteRule(BaseModel):
    """One headline selector rule."""

    description: str | None = Field(None, description="What the rule matches, and why.")
    container: str
    link: str
    title: str


class Site(BaseModel):
    """The configuration of one site, keyed by its host in :attr:`SitesDocument.sites`."""

    name: str
    description: str | None = Field(None, description="Notes about the site and its configuration.")
    enabled: bool = True
    origins: list[Origin] | None = Field(None, description="Match patterns. Defaults to `https://<key>/*`.")
    links: list[Link] = Field(default_factory=list)
    rules: list[SiteRule] = Field(default_factory=list)

    def effective_origins(self, key: str) -> list[str]:
        return self.origins or [f"https://{key}/*"]


def _unique(first: list, second: list) -> list:
    """The items of `first`, then the items of `second` not seen before. Compared by value, in linear time."""
    seen = {_key(item) for item in first}
    unique = list(first)
    for item in second:
        if (key := _key(item)) not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _key(item: Any) -> str:
    # Pydantic models cannot be hashed, so their JSON is the key.
    return item.model_dump_json() if isinstance(item, BaseModel) else json.dumps(item)


def merge_sites(first: Site, second: Site) -> Site:
    """Merge two entries of one site: `rules`, `origins` and `links` concatenate without duplicates, other fields last wins."""
    return second.model_copy(
        update={
            "rules": _unique(first.rules, second.rules),
            "origins": _unique(first.origins or [], second.origins or []) or None,
            "links": _unique(first.links, second.links),
        }
    )


class SitesDocument(_Document):
    """`sites.json`: site configurations, keyed by host."""

    schema_version: str
    sites: dict[str, Site]

    @field_validator("schema_version")
    @classmethod
    def _compatible(cls, value: str) -> str:
        return check_compatible(value, SCHEMA_VERSION)

    def origins(self) -> set[str]:
        return {origin for key, site in self.sites.items() for origin in site.effective_origins(key)}


def merge_sites_documents(first: SitesDocument, second: SitesDocument) -> SitesDocument:
    sites = dict(first.sites)
    for key, site in second.sites.items():
        sites[key] = merge_sites(sites[key], site) if key in sites else site
    return second.model_copy(update={"sites": sites})


class RulesDocument(BaseModel):
    """
    `rules.json`, Suola's compiled rules. Only the shape is checked here; Suola validates the rules themselves.

    It has no `schema_version`: Suola's schema does not allow one. An incompatible document is found by the error
    Suola returns for it.
    """

    model_config = ConfigDict(extra="allow")

    sites: list[dict[str, Any]]


def merge_rules(first: RulesDocument, second: RulesDocument) -> RulesDocument:
    """Concatenate the site rules. Suola orders them by its own rule weights."""
    return RulesDocument(sites=first.sites + second.sites)


class RahtiDocument(BaseModel):
    """`data.json`, the headline data. Only the envelope is checked here; `meri.rahti.data.RahtiData` is the full model."""

    model_config = ConfigDict(extra="allow")

    schema_version: str
    entries: list[dict[str, Any]]

    @field_validator("schema_version")
    @classmethod
    def _major(cls, value: str) -> str:
        return check_major(value, 0)


def _signs(entry: dict[str, Any]) -> set[str]:
    # The envelope does not check entries, so a malformed one has no signature here; full validation reports it.
    urls = entry.get("urls")
    if not isinstance(urls, list):
        return set()
    return {url["sign"] for url in urls if isinstance(url, dict) and isinstance(url.get("sign"), str)}


def merge_rahti(first: RahtiDocument, second: RahtiDocument) -> RahtiDocument:
    """Last wins per signature: an entry of `second` replaces each entry of `first` that shares a signature with it."""
    replaced = set().union(*(_signs(entry) for entry in second.entries))
    kept = [entry for entry in first.entries if not _signs(entry) & replaced]
    return second.model_copy(update={"entries": kept + second.entries})


#: Declared outside the resource classes, where `type` names their field and not the builtin.
DocumentModel = type[BaseModel]


class Resource(BaseModel):
    """
    The envelope that all resources share.

    Content comes from `href`, from the inline key of the type, or from both. The fetched document comes first and
    the inline content last, combined by the merge rule of the type.
    """

    type: str
    href: str | Annotated[list[str], Field(min_length=1)] | None = Field(
        None,
        description="Location of the document, relative to the containing document. A list names several "
        "documents, merged in list order.",
    )
    description: str | None = None
    weight: NonNegativeInt = Field(
        0, description="Precedence: lower weights apply first, equal weights in document order, and later wins."
    )

    inline_key: ClassVar[str | None] = None
    document: ClassVar[DocumentModel]
    merge: ClassVar[Callable[[Any, Any], Any]]

    @property
    def hrefs(self) -> list[str]:
        return [self.href] if isinstance(self.href, str) else list(self.href or [])

    def inline(self) -> BaseModel | None:
        """The inline content as a document of the type."""
        return getattr(self, self.inline_key) if self.inline_key else None

    def combine(self, documents: list[BaseModel]) -> BaseModel | None:
        """Merge the fetched `documents` in order, and then the inline content, by the merge rule of the type."""
        inline = self.inline()
        merged = [*documents, inline] if inline is not None else documents
        return reduce(type(self).merge, merged) if merged else None

    @model_validator(mode="after")
    def _has_content(self):
        if self.inline_key and not self.hrefs and getattr(self, self.inline_key) is None:
            raise ValueError(f"a {self.type!r} resource needs `href`, `{self.inline_key}`, or both")
        return self


def describe(resource: Resource) -> str:
    """Name a resource in a message. There is no `id`: the type and the href identify a resource."""
    return f"{resource.type} resource {resource.href or '(inline)'}"


class SuolaRulesResource(Resource):
    """URL normalization rules for Suola. Several resources append in precedence order."""

    type: Literal["suola-rules"]  # pyright: ignore[reportIncompatibleVariableOverride]
    rules: RulesDocument | None = None

    inline_key = "rules"
    document = RulesDocument
    merge = staticmethod(merge_rules)


class RahtiResource(Resource):
    """The headline data set. Several resources merge per signature, last wins."""

    type: Literal["rahti-data"]  # pyright: ignore[reportIncompatibleVariableOverride]
    data: RahtiDocument | None = None

    inline_key = "data"
    document = RahtiDocument
    merge = staticmethod(merge_rahti)


class SitesResource(Resource):
    """Site configurations. Several resources merge per site key."""

    type: Literal["site-configs"]  # pyright: ignore[reportIncompatibleVariableOverride]
    sites: dict[str, Site] | None = Field(None, description="Inline site configurations, keyed by host.")
    origins: list[Origin] | None = Field(
        None,
        description="The site origins that a cross-origin `href` may contribute. Required for a cross-origin "
        "`href`, and not allowed otherwise.",
    )

    inline_key = "sites"
    document = SitesDocument
    merge = staticmethod(merge_sites_documents)

    def inline(self) -> SitesDocument | None:
        if self.sites is None:
            return None
        return SitesDocument.model_validate({"schema_version": SCHEMA_VERSION, "sites": self.sites})


class UnknownResource(Resource):
    """A resource of a type that this version does not know, or of a private `x-` type. It is never read."""

    model_config = ConfigDict(extra="allow")

    type: Annotated[str, WithJsonSchema({"type": "string", "pattern": "^x-"})]


#: The registered resource types. A name is `<subject>-<kind>`: the last word says what the document is.
RESOURCE_TYPES: dict[str, type[Resource]] = {
    "suola-rules": SuolaRulesResource,
    "rahti-data": RahtiResource,
    "site-configs": SitesResource,
}


def _resource_tag(value: Any) -> str:
    kind = value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    return kind if kind in RESOURCE_TYPES else "unknown"


AnyResource = Annotated[
    Annotated[SuolaRulesResource, Tag("suola-rules")]
    | Annotated[RahtiResource, Tag("rahti-data")]
    | Annotated[SitesResource, Tag("site-configs")]
    | Annotated[UnknownResource, Tag("unknown")],
    Discriminator(_resource_tag),
]


class Manifest(_Document):
    """`manifest.json`: the name of a provider, its links and its resources."""

    schema_version: str
    name: str
    updated: AwareDatetime | None = None
    links: list[Link] = Field(default_factory=list)
    resources: list[AnyResource] = Field(default_factory=list, max_length=MAX_RESOURCES)

    @field_validator("schema_version")
    @classmethod
    def _compatible(cls, value: str) -> str:
        return check_compatible(value, SCHEMA_VERSION)

    def ordered(self, *types: str) -> list[Resource]:
        """Resources of the registered `types` (all when none are given) in precedence order: `weight`, then document order."""
        known = [r for r in self.resources if r.type in RESOURCE_TYPES and (not types or r.type in types)]
        return sorted(known, key=lambda resource: resource.weight)


def manifest_schema() -> dict[str, Any]:
    """The JSON schema of :class:`Manifest`, as published in `docs/manifest.schema.json`."""
    schema = Manifest.model_json_schema(by_alias=True)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": SCHEMA_URL, **schema}
