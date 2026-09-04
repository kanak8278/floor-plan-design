# Corpus triage: 21 raw files -> 16 examples

Every file opened and read by eye. Filenames were ignored; the drawing decides.

## Reconstruction targets (15)

| # | file | what it actually is | tier | why it earns a place |
|---|---|---|---|---|
| 1 | `godrej-ihp-3.5-bhk-floor-plan.webp` | Godrej Woods **2BHK**+2T, 1193 sqft | A | dual units agree on all 11 rooms; every printed sub-total closes. **Annotated.** Filename says 3.5BHK and is wrong |
| 2 | `happho-30x40-duplex-gf.jpg` + `ff-plan.jpg` | 30x40 duplex, both storeys | A- | the only multi-storey example; 6 imperial/metric contradictions. **Annotated.** |
| 3 | `2BHK-1223-Godrej-Woods-Thanisandra.webp` | Godrej Woods 2BHK variant, 1223.76 | A | same tower, different unit — tests that we do not treat variants as duplicates |
| 4 | `2qaajcs_1768979079_701780493.jpg` | 3BHK+3T+Study, 2184/1310 | A | 3 PHE shafts, a pooja, a handwash alcove |
| 5 | `2BHK-1353-Brigade-Lakecrest-Bhattarahalli.webp` | 2BHK 2T Type C18, 1353/873 | A | width-only PASSAGE label, 2 PHE shafts |
| 6 | `3BHK-1443-Brigade-Belvedere-Budigere.jpg` | 3BHK+2T Type 3G, 1443/909 | A | needs region crops; text is small but present |
| 7 | `5-bhk-4204-sq-ft.webp` | 5BHK+maid, **lower floor of a duplex apartment**, 4204/2648 | A | a maid's room at 52 sqft; one room with dimensions but no name |
| 8 | `vgsw12a_1741583845_576260369.jpg` | Godrej Prakriti **Kolkata** 2BHK, 934 | A | **not BBMP** — usable for extraction, not for bye-laws |
| 9 | `1767698875_0floorplansimage.webp` | 3BHK with both DRAWING and LIVING, pooja, sit-out | A- | three public rooms; the `drawing` label is unmapped today |
| 10 | `divyasree-2bhk-layout-shettigere.webp` | 2BHK 1150/785/733 | B | feet-inches only; RERA carpet printed separately |
| 11 | `3-5bhk-at-divyasree-yelahanka.webp` | **3.5BHK** 2200/1510 | B | legend prints the programme in words, so the truth is not my reading |
| 12 | `2-5bhk-floor-plan-brigade-granada.webp` | **2.5BHK**, "1,200-1,400 sq.ft." | B | area quoted as a **range**, which no schema field can hold |
| 13 | `3BHK-5148sqft-...DIVYASREE-77-LIFE...webp` | 3BHK+Study+Family+SR+ST, 5148, East facing, 100% Vastu | C | unit wraps a building core; the only sheet stating a facing and vastu in text |
| 14 | `Floor-Plan-41-Divyasree-77-Life-...avif` | 77° Life A-0302, 4BHK+SR+ST+PDR | C | different unit of the same project, not a duplicate |
| 15 | `2BHK-1176-Prestige-Golden-Grove-Tellapur.jpg` | 2BHK Type A2, 1176/709 | B | prints UTILITY AREA and USEABLE AREA, neither of which has a schema field |

## Rules-calibration case (1)

| file | what it is | why |
|---|---|---|
| `735.jpg` | 2STN 2-Bed, carpet 529.4 sqft / saleable 735 | A **real, published, affordable-segment** unit with a 4'0"x3'0" bath (1.1 m²), a 7'0"x7'0" kitchen and no labelled living room. Our plausibility bands will reject it. Either the bands are wrong for this segment or the finding must be a warn. Not a reconstruction target: the sheet is cropped on the right, so the envelope is unknown |

## Rejected (4)

| file | why |
|---|---|
| `prestige-spring-heights-...webp` | **a clubhouse fifth-floor plan** — badminton courts, squash courts, lift lobbies, fire towers. Not a dwelling |
| `prestige-city-aston-park-duplex-...webp` | photograph of a printed brochure page, screen glare, dimensions illegible |
| `images.jpeg` | three plans in one 755x264 strip, all text illegible |
| `4-bhk-floor-plan.webp` | claims 4BHK, labels 3 bedrooms; two different rooms both labelled "TOILET 2". The schedule does not close |

The first three are worth keeping as negative controls: a classifier that accepts
a clubhouse or a glare-covered photo is not gating anything.

## What rungs 1 and 2 already say

Resolved every label the 21 sheets print through `roomtypes.canonical`. No solver,
no API. The vocabulary is the binding constraint, not the geometry.

**Dropped entirely** (`-> unknown`, then dropped at `bridge.py:187` as an unmapped
category): `WASH ROOM` · `WASH` · `S.ROOM` · `MAID` · `DRAWING` · `GUEST ROOM` ·
`PDR` · `WALK-IN` · `T-1` · `LOFT` · `VOID` · `DOUBLE HEIGHT` · `CUTOUT`

`WASH ROOM` is the standard South Indian label for a bathroom. On example 9 it is
the label on all three bathrooms, so that 3BHK reconstructs with zero bathrooms.
`S.ROOM`/`MAID` drop even though a `servant` type exists — the aliases just miss
the abbreviations builders actually print.

**Collapsed, losing the distinction:**

| printed | resolves to | consequence |
|---|---|---|
| `M. BEDROOM`, `M.BEDROOM`, `M.BED ROOM` | `bedroom` | the master is unreachable from an abbreviated label; only `MASTER BEDROOM` and `MBR` reach `master_bedroom`. Roughly half the corpus abbreviates |
| `CORRIDOR`, `PASSAGE`, `LOBBY` | `foyer` | a 1.1 m x 6.3 m corridor is graded on a foyer's area band and aspect limit |
| `POWDER`, `TOI`, `ATTACHED TOILET`, `HANDWASH` | `bathroom` | a 1000x2200 powder room is graded on a full bathroom's minimum |
| `DRESSING`, `WALK IN WARDROBE` | `store` | `ZONE_OF` puts `dress` in the **private** zone; `store` is **service**. The two tables disagree |
| `LIFT LOBBY` | `foyer` | this is how the existing extraction pulled 6 building-core spaces into the 77° Life unit as rooms of the dwelling |
| `TERRACE` | `patio` | a first-floor terrace and a ground patio become one thing |

**The two vocabulary tables disagree by 14 entries.** `roomtypes.T` has 19 keys;
`topology.ZONE_OF` has 33. Fourteen of ZONE_OF's — `corridor`, `passage`,
`staircase`, `powder`, `toilet`, `handwash`, `dress`, `guest_bedroom`, `office`,
`hall`, `family`, `sit_out`, `terrace`, `garage` — name categories the taxonomy
cannot produce, so those 14 zone rules can never fire. 42% of the table is dead.

**Also dropped at rung 2:** `shaft`, deferred at `bridge.py:197` as a "site element
placed against the plot boundary". A PHE shaft in an apartment is interior, so for
every apartment example the shafts are simply lost.

**Not representable at all:** an area quoted as a range (example 12), a
double-height void (example 2), split floor levels (example 2 has seven distinct
levels), and `UTILITY AREA`/`USEABLE AREA` as printed by example 15.
