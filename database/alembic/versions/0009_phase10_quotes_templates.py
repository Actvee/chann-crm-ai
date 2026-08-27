"""Phase 10 — quotes + generic document template engine (schema only).

Revision ID: 0009_phase10_quotes_templates
Revises: 0008_phase9_crm_core

Table creation order matters here even though there is no real circular
dependency: quotes.generated_document_id references generated_documents.id,
so generated_documents (and its own dependency, document_template_versions,
and that table's dependency, document_templates) must all exist before
quotes is created.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0009_phase10_quotes_templates"
down_revision = "0008_phase9_crm_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("template_code", sa.String(64), nullable=False),
        sa.Column("template_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("license_id", "template_code"),
    )
    op.create_index("ix_document_templates_license_id", "document_templates", ["license_id"])

    op.create_table(
        "document_template_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "template_id", UUID(as_uuid=True),
            sa.ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("source_docx_path", sa.String(512), nullable=False),
        sa.Column("intermediate_model", JSONB, nullable=False),
        sa.Column("mapping_schema", JSONB, nullable=False),
        sa.Column("compiled_template_path", sa.String(512), nullable=False),
        sa.Column("renderer", sa.String(32), nullable=False, server_default="smartbrowz"),
        sa.Column("renderer_mode", sa.String(32), nullable=False, server_default="html_convert"),
        sa.Column("smartbrowz_template_id", sa.String(128)),
        sa.Column(
            "created_by", UUID(as_uuid=True),
            sa.ForeignKey("license_members.id", ondelete="RESTRICT"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("template_id", "version"),
    )
    op.create_index(
        "ix_document_template_versions_template_id", "document_template_versions", ["template_id"]
    )

    op.create_table(
        "generated_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("source_entity_type", sa.String(32), nullable=False),
        sa.Column("source_entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "template_version_id", UUID(as_uuid=True),
            sa.ForeignKey("document_template_versions.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("data_snapshot", JSONB, nullable=False),
        sa.Column("output_path", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("renderer", sa.String(32), nullable=False, server_default="smartbrowz"),
        sa.Column(
            "generated_by", UUID(as_uuid=True),
            sa.ForeignKey("license_members.id", ondelete="RESTRICT"),
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_generated_documents_license_id", "generated_documents", ["license_id"])

    op.create_table(
        "quotes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("quote_id", sa.String(32), nullable=False),
        sa.Column(
            "deal_id", UUID(as_uuid=True),
            sa.ForeignKey("deals.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column(
            "generated_document_id", UUID(as_uuid=True),
            sa.ForeignKey("generated_documents.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "owner_member_id", UUID(as_uuid=True),
            sa.ForeignKey("license_members.id", ondelete="RESTRICT"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("license_id", "quote_id"),
    )
    op.create_index("ix_quotes_license_id", "quotes", ["license_id"])
    op.create_index("ix_quotes_deal_id", "quotes", ["deal_id"])


def downgrade() -> None:
    op.drop_index("ix_quotes_deal_id", table_name="quotes")
    op.drop_index("ix_quotes_license_id", table_name="quotes")
    op.drop_table("quotes")
    op.drop_index("ix_generated_documents_license_id", table_name="generated_documents")
    op.drop_table("generated_documents")
    op.drop_index(
        "ix_document_template_versions_template_id", table_name="document_template_versions"
    )
    op.drop_table("document_template_versions")
    op.drop_index("ix_document_templates_license_id", table_name="document_templates")
    op.drop_table("document_templates")
