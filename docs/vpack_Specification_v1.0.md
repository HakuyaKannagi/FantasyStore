# `.vpack` Specification v1.0
# Portable Product Pack Format

**Status:** Formal / Final Frozen  
**Freeze Status:** FINAL FREEZE — Human approved  
**Baseline:** Human Decisions OD-001..OD-017, synchronized FantasyStore v1.0 Source Candidate, Schema, Validator, Regression Test, UAT Pack evidence  
**Freeze date:** 2026-09-06

> This document is the Human Final Frozen `.vpack Specification v1.0` and is the sole Normative Authority for the v1 format.

---

## 1. Introduction

`.vpack` is a **Portable Product Pack Format** for distributing product content independently from a consuming application.

A `.vpack` package contains:

- package metadata,
- one or more product records,
- local image assets referenced by those products.

The format is intentionally independent from FantasyStore runtime internals. A conforming Producer MAY be implemented manually or by any independent tool. A conforming Consumer need not use FantasyStore, Python, SQLite, pywebview, or any FantasyStore-specific lifecycle implementation.

This Specification defines the frozen `.vpack v1` package contract and integrates the Human Decisions that resolved OD-001 through OD-017. Section 23 preserves those decisions as a traceable **Resolved Human Decisions** record.

All known Open Decisions are resolved. Human Final Freeze was approved after FantasyStore implementation synchronization, independent review, limited MINOR correction, and independent re-review.

---

## 2. Scope

This Specification specifies:

- the `.vpack` file extension and ZIP container structure,
- path and filename safety rules,
- JSON encoding and JSON object structure,
- `pack.json`, `items.json`, and `assets/`,
- Pack ID and Item ID,
- Pack Content Version syntax and comparison,
- exact Money encoding,
- image references and image validity,
- collision and duplicate rules,
- current FantasyStore v1.0 interoperability limits,
- package, Producer, and Consumer conformance,
- rejection conditions and test vectors.

This Specification does **not** specify FantasyStore UI, databases, recovery journals, Pack enable/disable state, Cart, Checkout, Purchase History, Snapshot retention, WebView2, packaging, or store-manager operation. Those are Consumer behavior and are not package-format semantics.

---

## 3. Normative Language

The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are to be interpreted in the RFC 2119 / RFC 8174 sense when written in uppercase.

- **MUST / MUST NOT** — required for conformance.
- **SHOULD / SHOULD NOT** — strong recommendation; deviation requires a justified interoperability reason.
- **MAY** — optional.

Requirements explicitly marked **FantasyStore v1.0 Consumer Profile** are interoperability requirements of that Consumer and are not automatically universal Format requirements.

---

## 4. Terminology

| Term | Meaning |
|---|---|
| Package | One `.vpack` file. |
| Producer | Software or process that creates `.vpack` packages. |
| Consumer | Software that reads or validates `.vpack` packages. |
| Pack | The product-content unit represented by one package. |
| Pack ID | Stable identifier of a Pack. |
| Item | One product record in `items.json`. |
| Item ID | Identifier of an Item within its Pack. |
| Product Identity | The tuple `(Pack ID, Item ID)`. |
| Pack Content Version | Human-controlled `MAJOR.MINOR` version of Pack content. |
| Specification Major Version / Schema Version | The `.vpack Specification` Major Version equals the integer `schema_version`. Specification v1.x uses `schema_version = 1`; a future incompatible Specification v2.x uses `schema_version = 2`. |
| Asset | A file below logical `assets/`. In v1 current behavior, all asset files are images. |
| Image Reference | An `items[].images[]` string identifying one image asset. |
| Canonical archive path | A ZIP entry path after separator normalization and Unicode NFC normalization. |
| Consumer Profile | Consumer-specific operational/resource policy layered on top of Format semantics. |

---

## 5. Conformance

### 5.1 `.vpack Specification v1.0 Package Conformance`

A Package satisfies **`.vpack Specification v1.0 Package Conformance`** when it satisfies every normative Base Format requirement in this Specification and violates no Base Format rejection condition.

This is the formal v1 package-conformance claim defined by the frozen Specification.

### 5.2 Producer Conformance

A Producer conforming to this Specification:

1. MUST produce a Package satisfying the resolved Base Format requirements.
2. MUST emit `pack.json` and `items.json` with `schema_version: 1` for Specification v1.x.
3. MUST generate canonical Pack Content Version and Money values rather than rely on Consumer-side normalization.
4. MUST use `/` as the archive member path separator.
5. MUST use ZIP Stored or Deflate as the standard interoperable compression method.
6. MUST encode ZIP entry filenames according to the UTF-8 filename policy in Section 7.5.
7. MUST NOT add unknown JSON fields outside the explicitly defined `items[].attributes` extension area.
8. MUST NOT depend on FantasyStore-specific databases, journals, or lifecycle state for package meaning.

### 5.3 Consumer Conformance

A Consumer conforming to this Specification:

1. MUST validate package structure before trusting contained paths or files.
2. MUST reject structural and semantic violations identified as Base Format rejection conditions.
3. MUST preserve exact Money semantics and MUST NOT require binary floating point to represent package prices.
4. MUST treat `(Pack ID, Item ID)` as product identity and MUST NOT infer identity from display names.
5. MUST support ZIP Stored and Deflate.
6. MUST support well-formed ZIP Data Descriptors.
7. MUST reject encrypted/password-protected package entries.
8. SHOULD support well-formed ZIP64; a Consumer Profile MAY document a justified implementation limitation.
9. MAY support additional compatibility extensions, provided those extensions do not weaken validation of Base Format packages.
10. SHOULD document Consumer-specific resource limits separately from Base Format semantics.

### 5.4 Two-Layer Conformance Claims

Specification v1.0 distinguishes two separate claims:

1. **`.vpack Specification v1.0 Package Conformance`** — the Package satisfies this Specification's Base Format requirements.
2. **`FantasyStore v1 Consumer Profile Compatibility`** — the Package additionally satisfies FantasyStore v1.0 operational/resource limits and importer acceptance boundaries.

These claims MUST NOT be collapsed into one statement.

A Package can satisfy the Base Format yet fail FantasyStore v1 Consumer Profile Compatibility because of a Consumer-specific operational limit. Conversely, FantasyStore may accept certain compatibility extensions beyond the Base Format, such as BZIP2/LZMA compression, uniform backslash archive paths, or Unicode asset paths; acceptance of such an extension does not make the input Base-Format conforming.

---

## 6. Container

### 6.1 File Extension

The canonical file extension is:

```text
.vpack
```

A Producer SHOULD use lowercase `.vpack`. FantasyStore v1.0 performs a case-insensitive suffix check and therefore also accepts case variants such as `.VPACK`.

### 6.2 Container Type

A `.vpack` is a ZIP container.

The logical root layout is exactly:

```text
example.vpack
├─ pack.json
├─ items.json
└─ assets/
   └─ ... image files and optional nested directories ...
```

The package MUST contain exactly one `pack.json` file at ZIP root and exactly one `items.json` file at ZIP root.

Content outside `pack.json`, `items.json`, and the `assets/` tree MUST NOT appear.

The logical `assets/` tree MUST exist. An explicit ZIP directory entry named `assets/` is not required if one or more file entries below `assets/` establish the tree.

Because `items` contains at least one Item and every Item contains at least one image reference, a conforming package necessarily contains at least one referenced image asset.

### 6.3 Directory Entries

Directory entries are permitted only for `assets/` or descendants of `assets/`.

Nested directories below `assets/` are permitted.

### 6.4 Encryption

A `.vpack v1` Package MUST NOT use password protection or ZIP encryption for any entry.

A conforming Consumer MUST reject an encrypted/password-protected Package. FantasyStore does not provide a password mechanism.

### 6.5 Compression Method

The standard interoperable ZIP compression methods for `.vpack v1` are:

```text
Stored
Deflate
```

A conforming Producer MUST use Stored or Deflate.

A conforming Consumer MUST support both Stored and Deflate.

A Consumer MAY additionally accept BZIP2, LZMA, or another well-defined ZIP compression method as a **Consumer Compatibility Extension**. Such additional support does not make that compression method part of the `.vpack v1` standard interoperability set.

Current FantasyStore accepts Stored, Deflate, BZIP2, and LZMA; BZIP2/LZMA acceptance is therefore documented only as a FantasyStore compatibility extension.

### 6.6 ZIP64 and Data Descriptors

A Producer MAY use well-formed ZIP64.

A conforming Consumer SHOULD support well-formed ZIP64. ZIP64 does not override any Consumer Profile capacity or resource limit.

A Producer MAY use ZIP Data Descriptors. A conforming Consumer MUST support well-formed Data Descriptors.

Malformed ZIP64 or malformed Data Descriptor structures MUST be rejected.

### 6.7 Malformed Archives

A Consumer MUST reject a package whose ZIP metadata or entry stream cannot be parsed or read safely.

### 6.8 ZIP Non-Semantic Metadata and Canonical Packaging

The following well-formed ZIP metadata are **non-semantic** to `.vpack` content meaning:

- archive comment,
- per-entry comment,
- entry timestamp,
- entry order,
- benign ZIP extra fields,
- ordinary regular-file permission bits.

A Producer MAY emit these values. A Producer SHOULD avoid unnecessary metadata when practical, but `.vpack v1` does not require fixed timestamps, fixed comments, fixed permission bits, fixed entry order, or byte-for-byte reproducible ZIP containers.

Safety-relevant file-type metadata is different. Metadata indicating a symlink, special file, device, reparse-point-like entry, or file/directory type conflict remains subject to mandatory validation and rejection.

FantasyStore's internal `content_digest` is based on validated logical paths and file payload bytes, not raw ZIP-container bytes. Therefore benign comments, timestamps, entry order, ordinary permissions, compression choice, and benign extra fields do not contribute to that Consumer-internal digest.

---

## 7. Paths and Filenames

### 7.1 Canonical Path and Separator

The `.vpack v1` archive member separator is `/`.

A conforming Producer MUST use `/`. A Package containing `\` as an archive path separator is NON-CONFORMING to the Base Format. A Consumer MAY accept and normalize a uniformly backslash-separated archive as a **Consumer Compatibility Extension**, but that does not confer Base Format conformance. Mixed `/` and `\` separators MUST be rejected.

For validation, an archive path:

1. MUST be non-empty.
2. MUST NOT contain NUL or control characters U+0000..U+001F or U+007F.
3. MUST NOT be absolute, UNC, or drive-qualified such as `C:`.
4. MUST use `/` as the Base Format separator.
5. Is normalized to Unicode NFC for collision and containment checks.
6. MUST NOT contain an empty, `.` or `..` segment.
7. MUST NOT contain a segment ending in a space or `.`.
8. MUST NOT contain a Windows reserved basename `CON`, `PRN`, `AUX`, `NUL`, `COM1`..`COM9`, `LPT1`..`LPT9` case-insensitively, including such basenames with extensions.
9. MUST remain within the package extraction root.

### 7.2 Duplicate and Collision Rules

A package MUST be rejected if any of the following occurs:

- the same raw ZIP entry name occurs more than once,
- two entries become equal after NFC normalization,
- two entries collide under case-insensitive comparison after normalization,
- file/directory metadata conflicts with the entry name's file/directory form,
- a symlink, device, junction/reparse-like entry, FIFO, socket, block device, character device, or another non-regular special object is represented.

### 7.3 Allowed Root Paths

Files at root are limited to:

```text
pack.json
items.json
```

All other files MUST be below `assets/`.

### 7.4 Asset Path Character Set and Extensions

Every asset path below `assets/`, whether referenced or unreferenced, MUST use the ASCII-compatible path character set represented by the image-reference path grammar in Section 14.2. Unicode asset filenames/segments are not Base Format conforming in `.vpack v1`.

Asset files MUST have one of these filename extensions, compared case-insensitively at archive-entry validation:

```text
.png
.jpg
.jpeg
.webp
```

No other asset file type is part of `.vpack v1`.

A Consumer MAY accept Unicode asset paths as a compatibility extension. Current FantasyStore can accept some Unicode asset paths; that behavior is not Base Format conformance.

### 7.5 ZIP Filename Encoding

ZIP entry filenames in `.vpack v1` use UTF-8 as the canonical encoding.

A Producer MUST use UTF-8 filename encoding. When a non-ASCII filename is used in any future/otherwise permitted path context, the ZIP UTF-8 identification MUST be set correctly.

CP437 and other legacy filename encodings are outside the standard interoperability profile. A Consumer MAY support them as a compatibility extension, but a Package depending on legacy filename decoding is not `.vpack v1` Base Format conforming.

---

## 8. JSON Encoding and Common JSON Rules

### 8.1 Encoding

`pack.json` and `items.json` MUST be UTF-8 JSON.

A UTF-8 BOM (`EF BB BF`) MAY be present and is removed before JSON parsing.

UTF-16 and UTF-32 encodings MUST be rejected.

Invalid UTF-8 MUST be rejected.

### 8.2 JSON Syntax

Both files MUST contain standard JSON.

A Consumer MUST reject:

- malformed JSON,
- duplicate keys within the same object,
- `NaN`, `Infinity`, and `-Infinity` non-standard numeric constants.

Formatting whitespace outside JSON strings is insignificant.

### 8.3 String Safety

After JSON decoding, every string value and object key anywhere in `pack.json` or `items.json` MUST NOT contain:

- NUL,
- U+0000..U+001F control characters,
- U+007F.

This means escaped newline, tab, carriage return, and similar controls inside decoded JSON strings are rejected by current FantasyStore semantic validation.

Consumers MUST NOT treat trimming as a package normalization rule. A display string that is whitespace-only is not a Format violation solely for that reason, provided it satisfies the field's length and common string-safety constraints. Producers SHOULD nevertheless choose useful human-readable text where a field is intended for display.

### 8.4 Unknown Properties

`.vpack v1` is closed-world. Unknown JSON properties at the `pack.json`, `items.json`, Item, and Money object levels MUST be rejected.

The only general free extension area in v1 is `items[].attributes`, whose keys and scalar values are explicitly defined by this Specification.

A Producer MUST NOT invent a new Pack, Item, Money, or other JSON field under `schema_version: 1`. A new officially defined field requires a Specification revision; a structure change that breaks existing v1 Consumer compatibility belongs to a future Major Version under Section 19.1.

---

## 9. `pack.json`

`pack.json` MUST be a JSON object with exactly the fields below.

| Field | Required | Type | Constraint | Meaning | Example |
|---|---:|---|---|---|---|
| `schema_version` | Yes | integer | exactly `1` for Specification v1.x | Specification Major Version identifier | `1` |
| `pack_id` | Yes | string | 3..64 chars; `^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$`; additional reserved-name validation | Stable Pack identifier | `demo.market` |
| `name` | Yes | string | 1..120 chars | Display name | `Demo Market` |
| `version` | Yes | string | Pack Version rule in Section 12 | Content version | `1.10` |
| `author` | Yes | string | 1..120 chars | Author/producer display text | `Example Producer` |
| `description` | Yes | string | 0..4000 chars | Pack description | `Example pack` |

Unknown properties MUST be rejected under the current v1 schema.

### 9.1 Pack ID Additional Rules

`pack_id` MUST additionally satisfy:

- lowercase ASCII identifier syntax shown above,
- first segment before the first `.` MUST NOT be a Windows reserved basename when compared case-insensitively,
- therefore values such as `con.txt`, `nul.foo`, `com1.pack`, and `lpt9.data` are invalid.

Pack IDs are not display names and MUST NOT be localized or rewritten by a Consumer.

### 9.2 Absent Metadata

`.vpack v1` has no `license`, `copyright`, `rights`, `homepage`, signature, or arbitrary Pack-metadata field in `pack.json`.

The presence of content inside a `.vpack` does not grant permission to use, modify, redistribute, or commercially exploit that content. Rights handling is outside the v1 Package Format and remains the responsibility of the Producer/author/distributor and applicable law or separate terms.

A future rights-metadata design would require an explicit Specification change and MUST NOT be simulated by adding an unknown `license` field under `schema_version: 1`.

---

## 10. `items.json`

### 10.1 Top-Level Object

`items.json` MUST be a JSON object with exactly:

```text
schema_version
items
```

`schema_version` MUST be integer `1`.

`items` MUST be an array containing at least one Item.

The Base Format does not define a universal maximum Item count. Consumer processing/resource ceilings belong to a Consumer Profile. Current FantasyStore v1.0 enforces a maximum of 2,000 Items, but that value is not a Base Format requirement.

The synchronized FantasyStore v1 Base Format schema does not encode a universal Item-count maximum. FantasyStore retains its 2,000-Item ceiling in Consumer Profile semantic validation.

### 10.2 Item Object

Each Item MUST contain exactly these fields:

| Field | Required | Type | Constraint |
|---|---:|---|---|
| `item_id` | Yes | string | 1..64 chars; `^[a-z0-9][a-z0-9._-]{0,63}$`; must not end in dot/space |
| `name` | Yes | string | 1..200 chars |
| `price` | Yes | object | MoneyLiteral, Section 13 |
| `category` | Yes | string | 1..100 chars |
| `description` | Yes | string | 0..20000 chars |
| `attributes` | Yes | object | max 100 properties; property name 1..100 chars; scalar values only |
| `images` | Yes | array of strings | 1..12 values; unique; each 1..240 chars and an image reference |

Unknown item properties MUST be rejected.

### 10.3 Item ID

Item IDs:

- MUST be unique within one `items.json`,
- MAY be reused by another Pack ID,
- together with Pack ID form Product Identity,
- MUST NOT be inferred from item `name`.

Current semantic validation also rejects Item ID collisions after NFC normalization. The current ID character set is ASCII, so this is defensive rather than normally observable.

### 10.4 Attributes

`attributes` is the v1 free-form extension area for per-item scalar metadata.

It MAY contain up to 100 properties under the current schema. Each property name is 1..100 characters. Each value MUST be one of:

- string,
- JSON number,
- boolean,
- null.

Array or object values are not permitted inside an attribute value in v1.

Non-standard numeric constants remain prohibited by Section 8.2.

FantasyStore v1.0 additionally rejects an Item whose canonicalized `attributes` JSON exceeds 64 KiB. That byte ceiling is a FantasyStore Consumer Profile resource limit, not a Base Format requirement.

### 10.5 Images Array

`images` MUST contain at least one and at most 12 references.

The raw array MUST contain unique values. After normalization, references MUST also remain non-colliding.

The JSON array is ordered and that order is semantically significant.

`images[0]` is the **Primary / Default Image**. A Producer MUST place the representative image first. When a Consumer requires a default/representative image, it MUST use `images[0]`. Images at indexes 1 and later are additional images and their order MUST be preserved by an order-preserving Consumer operation.

### 10.6 Category

`category` is dynamic text, not a fixed enumeration. A package MAY use any category string that satisfies the 1..100 character constraint and common string-safety rules.

Multiple Items MAY use the same category. Current Format evidence defines no case folding, trimming, compatibility normalization, hierarchy syntax, or globally reserved category names. Category equality/display grouping beyond exact package text is Consumer policy.

### 10.7 Product Text Fields

`name`, `description`, `category`, Pack `name`, Pack `author`, and Pack `description` are Unicode text fields subject to their length and control-character constraints. Current validation does not define HTML or Markdown semantics.

Text such as `<script>alert(1)</script>` is not rejected merely because it resembles markup or script. A Consumer MUST therefore treat package text as untrusted data and MUST NOT execute or interpret it as active code solely because of its contents.

Current v1 does not normalize these display strings to NFC for storage and does not trim leading/trailing ordinary spaces. Newline/tab/control characters are nevertheless rejected by Section 8.3.

---

## 11. Identity

### 11.1 Pack Identity

`pack_id` identifies the Pack content namespace.

### 11.2 Product Identity

The only product identity defined by current v1 is:

```text
(Pack ID, Item ID)
```

Two products with the same Item ID in different Pack IDs are distinct.

Display name, category, image path, and price MUST NOT be used as identity substitutes.

---

## 12. Pack Content Version

### 12.1 Syntax

Pack Content Version MUST match:

```regex
^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$
```

It has exactly two decimal integer components:

```text
MAJOR.MINOR
```

Each component is `0..999`.

Leading zeroes are forbidden except the single digit `0`.

Valid examples:

```text
0.0
0.1
1.0
1.1
1.9
1.10
1.999
12.345
999.999
```

Invalid examples:

```text
1
1.
.1
1.2.3
01.2
1.01
1.001
1.1000
1000.1
v1.2
1-beta
1.2-beta
```

### 12.2 Comparison

Versions MUST be compared as the integer tuple:

```text
(int(MAJOR), int(MINOR))
```

They MUST NOT be compared lexicographically as strings.

Therefore:

```text
1.9 < 1.10
1.10 < 2.0
2.0 > 1.999
```

### 12.3 Version Versus Generation

Pack Content Version is not a cryptographic or content-generation identity. Two packages MAY have the same Pack ID and the same Pack Version while containing different bytes.

A Consumer MUST NOT assume byte identity merely because Pack Version strings are equal.

FantasyStore-specific NEW/UPGRADE/REINSTALL/DOWNGRADE_SKIPPED behavior is described only in Appendix B.

---

## 13. Money

### 13.1 MoneyLiteral Shape

An Item price MUST be an object containing exactly:

```json
{
  "significand": "...",
  "exponent": "..."
}
```

Both fields MUST be strings, not JSON numbers.

The mathematical value is:

```text
significand × 10^exponent
```

The value is non-negative and exact.

### 13.2 `significand`

`significand` MUST:

- contain 1..64 ASCII decimal digits,
- be `"0"`, or a canonical non-zero integer,
- for non-zero values, have no leading zero,
- for non-zero values, have no trailing zero.

Examples:

```text
"0"     valid
"1"     valid
"385"   valid
"10"    invalid; use "1" with exponent increased by 1
"01"    invalid
```

### 13.3 `exponent`

`exponent` MUST:

- be an ASCII non-negative decimal integer string,
- use no leading zero unless exactly `"0"`,
- represent a value no greater than `9,000,000,000,000,000,000`.

### 13.4 Zero

Zero has exactly one representation:

```json
{"significand":"0","exponent":"0"}
```

`{"significand":"0","exponent":"1"}` and other equivalent forms MUST be rejected.

### 13.5 Examples

```json
{"significand":"385","exponent":"8"}
```

means exactly:

```text
385 × 10^8 = 38,500,000,000
```

A huge valid example:

```json
{"significand":"123456789","exponent":"25"}
```

means exactly `123456789 × 10^25`.

The package format does not define currency-display grouping, Japanese Yen suffixes, superscript display, or rounding. Those are Consumer display policy.

---

## 14. Assets and Image References

### 14.1 Asset Tree

All asset files MUST reside under logical `assets/`.

Nested directories are permitted.

Every asset file accepted by FantasyStore v1.0 is validated as an image; arbitrary text, script, HTML, SVG, executable, shortcut, or other non-image asset types are not part of current v1.

An otherwise valid image asset MAY be unreferenced by any Item. Such an unreferenced asset has no Item-reference semantics and a Consumer is not required to use it.

Unreferenced assets MUST still satisfy all ordinary asset path, extension, image-validity, and safety rules. Producers SHOULD omit unnecessary unreferenced assets to reduce package size, but their presence has no Base Format conformance penalty.

### 14.2 Image Reference Syntax

Each `items[].images[]` reference MUST match the current schema pattern:

```regex
^assets/[A-Za-z0-9._/-]+\.(png|jpg|jpeg|webp)$
```

Consequences include:

- it starts with literal `assets/`,
- the extension in the JSON reference is lowercase,
- referenced paths are restricted to the ASCII character set allowed by the pattern,
- forward slash is the reference separator.

The reference MUST additionally pass independent semantic path validation:

- no absolute, UNC, or drive-qualified path,
- no empty, `.` or `..` segment,
- no segment ending in space or `.`,
- no Windows reserved basename,
- no NUL or forbidden control character,
- resolved target remains within `assets/` and the package root,
- referenced member exists uniquely,
- referenced member has passed image validation,
- referenced target is a regular file and not a symlink.

### 14.3 Reference Matching

A reference is NFC-normalized before matching.

The package’s asset member set MUST contain no case-insensitive or NFC collisions. A reference MUST resolve to exactly one validated member.

Current behavior requires an exact normalized member name, not merely a case-insensitive substitute.

### 14.4 Asset Path Character Set

All `.vpack v1` asset paths use the ASCII-compatible path character set defined by Sections 7.4 and 14.2. This applies to both referenced and unreferenced assets.

Unicode remains permitted in display-oriented JSON fields such as Pack name, Item name, category, description, and `attributes`. UTF-8 remains the canonical ZIP filename encoding under Section 7.5 even though the v1 asset-path grammar itself is ASCII-compatible.

A Consumer MAY accept Unicode asset paths as a compatibility extension. Current FantasyStore's broader acceptance does not alter Base Format conformance.

---

## 15. Image Validation

An image asset MUST have an allowed extension and actual decoded image format corresponding to that extension:

| Extension | Required actual format |
|---|---|
| `.png` | PNG |
| `.jpg` | JPEG |
| `.jpeg` | JPEG |
| `.webp` | WEBP |

A Consumer MUST reject an image that is corrupt, truncated, undecodable, or whose actual format does not match its extension.

The Base Format defines no universal minimum or maximum pixel dimensions and no maximum pixel count. Consumer-specific image resource ceilings, including FantasyStore v1.0's current 40,000,000-pixel ceiling and decompression-bomb handling, belong to the Consumer Profile.

**Non-Normative Recommendation:** Producers SHOULD prefer a **4:3** aspect ratio for product imagery. A different aspect ratio is not a Format violation, and no particular resolution such as 640×480 or 1024×768 is required.

`.vpack v1` does not distinguish static from animated content within the allowed PNG, JPEG, or WebP formats as a Base Format conformance condition. An animated WebP or APNG is not invalid merely because it is animated. A Consumer is not required to play animation and MAY render a static frame. GIF is not in the allowed image-format set and remains NON-CONFORMING.

---

## 16. Resource and Safety Constraints

### 16.1 Base Format Structural and Semantic Constraints

The Base Format includes constraints that define data shape, identity, canonical representation, interoperability, or structural safety. These include, among others:

- required file/object structure,
- field types and string lengths,
- Pack ID / Item ID syntax,
- Pack Version syntax,
- exact Money representation,
- `images` cardinality and reference syntax,
- `attributes` structure/cardinality,
- path/traversal/collision/special-file rules,
- standard compression methods,
- allowed image formats and extension/format agreement.

These are not Consumer performance tuning.

### 16.2 Consumer Resource Profiles

The Base Format does **not** impose universal fixed ceilings derived from one Consumer's processing capacity or operational safety policy. Source byte size, expanded byte size, file count, single-file size, JSON byte size, compression-ratio threshold, image pixel ceiling, canonical attributes byte ceiling, and total Item-count ceiling are Consumer Profile concerns.

A Consumer SHOULD publish such limits clearly. A Producer claiming compatibility with that Consumer MUST satisfy its profile in addition to the Base Format.

### 16.3 FantasyStore v1.0 Current Resource Profile

FantasyStore v1.0 currently enforces:

| Limit | Current value | Current enforcement | Classification |
|---|---:|---|---|
| `.vpack` source file | 512 MiB | Reject if larger | FantasyStore Consumer Profile |
| Expanded file bytes | 1 GiB total | Reject if larger; actual stream bytes rechecked | FantasyStore Consumer Profile |
| File count | 5,000 files | Directory entries are not counted | FantasyStore Consumer Profile |
| Single file | 64 MiB | Reject if larger; actual extraction rechecked | FantasyStore Consumer Profile |
| `pack.json` | 256 KiB | Reject if larger | FantasyStore Consumer Profile |
| `items.json` | 16 MiB | Reject if larger | FantasyStore Consumer Profile |
| Compression ratio | reject `>100:1` when uncompressed size >= 1 MiB | Per file | FantasyStore Consumer Profile |
| Non-empty member with compressed size 0 | Reject | Per file | FantasyStore Consumer Profile |
| Image pixels | 40,000,000 | Reject if greater | FantasyStore Consumer Profile |
| Canonical `attributes` JSON | 64 KiB per Item | Reject if greater | FantasyStore Consumer Profile |
| Items per Pack | 2,000 maximum | Encoded in current local JSON Schema | FantasyStore Consumer Profile |

This Specification does **not** change the current FantasyStore Item ceiling from 2,000 to 500 or any other value.

The synchronized FantasyStore v1 Base Format schema does not encode `maxItems: 2000`; the 2,000-Item ceiling is enforced separately as FantasyStore Consumer Profile policy.

### 16.4 Compression-Ratio Edge Rules in the FantasyStore Profile

FantasyStore applies the 100:1 ratio check only when the uncompressed member size is at least 1 MiB. Exactly 100:1 is not rejected; values greater than 100:1 are rejected. A non-empty member reporting compressed size 0 is rejected.

---

## 17. Integrity

### 17.1 Embedded Digest

Current `.vpack` files do **not** contain a required package digest or signature field.

A Producer MUST NOT invent a `digest` field in `pack.json` under schema version 1 because unknown fields are rejected.

### 17.2 Consumer-Calculated Digest

FantasyStore calculates an internal SHA-256 `content_digest` after validation. This digest is not a package field, not a producer signature, and not proof of publisher authenticity.

The digest is based on normalized extracted relative paths and file payload hashes. Raw ZIP-container metadata such as archive comments, entry comments, timestamps, entry order, ordinary permission bits, and well-formed unknown extra fields is not part of this digest input. Metadata that causes the archive to fail the safety boundary (for example a symlink/special-file type) prevents digest calculation rather than becoming digest data.

The algorithm is documented in Appendix B for interoperability diagnostics only. The Base Format does not require Producer-side byte-for-byte canonical ZIP packaging; Section 6.8 defines benign ZIP metadata as non-semantic.

Whether a future public `.vpack` specification should define an embedded integrity/signature mechanism is outside current v1 and would require a later version decision.

---

## 18. Validation and Rejection

### 18.1 Format-Level Rejection Conditions

A Consumer conforming to this Specification MUST reject at least the following:

| Condition | MUST Reject | Reason |
|---|---:|---|
| Missing or duplicate root `pack.json` | Yes | Required metadata unavailable/ambiguous |
| Missing or duplicate root `items.json` | Yes | Required product data unavailable/ambiguous |
| Content outside the allowed root/assets layout | Yes | v1 closed layout |
| Empty/malformed ZIP entry name | Yes | unsafe path |
| Absolute / UNC / drive path | Yes | path escape |
| Empty / `.` / `..` path segment | Yes | traversal/ambiguity |
| NUL/control in path | Yes | unsafe/ambiguous filename |
| Reserved Windows basename | Yes | portability/safety |
| Trailing space/dot segment | Yes | portability/collision safety |
| Symlink/special/device/reparse-like entry | Yes | extraction boundary |
| Raw duplicate ZIP member | Yes | ambiguity |
| NFC collision | Yes | ambiguity |
| Case-insensitive path collision | Yes | Windows portability |
| Unknown root file | Yes | current v1 layout |
| Asset with unsupported extension | Yes | current v1 asset model |
| Invalid UTF-8 JSON / UTF-16 / UTF-32 | Yes | current encoding |
| Duplicate JSON key | Yes | semantic ambiguity |
| NaN/Infinity JSON constant | Yes | non-standard numeric value |
| Unknown JSON property outside `attributes` | Yes | current closed schema |
| `schema_version` other than 1 | Yes | unsupported schema |
| Invalid Pack ID | Yes | identity/safety |
| Invalid Pack Content Version | Yes | Human-fixed version syntax |
| Empty `items` | Yes | current v1 Pack semantics |
| Missing required Item field | Yes | incomplete Item |
| Duplicate Item ID within Pack | Yes | identity collision |
| Invalid MoneyLiteral | Yes | exact-value ambiguity/noncanonical form |
| Missing image reference target | Yes | broken Item resource |
| Unsafe image reference | Yes | path safety |
| Corrupt/format-disguised image | Yes | resource validity |

### 18.2 Consumer-Profile Rejections

The numeric resource limits in Section 16.3 are **FantasyStore v1 Consumer Profile** rejections, not Base Format rejection conditions.

A Package that exceeds a FantasyStore resource ceiling may still satisfy Specification v1.0 Base Format requirements, but it is not FantasyStore v1 Consumer Profile compatible. Other Consumers MAY publish different operational ceilings.

---

## 19. Compatibility and Extensions

### 19.1 Specification Major Version and `schema_version`

The `.vpack Specification` Major Version equals `schema_version`:

```text
.vpack Specification v1.x  -> schema_version = 1
.vpack Specification v2.x  -> schema_version = 2
```

Pack Content Version is a separate concept and does not identify the Package Format version.

Minor/patch revisions within Specification v1.x are reserved for compatible information additions, clarifications, corrections, and supplements that do not break existing v1 Package/Consumer interoperability. A Format or Schema change that breaks existing v1 compatibility requires the next Specification Major Version.

### 19.2 Closed-World Unknown Field Policy

`.vpack v1` is closed-world. A Producer MUST NOT add undefined JSON properties at Pack, top-level Items, Item, Money, or other defined object levels.

Consumers MUST reject such unknown properties when validating Base Format conformance.

The explicit exception is the defined `items[].attributes` free extension area, which remains constrained to the scalar structure in Section 10.4.

### 19.3 Extension Area

The current v1 extension area is intentionally narrow:

```text
items[].attributes
```

It supports named scalar values only. An officially standardized new field must be introduced by the Specification, not by unilateral Producer invention. If the new structure cannot remain compatible with existing v1 Consumers, it belongs to a future Major Version.

### 19.4 Rights Metadata

`.vpack v1` defines no Pack-level or Asset-level license/copyright/rights metadata. Package inclusion itself grants no rights. Rights terms, when needed, are external to this Format unless a future Specification version explicitly standardizes them.

---

## 20. Valid Examples

The following examples were reconstructed from the current schemas and validated against the current FantasyStore importer during Draft creation.

### 20.1 Minimal Valid Package

Tree:

```text
minimal.vpack
├─ pack.json
├─ items.json
└─ assets/
   └─ item.png
```

`pack.json`:

```json
{
  "schema_version": 1,
  "pack_id": "demo.min",
  "name": "Minimal Pack",
  "version": "1.0",
  "author": "Example",
  "description": ""
}
```

`items.json`:

```json
{
  "schema_version": 1,
  "items": [
    {
      "item_id": "item-1",
      "name": "Example Item",
      "price": {"significand":"0","exponent":"0"},
      "category": "Sample",
      "description": "",
      "attributes": {},
      "images": ["assets/item.png"]
    }
  ]
}
```

For a byte-exact example image, the following Base64 decodes to a valid 1×1 PNG accepted by the current validator:

```text
iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGMQkdMAAACkAFt2Ll/jAAAAAElFTkSuQmCC
```

### 20.2 Normal Valid Package

`pack.json`:

```json
{
  "schema_version": 1,
  "pack_id": "demo.market",
  "name": "Demo Market",
  "version": "1.10",
  "author": "Example Producer",
  "description": "Normal example pack"
}
```

`items.json`:

```json
{
  "schema_version": 1,
  "items": [
    {
      "item_id": "cushion",
      "name": "ちょっと良い座布団",
      "price": {"significand":"72","exponent":"2"},
      "category": "生活",
      "description": "例示用商品",
      "attributes": {"color":"blue","rank":2,"flag":true},
      "images": ["assets/cushion.png"]
    },
    {
      "item_id": "cable",
      "name": "延長コード",
      "price": {"significand":"128","exponent":"2"},
      "category": "家電",
      "description": "例示用商品2",
      "attributes": {},
      "images": ["assets/cable.png"]
    }
  ]
}
```

### 20.3 Huge Money Package Example

A valid huge exact price:

```json
{
  "item_id": "moon",
  "name": "月面対応商品",
  "price": {"significand":"123456789","exponent":"25"},
  "category": "実験",
  "description": "巨大価格例",
  "attributes": {},
  "images": ["assets/moon.png"]
}
```

The value is exactly:

```text
123456789 × 10^25
```

No floating-point approximation is part of the package meaning.

---

## 21. Invalid Examples

### 21.1 Invalid Pack Version

```json
"version": "1"
```

Invalid because Pack Version requires `MAJOR.MINOR`.

### 21.2 Duplicate Item ID

Two Items with:

```json
"item_id": "same-item"
```

in the same `items.json` are invalid.

### 21.3 Invalid Path

```text
../assets/x.png
assets/../x.png
C:/x.png
\\server\share\x.png
```

are invalid.

### 21.4 Missing Required Field

An Item without `name`, or `pack.json` without `author`, is invalid.

### 21.5 Invalid Money

```json
{"significand":"10","exponent":"0"}
```

is non-canonical because a non-zero significand has a trailing zero.

The canonical equivalent is:

```json
{"significand":"1","exponent":"1"}
```

### 21.6 Invalid Image

A JPEG file stored as `assets/a.png` is invalid because actual image format and extension do not match.

A corrupt or truncated PNG is invalid.

### 21.7 Consumer-Profile Oversize Example

A single 64 MiB + 1 byte file is rejected by FantasyStore v1.0's Consumer Profile. That numeric ceiling is not a universal Base Format limit.

---

## 22. Existing UAT Pack Conformance

Earlier Drafts recorded and then independently revalidated the two named UAT Packs. This frozen Specification retains those artifact results and evaluates them against all OD-001..OD-017 Human Decisions integrated into the Base Format.

### 22.1 Formal v1 Conformance Result

| Package | `.vpack Specification v1.0 Package Conformance` | FantasyStore v1 Consumer Profile Compatibility | Notes |
|---|---|---|---|
| `FantasyStore_UAT_MidnightMarket_v1.0.vpack` | **CONFORMING** | **CONFORMING** | `demo.midnight-market`, Version `1.0`, 5 Items; referenced assets are valid PNG images; Deflate; `/` paths; ASCII-compatible asset paths |
| `FantasyStore_UAT_ExponentialPriceLab_v1.0.vpack` | **CONFORMING** | **CONFORMING** | `demo.exponential-price-lab`, Version `1.0`, 4 Items; includes a valid huge exact MoneyLiteral; Deflate; `/` paths; ASCII-compatible asset paths |

These are formal **`.vpack Specification v1.0 Package Conformance`** verdicts under this frozen Specification.

### 22.2 Artifact and Importer Evidence

| Package | Artifact SHA-256 | Current FantasyStore result | Internal `content_digest` |
|---|---|---|---|
| MidnightMarket | `215d4d6ecfe9cc917785933aea3dda9c04be78a81ecfcbc03a7c342122e4073f` | ACCEPT | `a03bf91fbd24814cf82bade667ef57fb1b95407cdccfcf2e6c0dfd4efb2b660c` |
| ExponentialPriceLab | `4211534caa9d31755e87547e19ff16777bb6423366117446037b40a10a146d52` | ACCEPT | `9eb73b3800168db51b2b6be8587e53e86fad6a1fb79840edf6c2111d3361876c` |

For both artifacts:

- root content is exactly `pack.json`, `items.json`, and `assets/*`;
- all file entries use ZIP Deflate, which is a v1 standard interoperable compression method;
- archive member paths use `/`;
- archive comment and per-entry comments are empty;
- ZIP extra fields are empty;
- entries are regular files;
- all image references and asset paths are ASCII-compatible and resolve exactly once;
- no raw, NFC, or case-insensitive path collision exists;
- all actual images are PNG and decode successfully with extension/format agreement;
- all images are `640 × 480` (4:3), which is consistent with but does not define the non-normative 4:3 recommendation;
- every asset is referenced by an Item.

The artifacts also demonstrate supported Unicode Pack/Item names, Unicode category/description/attribute text, boolean attributes, `schema_version: 1`, Pack Version `1.0`, ASCII Pack IDs / Item IDs, exact MoneyLiteral values, multiple Items, and dynamic categories.

### 22.3 ExponentialPriceLab Historical UI Annotation

`FantasyStore_UAT_ExponentialPriceLab_v1.0.vpack` contains the attribute:

```text
期待表示 = "1.2345 × 10^29 円"
```

This is ordinary scalar text inside `attributes`. It is valid Package data but only a historical UAT annotation. It is not a normative Money display rule and does not override Section 13.

### 22.4 Test-Vector Scope

The two actual Packs are concrete interoperability vectors for the features listed above. They do not establish universal Consumer resource ceilings, and their benign ZIP metadata values do not imply fixed canonical metadata requirements.

---

## 23. Resolved Human Decisions — Historical Traceability

**Open Decisions: 0**

OD-001 through OD-017 are all resolved by Human Decision. Their identifiers are retained for historical traceability.

### OD-001 — Specification Version / `schema_version`

**Status:** RESOLVED  
**Human Decision:** Specification Major Version equals `schema_version`. Specification v1.x uses `schema_version = 1`; an incompatible v2.x uses `schema_version = 2`. Compatible clarification/correction within v1 does not require a new schema major.  
**Normative integration:** Sections 9, 10, 19.1.

### OD-002 — Resource Limits / Consumer Profile

**Status:** RESOLVED  
**Human Decision:** Consumer processing/safety ceilings are Consumer Profile policy, not Base Format limits. This includes source/expanded bytes, file counts/sizes, JSON byte ceilings, compression-ratio threshold, image pixel ceiling, canonical attributes byte ceiling, and total Item-count ceiling. Current FantasyStore 2,000 Item limit remains unchanged but is not universal.  
**Normative integration:** Sections 10.1, 10.4, 15, 16, 18.2, Appendix B.

### OD-003 — ZIP Compression Methods

**Status:** RESOLVED  
**Human Decision:** Stored and Deflate are the `.vpack v1` standard interoperability methods. Producers use one of them; conforming Consumers support both. BZIP2/LZMA or other methods may be Consumer Compatibility Extensions.  
**Normative integration:** Sections 5.2, 5.3, 6.5.

### OD-004 — ZIP64 / Data Descriptor / Encryption

**Status:** RESOLVED  
**Human Decision:** Consumers MUST support well-formed Data Descriptors. Producers MAY use ZIP64 and Consumers SHOULD support it. Encryption/password protection is MUST NOT and Consumers reject it. Malformed ZIP64/Data Descriptor is rejected.  
**Normative integration:** Sections 6.4, 6.6.

### OD-005 — ZIP Filename Encoding

**Status:** RESOLVED  
**Human Decision:** UTF-8 is the canonical ZIP filename encoding. Legacy encodings are outside standard interoperability and MAY be supported only as Consumer Compatibility Extensions.  
**Normative integration:** Section 7.5.

### OD-006 — Unknown Field / Forward Extension Policy

**Status:** RESOLVED  
**Human Decision:** `.vpack v1` is closed-world. Undefined JSON fields are NON-CONFORMING except the explicitly defined `items[].attributes` extension area. New official fields require Specification definition; incompatible structure changes require a new Major Version.  
**Normative integration:** Sections 8.4, 19.2, 19.3.

### OD-007 — License / Copyright / Rights Metadata

**Status:** RESOLVED  
**Human Decision:** v1 defines no license/copyright/rights metadata and Package inclusion grants no rights. Rights terms are external unless a future Specification explicitly standardizes them.  
**Normative integration:** Sections 9.2, 19.4.

### OD-008 — Image Aspect Ratio / Dimensions

**Status:** RESOLVED  
**Human Decision:** 4:3 is a non-normative recommendation only. Base Format defines no universal pixel width/height minimum or maximum; Consumer image ceilings belong to Consumer Profiles.  
**Normative integration:** Section 15.

### OD-009 — Archive Path Separator

**Status:** RESOLVED  
**Human Decision:** `/` is the `.vpack v1` archive separator. `\` paths are Base-Format NON-CONFORMING; a Consumer MAY accept uniform `\` as a compatibility extension. Mixed separators are rejected.  
**Normative integration:** Sections 5.2, 7.1.

### OD-010 — Whitespace-Only Display Text

**Status:** RESOLVED  
**Human Decision:** Whitespace-only display text is not invalid solely because it is whitespace-only if all other field and string-safety rules are satisfied. Consumers do not normalize by trimming as a Format rule.  
**Normative integration:** Section 8.3.

### OD-011 — Asset Path Character Set

**Status:** RESOLVED  
**Human Decision:** All standard `assets/` paths use the ASCII-compatible path character set aligned with `images[]`. Unicode remains valid in display text. Consumer acceptance of Unicode asset paths is a compatibility extension.  
**Normative integration:** Sections 7.4, 14.2, 14.4.

### OD-012 — Animated Image Semantics

**Status:** RESOLVED  
**Human Decision:** Animation is not a Base Format rejection condition within PNG/JPEG/WebP. Consumers need not play animation and may display a static frame. GIF remains outside the allowed image format set.  
**Normative integration:** Section 15.

### OD-013 — Normative Authority

**Status:** RESOLVED  
**Human Decision:** The prose `.vpack Specification` is the sole Normative Authority. JSON Schema and validators are validation aids. On conflict, the Specification controls and Schema/validator/implementation must be corrected.  
**Normative integration:** Appendix A and the conformance model.

### OD-014 — ZIP Non-Semantic Metadata

**Status:** RESOLVED  
**Human Decision:** Archive/entry comments, timestamps, entry order, benign extra fields, and ordinary regular-file permission bits are non-semantic. Byte-for-byte reproducible ZIP output is not required. Safety-relevant file-type metadata remains validation input.  
**Normative integration:** Sections 6.8, 17.2.

### OD-015 — Image Ordering / Primary Image

**Status:** RESOLVED  
**Human Decision:** `items[].images` is ordered and `images[0]` is the Primary / Default Image. Later entries are additional images in order. No new `primary_image` field is introduced.  
**Normative integration:** Section 10.5.

### OD-016 — Unreferenced Asset Policy

**Status:** RESOLVED  
**Human Decision:** Unreferenced assets are allowed and have no conformance penalty if they satisfy normal asset/path/image/safety rules. Consumers need not use them; Producers SHOULD omit unnecessary ones.  
**Normative integration:** Section 14.1.

### OD-017 — Public JSON Schema Identifier URI

**Status:** RESOLVED  
**Human Decision:** Placeholder `schema.example` identifiers are retired for public publication. The intended stable public `$id` URIs are:

```text
https://hakuyakannagi.github.io/FantasyStore/schema/pack-v1.json
https://hakuyakannagi.github.io/FantasyStore/schema/items-v1.json
```

The synchronized FantasyStore Source uses these `$id` values. Making the corresponding GitHub Pages resources publicly reachable remains a publication follow-up and is not a Format-design Open Decision.  
**Normative integration:** Appendix A.

---

## Appendix A. JSON Schema Mapping

### A.1 Normative Authority and Schema Role

The prose `.vpack Specification` is the **sole Normative Authority**.

```text
Specification prose
= Normative Authority

JSON Schema
= machine-readable validation aid

Semantic Validator
= conformance-checking implementation
```

If prose, JSON Schema, validator, or implementation conflict, the Specification controls. The conflicting validation artifact or implementation MUST be corrected; inability to express a requirement in JSON Schema does not cancel the requirement.

### A.2 Schema Draft and Public Identifier Policy

The machine-readable v1 schemas use JSON Schema Draft 2020-12:

```text
https://json-schema.org/draft/2020-12/schema
```

The intended stable public identifiers are:

```text
https://hakuyakannagi.github.io/FantasyStore/schema/pack-v1.json
https://hakuyakannagi.github.io/FantasyStore/schema/items-v1.json
```

The synchronized FantasyStore Source uses these stable `$id` identifiers in its local schema files. Runtime validation remains local and does not depend on network retrieval. Making the corresponding GitHub Pages resources publicly reachable is a **Schema Publication Follow-up**, not a v1 Format-design Open Decision.

### A.3 Current Schema / Base Format Separation

FantasyStore ships local `resources/schema/pack-v1.json` and `resources/schema/items-v1.json`. They remain validation aids, while the prose Specification is the sole Normative Authority.

The synchronized Base Format schema no longer contains `maxItems: 2000`. Under the resolved Resource-Limit policy, the Base Format has no universal Item-count maximum; FantasyStore's 2,000 Item ceiling is enforced separately as Consumer Profile policy.

Other semantic rules are also enforced outside JSON Schema, including reserved Pack ID names, duplicate Item IDs, Pack Version semantics, Money exponent/canonical-zero semantics, string control-character rules, image-target validation, and path collision rules.

---

## Appendix B. FantasyStore v1.0 Consumer Profile (Non-Normative to Base Format Unless Stated)

### B.1 Import Version Policy

For an incoming Package with the same Pack ID as a currently installed Pack, FantasyStore v1.0 classifies Pack Content Version as:

```text
not installed                    -> NEW
incoming > installed             -> UPGRADE
incoming == installed            -> REINSTALL
incoming < installed             -> DOWNGRADE_SKIPPED
```

This is Consumer lifecycle policy, not Package Format semantics. A REINSTALL may produce a new content generation even when the Version string is unchanged.

### B.2 Resource Profile

FantasyStore currently enforces the numeric operational limits in Section 16.3, including the current 2,000 Items-per-Pack ceiling. Those values are Consumer Profile limits and this Specification does not change them.

### B.3 Compatibility Extensions

Current FantasyStore accepts some inputs beyond the v1 standard interoperability rules, including:

- BZIP2 and LZMA ZIP compression in addition to Stored/Deflate,
- a uniformly backslash-separated archive path that it normalizes for compatibility,
- some Unicode asset paths that are outside the Base Format's ASCII-compatible asset-path rule.

These are **Consumer Compatibility Extensions**. Their acceptance by FantasyStore does not make such Packages Specification v1.0 Base Format conforming.

### B.4 Internal Content Digest

FantasyStore calculates an internal digest over validated extracted content:

1. Include `pack.json`, `items.json`, and every regular file under `assets/`.
2. Normalize each relative path to `/` separators and Unicode NFC.
3. Sort paths ascending by normalized Unicode string.
4. Compute SHA-256 of each file's raw bytes.
5. For each entry, append to an overall SHA-256 input:

```text
UTF8(normalized_relative_path) + NUL(0x00) + raw_32_byte_file_SHA256
```

6. Store the final SHA-256 as 64 lowercase hex characters.

This digest is Consumer-internal generation/recovery metadata. It is not embedded in `.vpack`, not a signature, and not publisher-authenticity proof.

ZIP comments, timestamps, entry order, compression choice, ordinary regular-file permission bits, and benign extra fields are not inputs to this digest. Unsafe type metadata can still cause validation rejection before digest calculation.

### B.5 Lifecycle Outside Format

Enable/disable, uninstall, recovery, journals, database materialization, store-manager UI, Cart, Checkout, Purchase History, and Snapshot retention are deliberately outside the normative Package Format.

### B.6 FantasyStore v1 Compatibility Claim

A Producer claiming **FantasyStore v1 Consumer Profile Compatibility** SHOULD verify that the Package:

1. satisfies `.vpack Specification v1.0` Base Format requirements;
2. passes the applicable FantasyStore local Schema plus semantic validation;
3. remains within the current resource limits in Section 16.3;
4. uses paths/images that pass the current safety and image validation boundaries;
5. uses a compression method accepted by FantasyStore; Stored and Deflate are the standard portable choices, while BZIP2/LZMA are FantasyStore compatibility extensions.

The actual MidnightMarket and ExponentialPriceLab UAT Packs satisfy both `.vpack Specification v1.0 Package Conformance` and this current Consumer Profile.

---

=== DOCUMENT END ===
