# RDS PostgreSQL Connection Failure — Evidence Report

**Date:** February 27, 2026  
**Reporter:** T479888@deluxe.com  
**Environment:** Deluxe VDI → AWS RDS PostgreSQL (GS-GENAI-DEV)

---

## Summary

We cannot connect from the Deluxe VDI to the RDS PostgreSQL instance. The issue is that **the RDS security group has NO inbound rule for port 5432 (PostgreSQL)** — it only allows port 1433 (SQL Server).

---

## RDS Instance Details

| Field | Value |
|---|---|
| Endpoint | `sdlc-orch-dev-us-east-1-pg-rds-db.cvmmysogs29x.us-east-1.rds.amazonaws.com` |
| Resolved IP | `10.212.64.37` |
| Port | `5432` |
| DB Engine | PostgreSQL |
| Database | `postgres` |
| Username | `postgres` |
| VPC | `GS-GENAI-DEV-VPC (vpc-00a9f7fa9783f8912)` |
| Publicly Accessible | **No** |

## VDI Source Details

| Field | Value |
|---|---|
| VDI IP | `10.203.44.14` |
| Subnet | `10.203.x.x` (Deluxe corporate network) |

---

## Test Results

### Test 1: DNS Resolution ✅ PASS
```
sdlc-orch-dev-us-east-1-pg-rds-db.cvmmysogs29x.us-east-1.rds.amazonaws.com → 10.212.64.37
```
DNS resolves correctly.

### Test 2: ICMP Ping ❌ FAIL
```
Pinging 10.212.64.37 with 32 bytes of data:
Request timed out.
Request timed out.
Request timed out.
Request timed out.
Packets: Sent = 4, Received = 0, Lost = 4 (100% loss)
```

### Test 3: TCP Port 5432 ❌ FAIL
```
TCP connection to 10.212.64.37:5432 - TIMEOUT after 10 seconds
```
Port 5432 is completely unreachable from VDI.

### Test 4: psycopg2 Connection ❌ FAIL
```
Timestamp: 2026-02-27T17:59:03
Host: sdlc-orch-dev-us-east-1-pg-rds-db.cvmmysogs29x.us-east-1.rds.amazonaws.com
Port: 5432, DB: postgres, User: postgres
ERROR: connection to server at "sdlc-orch-dev-us-east-1-pg-rds-db.cvmmysogs29x.us-east-1.rds.amazonaws.com" (10.212.64.37), port 5432 failed: timeout expired
```

### Test 5: Secrets Manager Fetch ✅ PASS
```
boto3 → Secrets Manager → sdlc-orchestration-agent:
  POSTGRES_USER = postgres
  POSTGRES_PASSWORD = postgres
  Successfully retrieved credentials.
```
AWS API calls from VDI work fine — only TCP 5432 to RDS is blocked.

---

## Root Cause: Security Group Analysis

### SG: GS-GENAI-DEV-DB-SG (`sg-089056c61f57ebbc3`) — ⚠️ MISSING PORT 5432

| Port | Protocol | Sources | Issue |
|---|---|---|---|
| **1433** | TCP | `10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12` | ← This is SQL Server, NOT PostgreSQL |
| 8501 | TCP | `SG:sg-01c247dd2fb59c58d` | Streamlit (app-to-app) |
| **5432** | — | — | **❌ NO RULE EXISTS** |

### SG: GS-GENAI-DEV-BASELINE-SG (`sg-097f8a45ec38522cb`)

| Port | Protocol | Sources |
|---|---|---|
| 3389 | TCP | `10.0.0.0/8`, `161.211.0.0/16`, `168.135.0.0/16`, `168.235.0.0/16` |
| 443 | TCP | `0.0.0.0/0` |

**Neither security group has an inbound rule for port 5432.**

---

## Fix Required

**Add an inbound rule** to security group `GS-GENAI-DEV-DB-SG (sg-089056c61f57ebbc3)`:

| Type | Protocol | Port | Source | Description |
|---|---|---|---|---|
| PostgreSQL | TCP | 5432 | `10.0.0.0/8` | Allow PostgreSQL from internal network (same as existing 1433 rule) |

Alternatively, for tighter access:
| Type | Protocol | Port | Source | Description |
|---|---|---|---|---|
| PostgreSQL | TCP | 5432 | `10.203.44.14/32` | Allow PostgreSQL from VDI |

---

## Diagram

```
VDI (10.203.44.14) ──── TCP 5432 ────❌──── RDS (10.212.64.37)
                                      │
                            SG: GS-GENAI-DEV-DB-SG
                            Only allows port 1433 (SQL Server)
                            Port 5432 (PostgreSQL) = NO RULE
```
