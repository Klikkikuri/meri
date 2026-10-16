# The provider manifest

A provider manifest tells a consumer where to find the data of a provider: the Suola rules, the Rahti headline
data and the site configurations. Meri and the Paatti browser extension read the same manifest, so they use the
same rules and calculate the same URL signatures.

The official manifest is `manifest.json` in the `rahti` repository. Anyone can publish a manifest for other sites.

`docs/manifest.schema.json` is the JSON schema of the manifest. `meri manifest schema` generates it from the models
in `meri.rahti.manifest.models`.

## The manifest

```json
{
  "$schema": "https://raw.githubusercontent.com/Klikkikuri/meri/refs/heads/main/docs/manifest.schema.json",
  "schema_version": "0.1.0",
  "name": "Klikkikuri",
  "updated": "2026-09-12T09:12:00Z",
  "links": [
    { "rel": "about", "href": "https://github.com/Klikkikuri" }
  ],
  "resources": [
    { "type": "suola-rules", "href": "./rules.json" },
    { "type": "rahti-data", "href": "./data.json" },
    { "type": "site-configs", "href": "./sites.json" }
  ]
}
```

| Field | Required | Content |
| --- | --- | --- |
| `$schema` | no | The URL of the schema. |
| `schema_version` | yes | A semantic version. Refer to [Versions](#versions). |
| `name` | yes | The name of the provider. |
| `updated` | no | An ISO 8601 time with a time zone. |
| `links` | no | Links about the provider. Refer to [Links](#links). |
| `resources` | no | A maximum of 32 resources. |

## Resources

A resource is one typed item of data. Each resource has these fields:

| Field | Content |
| --- | --- |
| `type` | The resource type. Refer to [Resource types](#resource-types). |
| `href` | A URL, or a list of URLs. |
| the inline key of the type | The content, written in the manifest. |
| `description` | Text for people. |
| `weight` | An integer, 0 or more. The default is 0. |

A resource must have `href`, its inline key, or both.

### Where content comes from

- **`href` resolves against the document that contains it.** `./rules.json` in a manifest at
  `https://example.fi/provider/manifest.json` is `https://example.fi/provider/rules.json`. The same manifest works
  from a local copy.
- **A list of `href` values names several documents.** The consumer loads all of them, and merges them in list
  order with the merge rule of the type. If a document does not load, the consumer reports it, and merges the
  documents that load.
- **A resource can have `href` and inline content.** The consumer merges the documents first, and then applies
  the inline content with the same merge rule. Thus a resource can extend a published document and not change it.

A list and several resources both combine documents of one type. Use several resources when the documents need
different weights or descriptions. If a list names one `suola-rules` document two times, each rule is added two
times. The signatures do not change, because Suola uses the first rule that matches.

### Precedence

A consumer applies resources of one type in order of `weight`, from low to high. Resources with the same weight
apply in document order. When a merge rule keeps one value, the value applied last wins.

## Resource types

| `type` | Inline key | Read by | Merge rule |
| --- | --- | --- | --- |
| `suola-rules` | `rules` | Meri, Paatti | Add the `sites` rules of each document in order. Suola then sorts all rules by its own rule weights. |
| `rahti-data` | `data` | Meri, Paatti | Merge the entries by signature (`urls[].sign`). The entry applied last wins. |
| `site-configs` | `sites` | Paatti | Merge by site key. `rules`, `origins` and `links` are added together without duplicates; the last value of each other field wins. A shadowing entry replaces the site key. |

A type name has the form `<subject>-<kind>`. The last word tells what the document is: rules, data or
configurations. The first word names the subject or the format of the document, never a consumer.

The inline `rules` and `data` are complete documents. The inline `sites` is the map of site keys, the `sites`
field of `sites.json`.

- **A consumer ignores a type that it does not know.** It does not read an ignored resource, and it does not
  request its URL. Thus a new type does not break an old consumer, and an unknown type does not make a consumer
  fetch a URL.
- **A type name that starts with `x-` is private.** No consumer reads it, and the schema accepts it.
- **`meri manifest validate` reports an unknown type** that does not start with `x-`, and suggests the nearest
  known type. An error in a type name is found when the manifest is written.

### Several instances in one manifest

One manifest can link the data of several Meri instances:

```json
"resources": [
  { "type": "rahti-data", "href": "https://raw.githubusercontent.com/Klikkikuri/rahti/refs/heads/main/data.json" },
  { "type": "rahti-data", "href": "https://meri.example/lapinkansa/data.json", "weight": 10 },
  { "type": "site-configs", "href": "https://meri.example/lapinkansa/sites.json", "weight": 10,
    "origins": ["https://www.lapinkansa.fi/*"] },
  { "type": "suola-rules", "href": "https://meri.example/lapinkansa/rules.json", "weight": 10 }
]
```

The declared scope (refer to [Safety](#safety)) belongs to the manifest, not to each instance. The author of the
manifest is responsible for all of its resources. To trust two instances separately, add them as two providers.

## Versions

Each document type has its own version rule:

| Document | Version | Check |
| --- | --- | --- |
| manifest, `sites.json` | `schema_version`, now `0.1.0` | The same major version. While the major version is 0, also the same minor version. |
| `data.json` | `schema_version`, now `0.1.0` | The same major version. |
| `rules.json` | none | Suola's schema does not allow a version field. A consumer knows that a rules document is not compatible when Suola refuses it. |

Where a version exists, it is a semantic version.

The manifest and `sites.json` formats are in initial development, so their major version is 0. A new minor version
can contain changes that are not compatible, and a consumer refuses it. This is the caret rule of npm and Cargo: a
consumer of `0.1.0` reads `0.1.x`, and refuses `0.2.0`. Version `1.0.0` comes when the first consumer ships and the
official manifest is published. After that, only a new major version is not compatible.

The Rahti data format is used in production, and consumers compare only its major version.

## Links

A link is an object:

```json
{ "rel": "x-publisher-policy", "href": "https://www.mtvuutiset.fi/artikkeli/...", "hreflang": "fi" }
```

`rel` and `href` are required. `hreflang`, `type` (a media type) and `title` are optional. `href` must be an
`http` or `https` URL.

The registered `rel` values are `about`, `privacy-policy`, `terms-of-service`, `license` and `icon`. Other names
start with `x-`. `x-publisher-policy` is the page where a publisher states its principles. A consumer ignores a
`rel` that it does not know.

The consumer gives the label of a link from its own translations for the `rel`. It uses `title` only for a `rel`
that it has no label for. When several links have the same `rel`, the consumer uses the link whose `hreflang`
agrees with the user interface language, or else the first link.

## `sites.json`

```json
{
  "schema_version": "0.1.0",
  "sites": {
    "www.hs.fi": {
      "name": "Helsingin Sanomat",
      "description": "Selectors for the front page and the article pages.",
      "enabled": true,
      "origins": ["https://www.hs.fi/*"],
      "links": [
        { "rel": "x-publisher-policy", "href": "https://www.hs.fi/info/art-2000006390609.html" }
      ],
      "rules": [
        {
          "description": "Sidebar content: Luetuimmat, Uusimmat",
          "container": "aside article ol > li",
          "link": "a",
          "title": "h3 span:last-child"
        }
      ]
    }
  }
}
```

- The key of a site is its host.
- `origins` are match patterns. The default is `https://<key>/*`. A pattern must have an exact host: `*` is not
  permitted in the host.
- `description` on a site gives notes about the site. On a selector rule, it tells why the rule exists.
- For the bundled sites, `enabled: true` means that the site is on. For a site from a provider, it means that the
  site is offered: the site is off until the user turns it on.

## Identity

The identity of a provider is the URL of its manifest. The manifest has no `id` field: an `id` needs a registry,
and no registry exists. The URL is already unique.

A consumer does not follow a redirect for a provider. If a manifest URL answers with a redirect, the provider is
not available at the URL that the user added. To move a provider, the user adds the new URL.

## Resolution limits

- A manifest has a maximum of 32 resources.
- A manifest can cause a maximum of 32 fetches. Each `href` uses one fetch.
- A linked document is data. It is never read as a manifest.
- Each document has a size limit and a total time limit. Both apply while the document downloads, so the consumer
  stops a large or slow response before it is complete.
- The consumer reads each URL one time for each resolution.
- A remote manifest cannot link a local file.

## Storage locations

Meri reads and writes documents through a storage driver. The location selects the driver:

| Location | Driver | Write |
| --- | --- | --- |
| A path, or a `file://` URL | file | yes |
| `https://api.github.com/repos/<owner>/<repo>/contents/<path>[?ref=<ref>]` | GitHub | yes |
| `https://raw.githubusercontent.com/<owner>/<repo>/refs/heads/<branch>/<path>` | GitHub | yes |
| `https://raw.githubusercontent.com/<owner>/<repo>/refs/tags/<tag>/<path>` | GitHub | yes |
| `https://raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>` | GitHub | yes |
| Other `https://` URLs | https | no |

The GitHub driver reads and writes both URL forms through the Contents API. The raw CDN can serve a copy that is
some minutes old, and a write needs the blob sha that only the API gives.

In a raw URL, the ref is one path segment. For a branch name that contains `/`, use the Contents API form with
`?ref=`.

A relative `href` keeps the form and the ref of the manifest location:

| Manifest location | `./data.json` resolves to |
| --- | --- |
| `packages/rahti/manifest.json` | `packages/rahti/data.json` |
| `https://api.github.com/repos/Klikkikuri/rahti/contents/manifest.json?ref=dev` | `https://api.github.com/repos/Klikkikuri/rahti/contents/data.json?ref=dev` |
| `https://raw.githubusercontent.com/Klikkikuri/rahti/refs/heads/main/manifest.json` | `https://raw.githubusercontent.com/Klikkikuri/rahti/refs/heads/main/data.json` |
| `https://meri.example/lapinkansa/manifest.json` | `https://meri.example/lapinkansa/data.json` |

A GitHub answer of 404 does not mean that the document is empty: GitHub also answers 404 when a token cannot see a
private repository. Only a missing local Rahti file gives empty data.

## Safety

A manifest from a third party is content that nobody has checked. These rules apply to all of its content. One
validator states them, and the build tool and the run-time loader both use it. The consumer reports each
rejection.

### Declared scope

The declared scope of a provider is the set of site keys in its `site-configs` resources. The consumer removes a key
whose `origins` have a scheme other than `http` or `https`, are `<all_urls>` or `*://*/*`, or are not exactly the
host of the key. Then the scope filters all other content of the provider.

Declared scope is a coherence rule, not proof of control. A key is text that the provider wrote. Nothing connects
`www.hs.fi` in a manifest to the origin of that manifest, and nothing can, because a provider can describe sites
that it does not own. This is the trust model of a uBlock Origin filter list: the user chooses to trust the
provider.

### Origins of a `site-configs` resource

- A `site-configs` resource whose `href` is on a different origin from the manifest must declare `origins`: the site
  origins that its document can give. The user must see the sites of a provider before the consumer fetches the
  document. The consumer rejects each site of the document that the declared origins do not include.
- A `site-configs` resource with a same-origin `href`, or with inline content, must not declare `origins`. The grant for
  the manifest origin already includes the document, so the consumer reads the sites from the document itself.
- `origins` cannot come from an `href`. `./sites.json` on `meri.example` can name `www.hs.fi`.
- `meri manifest validate` reports `origins` that do not agree with the linked document. Too few origins remove
  sites; too many origins ask for more access than the provider uses.

### Shadowing

The `providerShadowingEnabled` setting of Paatti decides if a provider can claim a site key or an origin that the
bundled set or an earlier provider already claims.

- When the setting is off, the consumer rejects the entry and shows the reason.
- When the setting is on, `weight` decides the precedence. The bundled set always applies first, and weight 0
  does not come before it. To replace a bundled site, a provider must use a positive weight.
- A shadowing entry replaces the whole site key, `rules` included. A normal merge only adds selectors, so only
  shadowing can remove a selector that is not correct.
- The options page shows which provider supplies each site.

The setting is on by default in the development environment, and off in the other environments. It is stored on
the device, and it does not sync.

### Suola rules

Paatti installs the rules of each provider as a named rule set, with the manifest URL as the name. The bundled
rules stay the base rules, and a named rule set cannot change a base signature.

- A link is signed with the rules of the source that owns the host of the link. A provider's rules thus affect
  only the links of that provider.
- A name that is not defined gives no signature. It never falls back to the base rules, because a signature from
  the wrong rules is a valid string that no later check can find.
- `weight` has no effect on the order of rules inside Suola.

Meri reads one manifest, so it has one owner and uses no named rule sets. Refer to [Meri](#meri).

### Rahti entries

The host of the link decides which Rahti data a consumer uses, not the host of the page. For each link, the
consumer uses only the sources whose declared scope includes the host of that link, in precedence order. Thus a
provider cannot replace the headline of a link to a site that is not in its scope, on any page.

Each provider has its own data. A provider that fails does not remove or change the bundled data.

### Links and selectors

- A link `href` must be `http` or `https`, and the consumer shows it with `rel="noopener noreferrer"`.
- The consumer compiles each selector before it stores it, and removes a rule with unknown keys.
- The cost of a selector rule is the number of matched nodes and the mutation rate of the page. A time budget
  for each mutation batch and a limit of matched containers for each rule bound this cost. The budget applies to
  bundled rules too.
- The budget stops the current pass only. It never changes stored data or disables a rule, because a page could
  then disable its own corrections. Only the user can disable a rule.

### Requests

A manifest can make a consumer send requests from the network of the user. These rules apply to each fetch:

- The consumer checks the manifest URL before the first fetch, and each resource URL before its fetch.
- Remote URLs must use HTTPS.
- The consumer parses the host to an address, and rejects loopback, unspecified, private, carrier-grade NAT,
  link-local, multicast, broadcast, NAT64 and documentation addresses. It also rejects `localhost`, names that end
  in `.localhost`, and names that end in `.local`.
- The `loopbackProvidersEnabled` setting permits loopback addresses and `localhost` at `http` or `https`, for
  development. It permits no other address.
- The consumer does not follow redirects, so a redirect cannot send a request to a different address.
- The consumer sends no credentials, and it never sends fetched content to a different location.

A host permission does not stop a request: the browser sends a simple request and only holds back the response.
The permission controls reading and disclosure. The options page shows each URL that the consumer will fetch.

A consumer cannot see the address that a name resolves to. A public name with a private address thus passes the
address check. This risk is not mitigated. The user's choice of provider, the fetch limit, and the rule that
fetched content is never sent anywhere keep it small.

## Reasons for the design

- **Resources are a typed list, not blocks for each consumer.** A consumer name in a format stays after the
  consumer changes, and a list of types lets a new consumer read the types that it needs. Meri data also does not
  fit a block for each site: one news source has several feeds, and some blocked domains have no site entry.
- **Each type has one merge rule.** The rule applies inside a resource and between resources. Formats that merge
  lists without a stated rule get duplicate items and several incompatible merge methods.
- **Resources are the only indirection.** There are no nested manifests and no `href` inside a site entry. To add
  content from a different location, add a resource.
- **Linked documents have their own versions.** The Rahti data changes many times each day, and the site
  configurations change seldom. Separate documents can have separate cache lives.
- **The identity is the manifest URL.** A URL is unique without a registry.
- **The declared scope belongs to the manifest.** The author of a manifest is responsible for all that it links.

## Meri

Meri reads one manifest: the one that the `manifest` setting names. The default is `packages/rahti/manifest.json`
when that file exists.

- **Suola rules.** At the first signature, Meri loads the `suola-rules` resources in precedence order. The first
  document that Suola accepts replaces the built-in rules, and Meri adds each further document. Meri logs each
  document that does not load, and uses the built-in rules when no rules load. Meri keeps a copy of a
  remote rules document for one day, and uses an old copy when a refresh fails.
- **Rahti location.** Meri pulls and pushes Rahti data at `rahti.url`, or at `file:///app/instance/rahti/data.json`
  when `rahti.url` is empty. The manifest does not set this location. A manifest tells consumers what to read, and
  the operator of each Meri instance decides where that instance writes. The location must be writable.
- **Reads.** Meri reads a remote document only over HTTPS, and it does not follow redirects: a redirect is an error
  that names its target. A document can be 10 MB at most, and its transfer must end within the `rahti.timeout`
  setting. Meri does not check the address that a host name resolves to, so a public name with a private address
  is not stopped.
- **Commands.**
  - `meri manifest generate [PATH]` writes a manifest. Each value comes from an option, a prompt, the manifest
    already at `PATH`, or the conventional name next to the manifest. It writes `origins` only for a cross-origin
    `site-configs` resource, from the linked document.
  - `meri manifest validate [LOCATION]` checks the manifest and each linked document, and exits with an error when
    it finds a problem. Suola compiles each rules document.
  - `meri manifest show [LOCATION]` prints the merged content of each type.
  - `meri manifest schema` prints the JSON schema.
