# AX-15 — Data quality report

**Source integrity: 311 files verified, 0 changed** — OK

_Generated 2026-11-21 14:06 UTC from `data-original/` (256 train tickets read for cross-checks; labels and train_index not read)._

## Reference load

| Table | Expected | Loaded | |
|---|---:|---:|---|
| stores | 120 | 120 | ✓ |
| assets | 477 | 477 | ✓ |
| sla_matrix | 28 | 28 | ✓ |
| version_matrix | 21 | 21 | ✓ |

## 1. Ticket asset_ids not in CMDB (4)

| Ticket | Store | Category | asset_id | Ticket version |
|---|---|---|---|---|
| INC-00011 | SW-0054 | Wi-Fi / Network | `POS-9791-T1` | FoodTech POS v4.2.1 |
| INC-00048 | SW-0114 | Online Orders | `POS-8341-T2` | FoodTech POS v4.2.1 |
| INC-00180 | SW-0059 | POS | `POS-9177-T1` | FoodTech POS v4.2.1 |
| INC-00217 | SW-0081 | Online Orders | `POS-9072-T5` | FoodTech POS v4.2.1 |

## 2. Stores reporting Soft Serve issues with no soft-serve asset in CMDB (24 stores)

Only 13 of 41 Soft Serve tickets link to an SSM asset. Five of these stores (SW-0009, 0037, 0061, 0101, 0102) are the chaos-ticket stores; the full ticket set shows more.

| Store | Soft Serve tickets |
|---|---|
| SW-0009 | INC-00118, INC-00155 |
| SW-0010 | INC-00074 |
| SW-0021 | INC-00112 |
| SW-0025 | INC-00170 |
| SW-0031 | INC-00053 |
| SW-0033 | INC-00115 |
| SW-0035 | INC-00136 |
| SW-0037 | INC-00014, INC-00037 |
| SW-0054 | INC-00164 |
| SW-0059 | INC-00026 |
| SW-0060 | INC-00150, INC-00246 |
| SW-0061 | INC-00045 |
| SW-0063 | INC-00319 |
| SW-0068 | INC-00158 |
| SW-0069 | INC-00289 |
| SW-0074 | INC-00247 |
| SW-0086 | INC-00281 |
| SW-0087 | INC-00079 |
| SW-0088 | INC-00143 |
| SW-0090 | INC-00266 |
| SW-0092 | INC-00021 |
| SW-0101 | INC-00224 |
| SW-0102 | INC-00078, INC-00189 |
| SW-0110 | INC-00096 |

## 3. Ticket vs CMDB version mismatches (3)

| Ticket | asset_id | Ticket says | CMDB says |
|---|---|---|---|
| INC-00014 | `RTR-0037-01` | KitchenBridge v2.3 | NetLink NL-3000 v2.2 |
| INC-00089 | `PRN-0089-P1` | KitchenBridge v2.3 | EpsonTM-T88VI v1.12 |
| INC-00298 | `KSK-0008-01` | KitchenBridge v2.3 | TouchPoint K1 v1.1 |

Ticket versions not in version-matrix.csv: KitchenBridge v2.3  
CMDB versions not in version-matrix.csv: none

## 4. Asset prefix vs ticket category mismatches (43)

Expected prefix per category: POS → POS, Printers → PRN, Wi-Fi / Network → RTR, Soft Serve → SSM, Kiosks → KSK. Online Orders is not checked (no dedicated asset class).

| Category | Expected | Linked asset prefix | Count | Tickets |
|---|---|---|---:|---|
| Soft Serve | SSM | RTR | 10 | INC-00014, INC-00021, INC-00037, INC-00045, INC-00053, INC-00096, INC-00118, INC-00158, INC-00189, INC-00224 |
| Soft Serve | SSM | PRN | 9 | INC-00026, INC-00074, INC-00079, INC-00112, INC-00143, INC-00150, INC-00247, INC-00289, INC-00319 |
| Soft Serve | SSM | POS | 8 | INC-00078, INC-00115, INC-00155, INC-00164, INC-00170, INC-00246, INC-00266, INC-00281 |
| Kiosks | KSK | PRN | 5 | INC-00094, INC-00101, INC-00257, INC-00259, INC-00262 |
| Kiosks | KSK | RTR | 5 | INC-00005, INC-00100, INC-00163, INC-00265, INC-00314 |
| Kiosks | KSK | POS | 3 | INC-00038, INC-00062, INC-00141 |
| Kiosks | KSK | SSM | 1 | INC-00056 |
| Soft Serve | SSM | KSK | 1 | INC-00136 |
| Wi-Fi / Network | RTR | POS | 1 | INC-00011 |

## 5. KB conflicts

### 5a. FoodTech POS v4.2.x end-of-life — 3 sources, 3 different dates

| Source | Applies to | EOL / end of support |
|---|---|---|
| `kb/runbooks/pos-startup-failure.md` | v4.2.x | 2026-03-31 |
| `kb/system-specs/foodtech-pos.md` | v4.2.1 | 2025-12-31 |
| `kb/system-specs/foodtech-pos.md` | v4.2.2 | 2025-12-31 |
| `kb/system-specs/version-matrix.csv` | v4.2.1 | 2026-12 |

- **v4.2.1: sources contradict** — 2025-12-31 (`foodtech-pos.md`) vs 2026-12 (`version-matrix.csv`) vs 2026-03-31 (`pos-startup-failure.md`)
- **v4.2.2: sources contradict** — 2025-12-31 (`foodtech-pos.md`) vs 2026-03-31 (`pos-startup-failure.md`)

Not counted: v4.2.3 is a separate version with its own date (2026-06-30 in `foodtech-pos.md`). The runbook's blanket "v4.2.x" wording would technically cover it too.

Release dates also disagree for 5 FoodTech versions:

| Version | foodtech-pos.md | version-matrix.csv |
|---|---|---|
| v4.2.1 | 2023-06-15 | 2022-03-15 |
| v4.2.2 | 2023-09-20 | 2022-09-10 |
| v4.2.3 | 2024-02-10 | 2023-06-01 |
| v5.1.0 | 2024-08-01 | 2024-01-20 |
| v5.1.1 | 2025-01-15 | 2024-07-08 |

### 5b. SLA: escalation SOP vs sla_matrix.csv (7 disagreements)

sla_matrix.csv is identical across all categories, so it is compared per priority. The SOP table is titled *L2 Response* SLAs; the matrix does not say whether its first-response/resolution columns are L1 or L2, which is itself an ambiguity to resolve.

| Priority | Measure | SOP (escalation-procedure.md) | sla_matrix.csv |
|---|---|---|---|
| P1 | resolution | 4 hours | 1 h |
| P2 | first_response | 1 hour | 30 min |
| P2 | resolution | 8 hours | 4 h |
| P3 | first_response | 3 hours | 1 h |
| P3 | resolution | 24 hours | 8 h |
| P4 | first_response | 8 business hours (business time) | 2 h |
| P4 | resolution | 5 business days (business time) | 24 h |

## 6. Other consistency checks

**Ticket store / asset ownership (0)**: none

**CMDB date anomalies (65)** — service/warranty dates before install date

<details><summary>show</summary>

- RTR-0001-01: last_service_date 2023-02-07 before install_date 2023-10-05
- RTR-0002-01: last_service_date 2023-07-14 before install_date 2023-12-07
- RTR-0003-01: last_service_date 2023-05-30 before install_date 2023-08-21
- POS-0005-T1: last_service_date 2023-01-11 before install_date 2023-04-17
- POS-0006-T1: last_service_date 2023-02-23 before install_date 2023-10-04
- PRN-0006-P1: last_service_date 2023-01-19 before install_date 2023-01-31
- SSM-0007-01: last_service_date 2023-03-04 before install_date 2023-09-20
- RTR-0011-01: last_service_date 2023-08-22 before install_date 2023-12-14
- PRN-0012-P1: last_service_date 2023-05-28 before install_date 2023-11-02
- POS-0013-T1: last_service_date 2023-02-21 before install_date 2023-12-12
- POS-0013-T2: last_service_date 2023-06-03 before install_date 2023-11-22
- POS-0014-T1: last_service_date 2023-05-04 before install_date 2023-10-31
- POS-0016-T1: last_service_date 2023-02-19 before install_date 2023-07-10
- PRN-0017-P1: last_service_date 2023-07-23 before install_date 2023-10-13
- PRN-0019-P2: last_service_date 2023-02-20 before install_date 2023-09-03
- POS-0021-T1: last_service_date 2023-04-19 before install_date 2023-09-19
- RTR-0025-01: last_service_date 2023-02-18 before install_date 2023-05-02
- SSM-0026-01: last_service_date 2023-04-19 before install_date 2023-11-06
- RTR-0029-01: last_service_date 2023-02-28 before install_date 2023-11-16
- SSM-0032-01: last_service_date 2023-04-10 before install_date 2023-11-11
- POS-0035-T1: last_service_date 2023-02-16 before install_date 2023-08-13
- POS-0037-T1: last_service_date 2023-06-21 before install_date 2023-08-18
- RTR-0039-01: last_service_date 2023-04-09 before install_date 2023-09-23
- POS-0040-T1: last_service_date 2023-03-26 before install_date 2023-07-03
- RTR-0042-01: last_service_date 2023-06-28 before install_date 2023-07-13
- POS-0043-T1: last_service_date 2023-06-21 before install_date 2023-08-24
- SSM-0046-01: last_service_date 2023-01-19 before install_date 2023-07-27
- PRN-0048-P1: last_service_date 2023-06-01 before install_date 2023-09-15
- PRN-0049-P1: last_service_date 2023-03-23 before install_date 2023-10-23
- RTR-0053-01: last_service_date 2023-06-26 before install_date 2023-11-26
- POS-0054-T2: last_service_date 2023-04-26 before install_date 2023-12-23
- PRN-0054-P1: last_service_date 2023-06-05 before install_date 2023-07-04
- KSK-0054-01: last_service_date 2023-03-28 before install_date 2023-10-19
- RTR-0058-01: last_service_date 2023-01-12 before install_date 2023-10-16
- POS-0060-T1: last_service_date 2023-03-02 before install_date 2023-06-05
- POS-0063-T1: last_service_date 2023-05-05 before install_date 2023-10-26
- POS-0064-T1: last_service_date 2023-03-12 before install_date 2023-12-16
- POS-0072-T1: last_service_date 2023-05-01 before install_date 2023-11-19
- RTR-0072-01: last_service_date 2023-06-07 before install_date 2023-08-12
- POS-0078-T1: last_service_date 2023-08-03 before install_date 2023-09-14
- RTR-0081-01: last_service_date 2023-01-30 before install_date 2023-08-07
- RTR-0086-01: last_service_date 2023-08-22 before install_date 2023-11-02
- PRN-0088-P1: last_service_date 2023-01-05 before install_date 2023-07-28
- POS-0089-T1: last_service_date 2023-02-18 before install_date 2023-03-04
- PRN-0089-P1: last_service_date 2023-05-24 before install_date 2023-11-02
- PRN-0090-P1: last_service_date 2023-02-10 before install_date 2023-11-12
- PRN-0091-P2: last_service_date 2023-06-01 before install_date 2023-06-20
- POS-0092-T1: last_service_date 2023-03-20 before install_date 2023-07-22
- POS-0096-T1: last_service_date 2023-09-13 before install_date 2023-10-10
- KSK-0096-01: last_service_date 2023-01-19 before install_date 2023-07-11
- RTR-0098-01: last_service_date 2023-03-19 before install_date 2023-10-22
- PRN-0099-P1: last_service_date 2023-02-28 before install_date 2023-12-26
- POS-0101-T1: last_service_date 2023-02-22 before install_date 2023-03-28
- KSK-0106-01: last_service_date 2023-08-08 before install_date 2023-10-10
- PRN-0108-P1: last_service_date 2023-07-21 before install_date 2023-10-16
- RTR-0110-01: last_service_date 2023-06-25 before install_date 2023-09-21
- PRN-0111-P2: last_service_date 2023-06-06 before install_date 2023-07-05
- SSM-0112-01: last_service_date 2023-10-16 before install_date 2023-12-23
- POS-0113-T1: last_service_date 2023-03-19 before install_date 2023-07-10
- RTR-0113-01: last_service_date 2023-04-22 before install_date 2023-06-26
- RTR-0116-01: last_service_date 2023-08-01 before install_date 2023-10-24
- PRN-0117-P2: last_service_date 2023-03-31 before install_date 2023-09-09
- KSK-0118-01: last_service_date 2023-03-27 before install_date 2023-08-04
- POS-0120-T1: last_service_date 2023-01-27 before install_date 2023-10-10
- RTR-0120-01: last_service_date 2023-01-24 before install_date 2023-11-23

</details>
