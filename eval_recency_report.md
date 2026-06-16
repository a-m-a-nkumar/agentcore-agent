# RAG Recency-Comparison Report — Digital Payments / Pair Programming

- top-K per query: **8**
- baseline pipeline: `w_temporal = 0.0` (no recency)
- new pipeline:      `w_temporal = 0.5` (prompt-enhance setting)
- decay-floor: `0.5`, half-life: `90d`, grace: `7d`

Query rewrites are cached so both runs see identical variants — the *only* differential between OLD and NEW is the recency multiplier.

## Summary

| category | query | avg age (old) | avg age (new) | shift |
|---|---|---|---|---|
| implementation | implement webhook retry logic for failed eDeposits | 1985d | 1985d | +0d |
| implementation | add MFA setup to the eChecksPro signup flow | 1738d | 1469d | -270d |
| implementation | implement DPN field validations | 580d | 509d | -72d |
| implementation | build the MPX onboarding flow for medical providers | 1426d | 1426d | +0d |
| implementation | set up Hyperwallet integration for push payments | 1188d | 1188d | +0d |
| pure_recency | what is the latest CkM database performance investigation? | 23d | 23d | +0d |
| evergreen | what is the current Payment API request structure? | 3109d | 3109d | +0d |
| evergreen | explain the permissions model for the check application | 2135d | 2135d | +0d |
| evergreen | what is the architecture of Digital Payments Exchange (DPX)? | 823d | 823d | +0d |
| pure_recency | how does the Washburn team measure sprint velocity? | 1056d | 1056d | +0d |
| pure_recency | what are the current QE team goals and bug targets? | 1731d | 1731d | +0d |
| lookup | how do I create a Bug ticket in Jira for the Digital Payments project? | 1373d | 1373d | +0d |

## [implementation] implement webhook retry logic for failed eDeposits

**Hypothesis:** Webhook Testing + Debugging Stuck eDeposits should surface; newer debugging guides should rank higher.

- top-8 avg age: **1985d → 1985d** (shift +0d)
- top-8 median age: **2340d → 2340d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 7.7y | eDeposit Retail | `704119185` |
| 2 | 2 | +0 | 7.7y | eDeposit Retail | `704119185` |
| 3 | 3 | +0 | 2.2y | First American Embedded Payments | `6254395462` |
| 4 | 4 | +0 | 7.7y | eDeposit Retail | `704119185` |
| 5 | 5 | +0 | 7.7y | eDeposit Retail | `704119185` |
| 6 | 6 | +0 | 1.9y | Outstanding Items & Other Details | `6234866012` |
| 7 | 7 | +0 | 5.1y | Bad eDeposit Transaction Research | `714770349` |
| 8 | 8 | +0 | 3.4y | Payer Submission | `5711561277` |

## [implementation] add MFA setup to the eChecksPro signup flow

**Hypothesis:** Multi-factor Authentication page + Sign Up page; MFA doc is recent and should rank near top.

- top-8 avg age: **1738d → 1469d** (shift -270d)
- top-8 median age: **1897d → 1821d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 3 | +2 | 0d | Authentication/Authorization/Security | `5217191417` |
| 2 | 1 | -1 | 5.0y | MFA Factor Enrolled (eChecksPro) | `2464186550` |
| 3 | 2 | -1 | 5.0y | Send Push Verify Activation Link (eChecksPro) | `2464088240` |
| 4 | 4 | +0 | 5.4y | Okta MFA Offering | `1429143919` |
| 5 | 5 | +0 | 5.0y | Email Challenge (eChecksPro) | `2464120952` |
| 6 | — | (NEW!) | 0d | Authentication/Authorization/Security | `5217191417` |
| 7 | 6 | -1 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)1 | `704119175` |
| 8 | 7 | -1 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)0 | `704119177` |

**Dropped out of top-8 by recency:**

| was rank | age | title |
|---|---|---|
| 8 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5) |

## [implementation] implement DPN field validations

**Hypothesis:** DPN Validations and Fields Baseline (recent) should dominate.

- top-8 avg age: **580d → 509d** (shift -72d)
- top-8 median age: **551d → 276d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 3 | +2 | 6d | DPN API Validation Errors and Messaging Guidelines | `8462565400` |
| 2 | 1 | -1 | 1.9y | DPN Possible errors for a payment | `6504546335` |
| 3 | 2 | -1 | 1.9y | DPN Possible errors for a payment | `6504546335` |
| 4 | 4 | +0 | 0d | DPN Validations and Fields Baseline | `8471117844` |
| 5 | 5 | +0 | 22d | DPN PM Flow and Troubleshooting Steps | `8250064931` |
| 6 | 6 | +0 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)0 | `704119177` |
| 7 | 7 | +0 | 1.5y | DPN - Payments API  (External) - LaserPayments | `6792052737` |
| 8 | — | (NEW!) | 0d | DPN Validations and Fields Baseline | `8471117844` |

**Dropped out of top-8 by recency:**

| was rank | age | title |
|---|---|---|
| 8 | 1.6y | DPXN - Payments API  (External) |

## [implementation] build the MPX onboarding flow for medical providers

**Hypothesis:** MPX Onboarding Setup + MPX on a Page + DPX-MPX Risk Assessment; most recent MPX docs at top.

- top-8 avg age: **1426d → 1426d** (shift +0d)
- top-8 median age: **1756d → 1756d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 2.6y | MPX on a Page | `5029430113` |
| 2 | 2 | +0 | 5.0y | Okta MFA/SSO | `2308965983` |
| 3 | 3 | +0 | 4.9y | MPX Onboarding Engine Delivery Plan | `5123277838` |
| 4 | 4 | +0 | 1mo | SDLC-76: As a Product Manager, I want to authenticate using my corporate SSO credentials, so that I can securely access the SDLC Orchestrator platform without managing separate credentials | `SDLC-76` |
| 5 | 5 | +0 | 4.9y | MPX Onboarding Setup | `5032771817` |
| 6 | 6 | +0 | 4.9y | Phase II Architecture | `2164262980` |
| 7 | 7 | +0 | 4.8y | DPX Onboarding Checklist for Michael Becerra | `5257920561` |
| 8 | 8 | +0 | 4.1y | Ryan van Mechelen - DPX Onboarding Checklist | `5531566081` |

## [implementation] set up Hyperwallet integration for push payments

**Hypothesis:** Hyperwallet + Hyperwallet Transfer Method Testing; newer Hyperwallet pages should win over older drafts.

- top-8 avg age: **1188d → 1188d** (shift +0d)
- top-8 median age: **531d → 531d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 1.2y | Deposit Services Updates (Q1 2025) | `7040761935` |
| 2 | 2 | +0 | 8.2y | Hyperwallet | `704118878` |
| 3 | 3 | +0 | 1.2y | Deposit Services Updates (Q1 2025) | `7040761935` |
| 4 | 4 | +0 | 7.7y | eDeposit Retail | `704119185` |
| 5 | 5 | +0 | 3.4y | Payer Submission | `5711561277` |
| 6 | 6 | +0 | 1.5y | Using MySQL Shell 8.4.3 | `6879871129` |
| 7 | 7 | +0 | 1.5y | Using MySQL Shell 8.4.3 | `6879871129` |
| 8 | 8 | +0 | 1.5y | Using MySQL Shell 8.4.3 | `6879871129` |

## [pure_recency] what is the latest CkM database performance investigation?

**Hypothesis:** PURE RECENCY TEST. The 'CkM Database Performance Investigation (May 2026)' page should rank #1 with recency on, and likely much lower with recency off (older CkM docs compete).

- top-8 avg age: **23d → 23d** (shift +0d)
- top-8 median age: **7d → 7d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 1d | CkM Database Performance Investigation (May 2026) — Findings Report | `8474165386` |
| 2 | 2 | +0 | 7d | CKM Performance Test Results - April 2026 | `8368586798` |
| 3 | 3 | +0 | 7d | CKM Performance Test Results - April 2026 | `8368586798` |
| 4 | 4 | +0 | 1d | CkM Database Performance Investigation (May 2026) — Findings Report | `8474165386` |
| 5 | 5 | +0 | 7d | CKM Performance Test Results - April 2026 | `8368586798` |
| 6 | 7 | +1 | 7d | CKM Performance Test Results - April 2026 | `8368586798` |
| 7 | 8 | +1 | 7d | CKM Performance Test Results - April 2026 | `8368586798` |
| 8 | 6 | -2 | 4mo | Local Database Setup for liink-billpay-lockbox-db | `7720763419` |

## [evergreen] what is the current Payment API request structure?

**Hypothesis:** EVERGREEN. Payment API Specs/Requests is canonical; must NOT be demoted by recency. DECAY_FLOOR=0.5 protects it.

- top-8 avg age: **3109d → 3109d** (shift +0d)
- top-8 median age: **4495d → 4495d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 3 | +2 | 2mo | DPN [P+M] [Internal] - Payments API | `7614005323` |
| 2 | 1 | -1 | 13.0y | Payment API Specs/Requests | `704118898` |
| 3 | 2 | -1 | 12.3y | Payment API Specs | `704118896` |
| 4 | 4 | +0 | 3.4y | Payer Submission | `5711561277` |
| 5 | 5 | +0 | 1.6y | DPXN - Payments API  (External) | `6139740236` |
| 6 | 6 | +0 | 12.3y | Inbound API REST Specification | `704119012` |
| 7 | 7 | +0 | 13.0y | Payment API Specs/Requests | `704118898` |
| 8 | 8 | +0 | 12.3y | Inbound API REST Specification | `704119012` |

## [evergreen] explain the permissions model for the check application

**Hypothesis:** EVERGREEN. Permissions Specification page is canonical; must NOT be demoted.

- top-8 avg age: **2135d → 2135d** (shift +0d)
- top-8 median age: **2156d → 2156d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)0 | `704119177` |
| 2 | 2 | +0 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)0 | `704119177` |
| 3 | 8 | +5 | 28d | Harness Role Based Access Control - CheckMatch | `8361640030` |
| 4 | 3 | -1 | 13.3y | Permissions Specification | `704118881` |
| 5 | 4 | -1 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)1 | `704119175` |
| 6 | 5 | -1 | 4.9y | Phase II Architecture | `2164262980` |
| 7 | 6 | -1 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)0 | `704119177` |
| 8 | 7 | -1 | 4.9y | Phase II Architecture | `2164262980` |

## [evergreen] what is the architecture of Digital Payments Exchange (DPX)?

**Hypothesis:** EVERGREEN. 'What is DPX?' + 'The Basics of DPX' + 'Welcome to DPX Wiki'; canonical, must NOT be demoted.

- top-8 avg age: **823d → 823d** (shift +0d)
- top-8 median age: **679d → 679d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 3.4y | Payer Submission | `5711561277` |
| 2 | 2 | +0 | 4.7y | What is Digital Payments Exchange(DPX)? | `5057741151` |
| 3 | 3 | +0 | 3.9y | The Basics of Digital Payments Exchange(DPX) | `5046175484` |
| 4 | 6 | +2 | 1mo | Digital Payments Vendor Analysis Overview | `8352235542` |
| 5 | 4 | -1 | 3mo | DPX Screening Initiative: Stakeholders | `8029667360` |
| 6 | 5 | -1 | 5.2y | DPX - User Terms and Conditions | `2124646223` |
| 7 | 7 | +0 | 2mo | Flow of DPX+ Screening Service | `8093827114` |
| 8 | 8 | +0 | 2mo | DPX Rails & ScreenService Integration in Local | `6774685728` |

## [pure_recency] how does the Washburn team measure sprint velocity?

**Hypothesis:** Many Washburn retrospectives (sprints 51, 52, 53...); newest retro should win with recency, older ones with recency off.

- top-8 avg age: **1056d → 1056d** (shift +0d)
- top-8 median age: **788d → 788d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 2.2y | Washburn Sprint 57 Retrospective 9-1-21 | `5287379670` |
| 2 | 2 | +0 | 2.2y | Washburn Sprint 51 Retrospective 5-12-21 | `2407042940` |
| 3 | 3 | +0 | 5.2y | Update Registrar to Rails 5 RA | `2018869695` |
| 4 | 4 | +0 | 5.0y | DPX 2021 Q2 Plan | `2182710148` |
| 5 | 5 | +0 | 2.2y | Washburn Sprint 43 Retrospective 1-19-21 | `1907297437` |
| 6 | 6 | +0 | 2.2y | Washburn Sprint 44 Retrospective 2-16-21 | `2038333860` |
| 7 | 7 | +0 | 2.2y | Washburn Sprint 35 Retrospective 9-15-20 | `1489174584` |
| 8 | 8 | +0 | 2.2y | Washburn Sprint 61 Retrospective 10-13-21 | `5344527198` |

## [pure_recency] what are the current QE team goals and bug targets?

**Hypothesis:** Many Digital Payments QE Team Agenda pages over time; recent agenda should dominate.

- top-8 avg age: **1731d → 1731d** (shift +0d)
- top-8 median age: **1819d → 1819d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 5.1y | Digital Payments QE Team Agenda: 4/15/2021 | `2250604614` |
| 2 | 2 | +0 | 5.1y | QE Projects & Initiatives | `712114593` |
| 3 | 3 | +0 | 5.1y | Digital Payments QE Team Agenda: 4/8/2021 | `2240742885` |
| 4 | 4 | +0 | 6.0y | Digital Payments QE Team Agenda: 05/07/2020 | `1082032136` |
| 5 | 5 | +0 | 4.9y | Digital Payments QE Team Agenda: 7/1/2021 | `5123215690` |
| 6 | 6 | +0 | 4.9y | Digital Payments QE Team Agenda: 7/1/2021 | `5123215690` |
| 7 | 7 | +0 | 3.7y | Adding and Deleting Test Cases in a Test Phase | `5730239066` |
| 8 | 8 | +0 | 3.2y | Shift-Left Testing | `5925311995` |

## [lookup] how do I create a Bug ticket in Jira for the Digital Payments project?

**Hypothesis:** Specific instructional page exists. Recency should be roughly neutral — single doc.

- top-8 avg age: **1373d → 1373d** (shift +0d)
- top-8 median age: **1383d → 1383d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 4.1y | Q1 - 2022 - Sprints 70 - 72 | `5496668690` |
| 2 | 2 | +0 | 4.3y | Q1 - 2022 - Sprints 67 - 69 | `5403083265` |
| 3 | 3 | +0 | 4.0y | Q2 - 2022 - EPAY Sprints 73 - 75 & DDN Sprint 13 - 15 | `5553651713` |
| 4 | 4 | +0 | 3.7y | Q3.2 - 2022 - EPAY Sprints 79 - 81 & DDN Sprints 19 - 21 | `5710053455` |
| 5 | 5 | +0 | 3.9y | Q3 - 2022 - EPAY Sprints 76 - 78 & DDN Sprints 16 - 18 | `5605916673` |
| 6 | 6 | +0 | 3.6y | Q4.1 - 2022 - EPAY Sprints 82 & 83 & DDN Sprints 22 & 23 | `5771231572` |
| 7 | 7 | +0 | 3.1y | Q1.2 - 2023 - EPAY Sprints 87, 88 & 89 | `5907349962` |
| 8 | 8 | +0 | 3.4y | Q1.1 - 2023 - EPAY Sprints 84, 85 & 86 | `5847679133` |
