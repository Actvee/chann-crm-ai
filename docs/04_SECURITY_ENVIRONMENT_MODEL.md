# Chann1 Security & Environment Model

## 1. Purpose

This document separates the **target security model** from the **reduced-security configurations used to prove the lifecycle**. This separation is necessary because the Chann1 project intentionally excluded IAM/Service Account permission work and Secret Manager from the allowed execution scope.

## 2. Security truth statement

The approved Production execution is a **Production lifecycle proof**, recorded as:

`PRODUCTION_PROOF_REDUCED_SECURITY`

It proves deployment/promotion mechanics, migration and seed ordering, readiness, functional E2E, and release evidence. It does **not** prove a recommended secure Production authentication/credential configuration.

## 3. Target logical security model

Independent of cloud-provider implementation, the target architecture requires:

- authenticated user identity established at the Application boundary;
- authorization context backed by authoritative user/role/permission state;
- Application selects required permission and scope;
- Data enforces scope and cannot broaden it;
- Presentation cannot bypass Application;
- Application cannot bypass Data to reach PostgreSQL/Redis;
- security-sensitive cache behavior fails secure;
- credentials are not committed to source control.

## 4. Current authorization model

The Application uses permissions such as:

- `contact.read/create/update/archive`;
- `deal.read/create/update/archive/reopen`;
- `note.read/create/update`;
- `followup.read/create/update`.

Authorization returns a scope (`OWN`, `TEAM`, `ALL`). Data applies ownership/team filtering for scoped resources.

### Evidence boundary

The reference proof uses an `E2E_SALES` role with functional permissions to make the CRM flow testable. This does not prove the complete role policy promised by the broader capability matrix.

Not fully proven:

- Manager/Admin-only Deal reopen;
- explicit reopen reason;
- full Sales/Manager/Admin scope matrix;
- user/role administration;
- material-change audit emission.

## 5. Test identity adapter

`X-Chann1-Test-User-Id` is an explicitly gated test identity mechanism. The original architecture documentation correctly treats it as non-production authentication.

During the later Stage and Production lifecycle proof, the project intentionally enabled this adapter as a reduced-security exception so functional behavior could be proven without adding an Identity Provider/IAM workstream.

Final interpretation:

- acceptable for isolated DEV/test/reference proof when explicitly enabled;
- **not acceptable as the target authentication mechanism for a real business Production system**;
- must be replaced by an approved external identity/authentication adapter before a real Production launch.

## 6. Cloud security proof limitations

The Production proof manifest explicitly records:

- IAM and Secret Manager were excluded;
- Cloud Run services were public for the proof;
- database credentials were injected directly into runtime environment variables;
- the test identity header was enabled.

These are evidence limitations, not recommended design choices.

## 7. Environment security policy

### DEV

May use controlled reduced-security conveniences when necessary for fast developer feedback, provided:

- they are explicitly labeled;
- they do not leak credentials into Git;
- they cannot silently propagate into target secure Production policy;
- the exception is reproducible and documented.

For the approved Chann CRM AI Phase 1 DEV bootstrap, all three Cloud Run
services disable the platform Invoker IAM check through an explicit Terraform
input. This avoids IAM policy changes in capability-limited mode. Presentation
and the Application webhook/API are intentionally reachable; Data still
requires the internal shared secret on protected endpoints. The Terraform
lifecycle and plan gates prohibit this exception from silently propagating to
Stage or Production.

This service setting does not declare an IAM policy resource, but Cloud Run may
still require the executing principal to hold `run.services.setIamPolicy`.
Capability is intentionally not inspected; a permission failure blocks the
DEV apply and does not authorize IAM investigation or remediation.

### Stage/Test

Should resemble target Production behavior as closely as project constraints permit. In a real project, Stage should use the same authentication model intended for Production. In this reference proof, Stage used the test identity exception and must therefore be classified as PROVEN_WITH_LIMITATIONS for security.

### Production

A real Production launch requires a separate security readiness gate. The Chann1 lifecycle proof does not satisfy that gate by itself.

## 8. Credential handling target

For future real projects:

- do not store passwords/tokens in source control;
- use an approved secret-management mechanism;
- use least-privilege runtime identities;
- separate human/operator credentials from workload credentials;
- rotate credentials independently from source releases;
- prevent logs/diagnostics from printing secret values.

Because IAM and Secret Manager were intentionally not part of this Chann1 execution scope, this document defines the target principle without claiming Chann1 proved the cloud implementation.

## 9. Network/service exposure target

The architectural goal is minimum necessary exposure. Internal service relationships should not be made public solely to simplify a real Production deployment.

The public Cloud Run exposure used in the proof is an explicit exception and must not be copied blindly into a production bootstrap.

## 10. Security Definition of Done

Before a new project can call an environment secure Production, require evidence for:

- real authentication provider integration;
- authorization role/scope matrix tests;
- least-privilege service/workload identities;
- approved secret injection/rotation;
- ingress/network restrictions;
- audit logging for material events;
- security-sensitive cache behavior;
- vulnerability/dependency policy;
- secure operational access;
- security smoke/negative tests.

## 11. Evidence states for security

Use:

- `PROVEN` only for executed security controls;
- `PROVEN_WITH_LIMITATIONS` for explicitly reduced configurations;
- `NOT_VERIFIED` where the design exists but execution evidence does not;
- `NOT_PROVEN_DEFERRED` for blocked/deferred operational proof.

Never convert the Production lifecycle proof into a claim of production-grade security.
