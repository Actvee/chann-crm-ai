# Chann CRM AI — SmartBrowz Document Engine Decision

**Status:** ACCEPTED / FINAL FOR IMPLEMENTATION

## 1. Decision

Chann CRM AI will use **Zoho Catalyst SmartBrowz** as the PDF rendering provider.

The product experience is:

`DOCX upload -> AI-assisted template compilation -> mapping review -> preview -> approval -> immutable publish -> deterministic PDF generation`

AI is permitted in template authoring only. AI is forbidden from the normal runtime PDF-generation path.

## 2. Why DOCX is not the runtime template

Users may design a familiar Word document and upload `.docx`, but Chann CRM compiles it into its own versioned template representation. This avoids coupling business/audit history to a mutable Word file and allows a controlled mapping/preview/publish workflow.

Persist:

- original DOCX in GCS;
- provider-neutral Intermediate Template Model;
- field/mapping schema;
- compiled HTML/CSS/Liquid-compatible template source;
- immutable published version metadata;
- every generated document's data snapshot and SHA-256.

## 3. Rendering architecture

Logical 4-tier architecture remains unchanged:

`Presentation -> Application -> Data -> Database`

SmartBrowz is a **supporting external integration**, not a fifth tier.

- Presentation: upload/mapping/preview/version UI
- Application: AI authoring orchestration, deterministic dataset builder, template compiler/renderer adapter
- Data: template/document metadata + GCS access
- Database: template/version/generated-document records
- SmartBrowz: final PDF rendering

## 4. v1 adapter strategy

Baseline v1 uses **application-managed HTML -> SmartBrowz PDF conversion**.

Reason: official SmartBrowz documentation clearly supports programmatic PDF generation from HTML as well as from predefined templates. Template design/management documentation primarily describes console workflows. The core tenant-facing product must not require an operator to manually create/publish a Catalyst template for each uploaded customer DOCX.

Therefore:

1. Chann CRM publishes its own immutable template version.
2. Chann CRM performs deterministic merge of business JSON into compiled template source.
3. SmartBrowz converts the final HTML to PDF.

Optional later mode: `predefined_template` using SmartBrowz template IDs after a supported automated management path is verified.

## 5. Template states

`DRAFT -> PREVIEWED -> PUBLISHED -> ARCHIVED`

Rules:

- publish requires explicit user approval;
- published versions are immutable;
- editing a published template creates a new draft version;
- a historical PDF always references the exact version that generated it.

## 6. Runtime truth

All money, tax, status, identity, permission and other business values come from deterministic Application/Domain logic.

The runtime flow is:

`business/domain data -> JSON data_snapshot -> published template_version -> deterministic final HTML -> SmartBrowz -> PDF -> GCS -> SHA-256/evidence`

The runtime must not ask an LLM to calculate totals, invent missing values, or rewrite the document.

## 7. SmartBrowz readiness gate

Before Phase 10 can be called READY, deployed GCP Application must prove the currently supported Catalyst integration/authentication method for SmartBrowz from the target environment. Record:

- Catalyst project ID;
- Catalyst org/DC details as required;
- SDK or REST adapter selected;
- credential/auth mechanism;
- DEV render smoke test;
- Thai font/render acceptance;
- timeout/retry/failure behavior;
- Stage proof before Production.

Do not put credential values in Git/docs/logs.

## 8. Official capability basis checked for this decision

Zoho Catalyst SmartBrowz official documentation currently describes:

- Templates designed with HTML/CSS/JavaScript and dynamic JSON;
- LiquidJS support in SmartBrowz Templates;
- programmatic PDF generation from HTML, URL, or a predefined template;
- Java, Node.js and Python SDK examples for PDF/Screenshot generation;
- draft/published behavior for templates in the Catalyst console.

Implementation must re-check current official Catalyst documentation during Phase 10 because provider APIs/authentication can change.
