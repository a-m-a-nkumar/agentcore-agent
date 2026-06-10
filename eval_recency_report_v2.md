# RAG Recency-Comparison Report — Digital Payments / Pair Programming

- top-K per query: **8**
- baseline pipeline: `w_temporal = 0.0` (no recency)
- new pipeline:      `w_temporal = 0.5` (prompt-enhance setting)
- decay-floor: `0.5`, half-life: `90d`, grace: `7d`

Query rewrites are cached so both runs see identical variants — the *only* differential between OLD and NEW is the recency multiplier.

## Summary

| category | query | avg age (old) | avg age (new) | shift |
|---|---|---|---|---|
| pure_recency | implement the DDN v26.3 release deployment process | 368d | 368d | +0d |
| versioned_topic | add DPN field validations following the latest baseline | 538d | 538d | +0d |
| pure_recency | set up the DPN v26.5 release prerequisites and dependencies | 184d | 184d | +0d |
| pure_recency | investigate CkM database performance issues | 368d | 220d | -148d |
| recent_only | build the AvidX integration test plan | 1126d | 1126d | +0d |
| versioned_topic | add MFA / authentication setup to the eChecksPro flow | 1706d | 1706d | +0d |
| versioned_topic | what is the latest Looney Tunes sprint retrospective? | 514d | 275d | -239d |
| versioned_topic | how does the Mustang team plan and review sprints? | 629d | 526d | -102d |
| recent_only | what is the current code review philosophy and standards? | 932d | 932d | +0d |
| pure_recency | create the rollback plan for a production deployment | 1366d | 1149d | -217d |
| evergreen | explain the permissions model for the check application | 2135d | 2135d | +0d |
| evergreen | explain the architecture of Digital Payments Exchange (DPX) | 1094d | 919d | -176d |

## [pure_recency] implement the DDN v26.3 release deployment process

**Hypothesis:** GROUND TRUTH: DDN Release v26.3 Notes / CheckList / Dependencies / Testing all 0d old. Must rank in top-3 with recency on. Without recency, older 'DDN Release' pages from prior versions may surface.

- top-8 avg age: **368d → 368d** (shift +0d)
- top-8 median age: **296d → 296d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 3 | +2 | 0d | DDN Release v26.3 Notes | `8411578455` |
| 2 | 1 | -1 | 2.1y | DPX+ Releases | `6151111343` |
| 3 | 2 | -1 | 2.1y | DPX+ Releases | `6151111343` |
| 4 | 4 | +0 | 2.2y | Digital Payments Process Improvement | `6232670238` |
| 5 | 5 | +0 | 0d | DDN Release v26.3 Prod CheckList | `8411578498` |
| 6 | 7 | +1 | 0d | DDN Release v26.3 Notes | `8411578455` |
| 7 | 6 | -1 | 1.1y | DPN Release Process | `2269675580` |
| 8 | 8 | +0 | 6mo | DDN Release v25.3 | `7697268737` |

## [versioned_topic] add DPN field validations following the latest baseline

**Hypothesis:** GROUND TRUTH: 'DPN Validations and Fields Baseline' (0d), 'DPN API Validation Errors and Messaging Guidelines' (6d). Must beat 'DPN Possible errors for a payment' (~1.9y old).

- top-8 avg age: **538d → 538d** (shift +0d)
- top-8 median age: **467d → 467d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)0 | `704119177` |
| 2 | 2 | +0 | 0d | DPN Validations and Fields Baseline | `8471117844` |
| 3 | 3 | +0 | 0d | DPN Validations and Fields Baseline | `8471117844` |
| 4 | 4 | +0 | 1.9y | DPN Possible errors for a payment | `6504546335` |
| 5 | 5 | +0 | 1.5y | DPN - Payments API  (External) - LaserPayments | `6792052737` |
| 6 | 6 | +0 | 6d | DPN API Validation Errors and Messaging Guidelines | `8462565400` |
| 7 | 7 | +0 | 1.3y | 20250205 DPN Interlock Notes | `7017529519` |
| 8 | 8 | +0 | 1.3y | 20250108 DPN Interlock Notes | `6974571673` |

## [pure_recency] set up the DPN v26.5 release prerequisites and dependencies

**Hypothesis:** GROUND TRUTH: 'DPN GA Release [v26.5] Dependencies' (0d), 'DPN GA Release [v26.5] Notes' (0d), 'DPN GA Release [v26.5] Prod CheckList' (6d). Should dominate; previous DPN release pages are older.

- top-8 avg age: **184d → 184d** (shift +0d)
- top-8 median age: **48d → 48d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 2 | +1 | 27d | DPN GA Release [v26.4] Dependencies | `8327856358` |
| 2 | 1 | -1 | 2mo | DPN GA Release [v26.3] Dependencies | `8166670516` |
| 3 | 3 | +0 | 5d | DPN GA Release [v26.5] | `8460697671` |
| 4 | 4 | +0 | 2.1y | DPX+ Releases | `6151111343` |
| 5 | 5 | +0 | 1mo | DPN - Component Ownership | `6372229545` |
| 6 | 6 | +0 | 1mo | DPN - Component Ownership | `6372229545` |
| 7 | 7 | +0 | 1.1y | DPN Release Process | `2269675580` |
| 8 | 8 | +0 | 3mo | DPN Release [v26.0.1] Notes | `7837843474` |

## [pure_recency] investigate CkM database performance issues

**Hypothesis:** GROUND TRUTH: 'CkM Database Performance Investigation (May 2026)' (1d), 'CKM Performance Test Results - April 2026' (~7d). Must rank top-2 over older CkM pages.

- top-8 avg age: **368d → 220d** (shift -148d)
- top-8 median age: **572d → 7d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 7d | CKM Performance Test Results - April 2026 | `8368586798` |
| 2 | 2 | +0 | 7d | CKM Performance Test Results - April 2026 | `8368586798` |
| 3 | 3 | +0 | 1d | CkM Database Performance Investigation (May 2026) — Findings Report | `8474165386` |
| 4 | 4 | +0 | 1.5y | Proactive Performance Activities | `6670942470` |
| 5 | — | (NEW!) | 1d | CkM Database Performance Investigation (May 2026) — Findings Report | `8474165386` |
| 6 | 5 | -1 | 1.6y | October 28, 2022 | `5785321473` |
| 7 | — | (NEW!) | 7d | Checkmatch Performance and Load Test Plan | `8322613301` |
| 8 | 6 | -2 | 1.6y | June 28, 2022 | `5634359297` |

**Dropped out of top-8 by recency:**

| was rank | age | title |
|---|---|---|
| 7 | 1.6y | September 30, 2022 |
| 8 | 1.6y | August 23, 2022 |

## [recent_only] build the AvidX integration test plan

**Hypothesis:** GROUND TRUTH: 'AvidX Integration Test Plan' (0d) is a single fresh canonical page. Should rank #1 trivially. Sanity test that recency doesn't break single-doc lookups.

- top-8 avg age: **1126d → 1126d** (shift +0d)
- top-8 median age: **1250d → 1250d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 0d | AvidX Integration Test Plan | `8479604825` |
| 2 | 2 | +0 | 0d | AvidX Integration Test Plan | `8479604825` |
| 3 | 3 | +0 | 1mo | CPV Integration Test Plan | `8067645518` |
| 4 | 4 | +0 | 3.7y | Editing Test Cases during Test Execution | `5730239275` |
| 5 | 5 | +0 | 3.2y | Shift-Left Testing | `5925311995` |
| 6 | 6 | +0 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)1 | `704119175` |
| 7 | 7 | +0 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)0 | `704119177` |
| 8 | 8 | +0 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5) | `704118985` |

## [versioned_topic] add MFA / authentication setup to the eChecksPro flow

**Hypothesis:** GROUND TRUTH: 'Authentication/Authorization/Security' (0d, fresh), 'MFA Factor Enrolled (eChecksPro)' (~5y old), 'Okta MFA Offering' (~5.4y). Recency should promote the fresh auth doc to #1.

- top-8 avg age: **1706d → 1706d** (shift +0d)
- top-8 median age: **1855d → 1855d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 0d | Authentication/Authorization/Security | `5217191417` |
| 2 | 2 | +0 | 5.4y | Okta MFA Offering | `1429143919` |
| 3 | 3 | +0 | 5.0y | Send Push Verify Activation Link (eChecksPro) | `2464088240` |
| 4 | 4 | +0 | 7.6y | Two-Factor Authentication | `704119060` |
| 5 | 5 | +0 | 5.0y | MFA Factor Enrolled (eChecksPro) | `2464186550` |
| 6 | 6 | +0 | 5.2y | Okta\MFA - V2.0 (Risk Assessment) | `2018804047` |
| 7 | 7 | +0 | 3.4y | Payer Submission | `5711561277` |
| 8 | 8 | +0 | 5.8y | Hyperwallet Payment Statuses | `704119094` |

## [versioned_topic] what is the latest Looney Tunes sprint retrospective?

**Hypothesis:** GROUND TRUTH: 8 Looney Tunes retros spanning 7d to 1923d. 'Looney Tunes Q2 Sprint Retrospective 5-12-26' (7d) should rank #1 with recency on; without recency, older retros may compete.

- top-8 avg age: **514d → 275d** (shift -239d)
- top-8 median age: **62d → 47d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 3 | +2 | 21d | Looney Tunes Q2 Sprint 2 Retrospective 4-28-26 | `8396734519` |
| 2 | 5 | +3 | 1mo | Looney Tunes Q2 Sprint 1 Retrospective 4-14-26 | `8321564732` |
| 3 | 1 | -2 | 2mo | Looney Tunes Q1 Sprint 4 Retrospective 3-2-26 | `8123908784` |
| 4 | 2 | -2 | 2mo | Looney Tunes Q1 Sprint 5 Retrospective 3-18-26 | `8209629185` |
| 5 | 4 | -1 | 2mo | Looney Tunes Q1 Sprint 3 Retrospective 2-18-26 | `8081375306` |
| 6 | 6 | +0 | 7d | Looney Tunes Q2 Sprint Retrospective 5-12-26 | `8454602876` |
| 7 | — | (NEW!) | 7d | Looney Tunes Q2 Sprint Retrospective 5-12-26 | `8454602876` |
| 8 | 7 | -1 | 5.3y | Looney Tunes Sprint 44 | `1989378116` |

**Dropped out of top-8 by recency:**

| was rank | age | title |
|---|---|---|
| 8 | 5.3y | Looney Tunes retro action items |

## [versioned_topic] how does the Mustang team plan and review sprints?

**Hypothesis:** GROUND TRUTH: 25 Mustang retros spanning 8d to 1006d. Recent retros (Q2 Sprint 3, 4-13-26) should rank top with recency on.

- top-8 avg age: **629d → 526d** (shift -102d)
- top-8 median age: **515d → 146d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 4.2y | 6-Week / 3 Sprint Planning | `5477105746` |
| 2 | 3 | +1 | 26d | Agile Execution Scorecard: Metrics, Insights, and Improvements. | `7167803485` |
| 3 | 5 | +2 | 22d | DPX Leader Onboarding | `5501226393` |
| 4 | 2 | -2 | 6mo | Sprint Review Demos | `704151588` |
| 5 | 4 | -1 | 4.1y | Sprint 7 Retrospective 8.14.19 | `704151564` |
| 6 | — | (NEW!) | 8d | Mustang Q2 Sprint 3 Retrospective 5-11-26 | `8447295489` |
| 7 | 7 | +0 | 2mo | Mustang Q1 Sprint 3 Retrospective 2-19-26 | `8082522264` |
| 8 | 6 | -2 | 2.3y | Mustang Sprint 99 Retrospective 12-12-23 | `6206914667` |

**Dropped out of top-8 by recency:**

| was rank | age | title |
|---|---|---|
| 8 | 2.3y | Mustang Sprint 100 Retrospective 12-19-23 |

## [recent_only] what is the current code review philosophy and standards?

**Hypothesis:** GROUND TRUTH: 'Code Review Philosophy' (4d, fresh) — recently updated single page. Should rank #1 either way; tests that recency doesn't introduce noise on single-fresh-doc queries.

- top-8 avg age: **932d → 932d** (shift +0d)
- top-8 median age: **1055d → 1055d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 2 | +1 | 4d | Code Review Philosophy | `5563416725` |
| 2 | 1 | -1 | 2.1y | DPX Scrum Tips / Workflow | `853508820` |
| 3 | 3 | +0 | 4.5y | Holistically Review the Entire Release’s Code | `5400822126` |
| 4 | 4 | +0 | 7mo | Pull Request Philosophy | `5563777121` |
| 5 | 5 | +0 | 4d | Code Review Philosophy | `5563416725` |
| 6 | 6 | +0 | 3.6y | Task Phases and Subtasks | `5563515636` |
| 7 | 7 | +0 | 3.6y | Task Phases and Subtasks | `5563515636` |
| 8 | 8 | +0 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5) | `704118985` |

## [pure_recency] create the rollback plan for a production deployment

**Hypothesis:** GROUND TRUTH: 'Rollback Plan' (1d) + 'Premanufactured Prod Plan' (6d). Both fresh; should both rank top.

- top-8 avg age: **1366d → 1149d** (shift -217d)
- top-8 median age: **1737d → 1686d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 2 | +1 | 1d | Rollback Plan | `8426356832` |
| 2 | 1 | -1 | 10mo | Bitbucket Release Process | `5623284676` |
| 3 | 3 | +0 | 4.8y | OKTA SSO Albertson's Deploy Plan | `5110039503` |
| 4 | 4 | +0 | 4.5y | GNG & Production Deploy | `5401346348` |
| 5 | 5 | +0 | 4.8y | SDLC | `5215027454` |
| 6 | 6 | +0 | 5.1y | Release v21.1 Rollback - Checks table migration | `1899299128` |
| 7 | 7 | +0 | 5.1y | Release v21.1 Rollback - Checks table migration | `1899299128` |
| 8 | — | (NEW!) | 1d | Rollback Plan | `8426356832` |

**Dropped out of top-8 by recency:**

| was rank | age | title |
|---|---|---|
| 8 | 4.8y | OKTA SSO Albertson's Deploy Plan |

## [evergreen] explain the permissions model for the check application

**Hypothesis:** EVERGREEN. GROUND TRUTH: 'Permissions Specification' (~13.3y old) is the canonical doc. DECAY_FLOOR=0.5 must keep it in top-K — recent loosely-related auth pages should NOT push it out.

- top-8 avg age: **2135d → 2135d** (shift +0d)
- top-8 median age: **2156d → 2156d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)0 | `704119177` |
| 2 | 2 | +0 | 4.9y | Phase II Architecture | `2164262980` |
| 3 | 7 | +4 | 28d | Harness Role Based Access Control - CheckMatch | `8361640030` |
| 4 | 3 | -1 | 13.3y | Permissions Specification | `704118881` |
| 5 | 4 | -1 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)1 | `704119175` |
| 6 | 5 | -1 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5)0 | `704119177` |
| 7 | 6 | -1 | 4.9y | Phase II Architecture | `2164262980` |
| 8 | 8 | +0 | 5.9y | ValidVerifyCustomTestPlan v1.4 (5) | `704118985` |

## [evergreen] explain the architecture of Digital Payments Exchange (DPX)

**Hypothesis:** EVERGREEN + RECENT. GROUND TRUTH: 'Welcome to YOUR DPX Wiki' (6d, recently touched), 'What is Digital Payments Exchange(DPX)?' (~4.7y, canonical), 'The Basics of DPX' (~3.9y). Both fresh-touched wiki and canonical explainer should appear.

- top-8 avg age: **1094d → 919d** (shift -176d)
- top-8 median age: **1344d → 1017d**

| new rank | old rank | move | age | title | source_id |
|---|---|---|---|---|---|
| 1 | 1 | +0 | 3.4y | Payer Submission | `5711561277` |
| 2 | 2 | +0 | 4.7y | What is Digital Payments Exchange(DPX)? | `5057741151` |
| 3 | 3 | +0 | 3mo | DPX Screening Initiative: Stakeholders | `8029667360` |
| 4 | 4 | +0 | 5.2y | DPX - User Terms and Conditions | `2124646223` |
| 5 | 5 | +0 | 2mo | Flow of DPX+ Screening Service | `8093827114` |
| 6 | 6 | +0 | 4.1y | DPXN On A Page | `1789132840` |
| 7 | 7 | +0 | 2.1y | DPX+ - Tech Overview | `5851775045` |
| 8 | — | (NEW!) | 1mo | Digital Payments Vendor Analysis Overview | `8352235542` |

**Dropped out of top-8 by recency:**

| was rank | age | title |
|---|---|---|
| 8 | 3.9y | The Basics of Digital Payments Exchange(DPX) |
