# Four cases, selected from the knowledge layer

Generated from 8 documents, 513 pages, 4825 facts and 8990 relationships.

Every case below was chosen by querying for the *shape* of that case, not by naming a document or a figure.

## Case 1 — Corroborated across documents, expressed differently

### Same measure, different wording and different unit scale

Two documents state the same measure without using the same words. The link was not made from the wording: it was made because both figures are stated for the same subject and period, in the same unit, and agree to within the precision each document reports.

**System verdict:** `corroborates` (confidence 0.769, found by value_bridge)

**Reasoning**

02-delhivery-annual-report-fy24-excerpt p.36 reports "Revenues from customers" as ₹72,253.01
million and probe-note-delhivery p.1 reports "revenue from operations" as Rs 72,253.01
million. The wording differs, but both are stated for Delhivery in FY2023 in the same unit
and agree to within the precision each states, so they are the same measure reported two
ways.

- **Fact A:** `₹72,253.01 million` — Revenues from customers
  - source: 02-delhivery-annual-report-fy24-excerpt, page 36 (characters 3707–3815)
  - period: FY2023 · context: none stated · confidence: 0.95
  - evidence:
  > Revenues from customers increased by 12.68% to ₹81,415.38 million for FY24 from
  > ₹72,253.01 million for FY23.

- **Fact B:** `Rs 72,253.01 million` — revenue from operations
  - source: probe-note-delhivery, page 1 (characters 125–255)
  - period: FY2023 · context: consolidation=consolidated · confidence: 0.855
  - evidence:
  > The revenue from operations on consolidated basis for FY24 stood at Rs 79,900.00 million
  > as against Rs 72,253.01 million for FY23.



## Case 2 — A genuine or likely contradiction

### Same measure, same period, no differentiating context — different numbers

The system only calls a pair contradictory after ruling out every context difference it models, and after checking that the gap is larger than the precision the two documents claim.

**System verdict:** `contradicts` (confidence 0.648, found by concept)

**Reasoning**

Express parcel shipments for Delhivery in FY2024 is reported as 705 Mn. by probe-note-
delhivery p.1 and 740 Mn by 03-delhivery-q4-fy24-earnings-presentation p.6. No difference in
period, basis, vintage or adjustment was found between the two statements, and the gap of
4.73% is larger than their stated precision allows.

- **Fact A:** `705 Mn.` — Express parcel shipments
  - source: probe-note-delhivery, page 1 (characters 257–302)
  - period: FY2024 · context: none stated · confidence: 0.8
  - evidence:
  > Express parcel shipments in FY24 were 705 Mn.

- **Fact B:** `740 Mn` — Express parcel shipments
  - source: 03-delhivery-q4-fy24-earnings-presentation, page 6 (characters 249–267)
  - period: FY2024 · context: none stated · confidence: 0.72
  - evidence:
  > 740 Mn 1.4 Mn Tons
  - caption recovered from another cell on the same page: "Express parcel shipments in FY24"



## Case 3 — An apparent contradiction explained by context

### Reconciled by *consolidation*

The two figures differ, and the difference is accounted for by the context each statement carries.

**System verdict:** `reconciled_by_context` (confidence 0.807, found by concept)

**Reasoning**

₹ 74,540.82 million (02-delhivery-annual-report-fy24-excerpt p.22) and ₹ 81,415.38 million
(02-delhivery-annual-report-fy24-excerpt p.22) both report revenue from operations for
Delhivery and differ by 8.44%, but they are not in conflict: the first statement reports a
standalone basis while the second reports a consolidated basis.

- **Fact A:** `₹ 74,540.82 million` — revenue from operations
  - source: 02-delhivery-annual-report-fy24-excerpt, page 22 (characters 1315–1474)
  - period: FY2024 · context: consolidation=standalone · confidence: 0.95
  - evidence:
  > y The revenue from operations on standalone basis for FY24 stood at ₹ 74,540.82 million
  > as against ₹66,586.61 million for FY23, registering a growth of 11.95%.

- **Fact B:** `₹ 81,415.38 million` — revenue from operations
  - source: 02-delhivery-annual-report-fy24-excerpt, page 22 (characters 1600–1761)
  - period: FY2024 · context: consolidation=consolidated · confidence: 0.95
  - evidence:
  > y The revenue from operations on consolidated basis for FY24 stood at ₹ 81,415.38
  > million as against ₹72,253.01 million for FY23, registering a growth of 12.68%.


### Reconciled by *vintage*

The two figures differ, and the difference is accounted for by the context each statement carries.

**System verdict:** `reconciled_by_context` (confidence 0.807, found by concept)

**Reasoning**

0.2 percent (India: 2025 Article IV Consultation-Press Release… p.12) and 1.0 percent
(India: 2025 Article IV Consultation-Press Release… p.14) both report CAD as % of GDP for
India and differ by 0.80 percentage points, but they are not in conflict: the first
statement reports no stated basis while the second reports a projection.

- **Fact A:** `0.2 percent` — CAD as % of GDP
  - source: India: 2025 Article IV Consultation-Press Release; Staff Report; and Statement by the Executive Director for India; IMF Country Report No. 25/314; November 6, 2025, page 12 (characters 1411–1502)
  - period: 2025Q2 · context: none stated · confidence: 0.95
  - evidence:
  > In 2025Q2, robust services exports and remittances contained the CAD at 0.2 percent of
  > GDP.

- **Fact B:** `1.0 percent` — CAD as % of GDP
  - source: India: 2025 Article IV Consultation-Press Release; Staff Report; and Statement by the Executive Director for India; IMF Country Report No. 25/314; November 6, 2025, page 14 (characters 113–244)
  - period: FY2026 · context: vintage=projection · confidence: 0.95
  - evidence:
  > In FY2025/26, the CAD is projected at 1.0 percent of GDP, as merchandise exports weaken
  > and REER declines on elevated U.S. tariffs.


### Reconciled by *price_basis*

The two figures differ, and the difference is accounted for by the context each statement carries.

**System verdict:** `reconciled_by_context` (confidence 0.807, found by concept)

**Reasoning**

7.8 percent (India: 2025 Article IV Consultation-Press Release… p.3) and 8.8 percent (India:
2025 Article IV Consultation-Press Release… p.10) both report GDP growth for India and
differ by 1.00 percentage points, but they are not in conflict: the first statement reports
real terms while the second reports nominal terms.

- **Fact A:** `7.8 percent` — GDP growth
  - source: India: 2025 Article IV Consultation-Press Release; Staff Report; and Statement by the Executive Director for India; IMF Country Report No. 25/314; November 6, 2025, page 3 (characters 404–526)
  - period: FY2026 · context: price_basis=real · confidence: 0.95
  - evidence:
  > Following economic growth of 6.5 percent in FY2024/25, real GDP expanded by 7.8 percent
  > in the first quarter of FY2025/26.

- **Fact B:** `8.8 percent` — GDP growth
  - source: India: 2025 Article IV Consultation-Press Release; Staff Report; and Statement by the Executive Director for India; IMF Country Report No. 25/314; November 6, 2025, page 10 (characters 666–822)
  - period: 2025Q2 · context: price_basis=nominal · confidence: 0.95
  - evidence:
  > Nominal GDP growth moderated to 8.8 percent in 2025Q2, down from 9.8 percent in
  > FY2024/25, reflecting a low deflator mainly driven by declining food prices.



## Case 4 — Extraction and reasoning failures

The system's own audit of where it is weakest. These counts are computed from the store, not asserted.

| Signal | Count | Share |
| --- | ---: | ---: |
| Facts extracted | 4825 | 100% |
| Facts with no period attached | 2807 | 58% |
| Facts whose metric is a single word | 1163 | 24% |
| Facts in units that cannot be compared across documents | 2323 | 48% |
| Pages whose reading order had to be reconstructed | 211 | |

A single-word metric is the main source of false conflict: two different series on one chart can both reduce to "margin". The weakest contradictions the system is currently asserting, ranked by how thin their metric phrase is:

- PIN codes for India in 2021-12-31 is reported as 17,488 by 01-delhivery-
  prospectus-2022-excerpt p.47 and 19,300 by 01-delhivery-prospectus-2022-excerpt p.50. No
  difference in period, basis, vintage or adjustment was found between the two statements,
  and the gap of 9.39% is larger than their stated precision allows.
- square ft for India in 2021-12-31 is reported as 6.08 million by 01-delhivery-
  prospectus-2022-excerpt p.49 and 14.61 million by 01-delhivery-prospectus-2022-excerpt
  p.68. No difference in period, basis, vintage or adjustment was found between the two
  statements, and the gap of 58.38% is larger than their stated precision allows.
- No of options for Delhivery in 2023-03-31 is reported as 11,785,442 by 02-delhivery-
  annual-report-fy24-excerpt p.97 and 7,600,000 by 02-delhivery-annual-report-fy24-excerpt
  p.98. No difference in period, basis, vintage or adjustment was found between the two
  statements, and the gap of 35.51% is larger than their stated precision allows.
