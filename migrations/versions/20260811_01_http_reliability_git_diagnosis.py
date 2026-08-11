"""HTTP reliability, learning and Git diagnosis models.

Revision ID: 20260811_01
Revises: 20260810_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260811_01"
down_revision = "20260810_01"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "change_event" in _tables():
        change_columns = _columns("change_event")
        with op.batch_alter_table("change_event") as batch:
            if "repository_id" not in change_columns:
                batch.add_column(sa.Column("repository_id", sa.String(36)))
                batch.create_index("ix_change_event_repository_id", ["repository_id"])
            if "commit_sha" not in change_columns:
                batch.add_column(
                    sa.Column("commit_sha", sa.String(64), nullable=False, server_default="")
                )
            if "image_version" not in change_columns:
                batch.add_column(
                    sa.Column("image_version", sa.String(200), nullable=False, server_default="")
                )
            if "environment" not in change_columns:
                batch.add_column(
                    sa.Column(
                        "environment", sa.String(32), nullable=False, server_default="production"
                    )
                )

    if "http_access_site_config" in _tables():
        columns = _columns("http_access_site_config")
        with op.batch_alter_table("http_access_site_config") as batch:
            if "diagnostic_business_line_id" not in columns:
                batch.add_column(sa.Column("diagnostic_business_line_id", sa.String(36)))
                batch.create_index(
                    "ix_http_access_site_config_diagnostic_business_line_id",
                    ["diagnostic_business_line_id"],
                )
            if "repository_id" not in columns:
                batch.add_column(sa.Column("repository_id", sa.String(36)))
                batch.create_index("ix_http_access_site_config_repository_id", ["repository_id"])
            if "deployment_service_name" not in columns:
                batch.add_column(
                    sa.Column(
                        "deployment_service_name",
                        sa.String(200),
                        nullable=False,
                        server_default="",
                    )
                )

        # Existing deployments were monitored before site governance existed.
        # Restore them once, while keeping future discoveries on model default
        # "observe" and preserving every explicit disabled choice.
        op.execute(
            sa.text("""
            UPDATE http_access_site_config
            SET environment = 'test', monitoring_mode = 'disabled'
            WHERE (
                LOWER(site) LIKE '%-test.%' OR LOWER(site) LIKE 'test-%' OR
                LOWER(site) LIKE '%.test.%' OR LOWER(site) LIKE '%-dev.%' OR
                LOWER(site) LIKE '%.dev.%' OR LOWER(site) LIKE 'dev-%' OR
                LOWER(site) LIKE 'dev.%' OR LOWER(site) LIKE '%-uat.%' OR
                LOWER(site) LIKE '%.uat.%' OR LOWER(site) LIKE 'uat.%' OR
                LOWER(site) LIKE '%-staging.%' OR LOWER(site) LIKE '%.staging.%' OR
                LOWER(site) LIKE 'staging.%' OR LOWER(site) LIKE '%-pre.%' OR
                LOWER(site) LIKE '%.pre.%' OR LOWER(site) LIKE 'pre.%' OR
                LOWER(site) LIKE '%-sandbox.%' OR LOWER(site) LIKE '%.sandbox.%' OR
                LOWER(site) LIKE 'sandbox.%'
            )
        """)
        )
        op.execute(
            sa.text("""
            UPDATE http_access_site_config SET role = CASE
                WHEN LOWER(site) LIKE '%tong%' THEN 'app'
                WHEN LOWER(site) LIKE '%account%' THEN 'account'
                WHEN LOWER(site) LIKE '%billing%'
                  OR LOWER(site) LIKE '%gpay%'
                  OR LOWER(site) LIKE '%payment%'
                  OR LOWER(site) LIKE '%-pay.%'
                  OR LOWER(site) LIKE 'pay.%' THEN 'payment'
                WHEN LOWER(site) LIKE '%front%' THEN 'front'
                WHEN LOWER(site) LIKE '%cdn%' OR LOWER(site) LIKE '%download%' THEN 'cdn_download'
                ELSE role END
        """)
        )
        op.execute(
            sa.text("""
            UPDATE http_access_site_config
            SET monitoring_mode = 'enabled'
            WHERE monitoring_mode = 'observe' AND environment = 'production'
        """)
        )

    if "http_access_incident" not in _tables():
        op.create_table(
            "http_access_incident",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("site", sa.String(253), nullable=False),
            sa.Column("source", sa.String(32), nullable=False),
            sa.Column("sources", sa.Text(), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("priority", sa.String(8), nullable=False),
            sa.Column("route_key", sa.String(520), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("recovered_at", sa.DateTime(timezone=True)),
            sa.Column("last_notified_at", sa.DateTime(timezone=True)),
            sa.Column("last_digest_at", sa.DateTime(timezone=True)),
            sa.Column("last_notified_impact", sa.Float(), nullable=False),
            sa.Column("current_impact", sa.Float(), nullable=False),
            sa.Column("peak_impact", sa.Float(), nullable=False),
            sa.Column("notification_pending", sa.Boolean(), nullable=False),
            sa.Column("notification_count", sa.Integer(), nullable=False),
            sa.Column("evidence_json", sa.Text(), nullable=False),
            sa.Column("diagnosis_json", sa.Text(), nullable=False),
            sa.Column("feedback", sa.String(24), nullable=False),
            sa.Column("feedback_comment", sa.Text(), nullable=False),
            sa.Column("handled_by", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "fingerprint", name="uq_http_access_incident_fingerprint"
            ),
        )
        op.create_index("ix_http_access_incident_tenant_id", "http_access_incident", ["tenant_id"])
        op.create_index("ix_http_access_incident_site", "http_access_incident", ["site"])
        op.create_index(
            "ix_http_access_incident_tenant_status", "http_access_incident", ["tenant_id", "status"]
        )
    elif "last_digest_at" not in _columns("http_access_incident"):
        with op.batch_alter_table("http_access_incident") as batch:
            batch.add_column(sa.Column("last_digest_at", sa.DateTime(timezone=True)))

    if "http_access_learning_rule" not in _tables():
        op.create_table(
            "http_access_learning_rule",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("site", sa.String(253), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("disposition", sa.String(24), nullable=False),
            sa.Column("source", sa.String(24), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True)),
            sa.Column("hit_count", sa.Integer(), nullable=False),
            sa.Column("last_hit_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "fingerprint", name="uq_http_access_learning_fingerprint"
            ),
        )
        op.create_index(
            "ix_http_access_learning_rule_tenant_id", "http_access_learning_rule", ["tenant_id"]
        )
        op.create_index("ix_http_access_learning_rule_site", "http_access_learning_rule", ["site"])

    if "git_repository_config" not in _tables():
        op.create_table(
            "git_repository_config",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("clone_url", sa.String(1000), nullable=False),
            sa.Column("default_branch", sa.String(32), nullable=False),
            sa.Column("credential_ref", sa.String(120), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("last_sync_status", sa.String(24), nullable=False),
            sa.Column("last_sync_error", sa.String(500), nullable=False),
            sa.Column("last_synced_at", sa.DateTime(timezone=True)),
            sa.Column("last_commit_sha", sa.String(64), nullable=False),
            sa.Column("cache_size_bytes", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", "clone_url", name="uq_git_repository_tenant_url"),
        )
        op.create_index(
            "ix_git_repository_config_tenant_id", "git_repository_config", ["tenant_id"]
        )

    if "git_deployment_revision" not in _tables():
        op.create_table(
            "git_deployment_revision",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("repository_id", sa.String(36), nullable=False),
            sa.Column("service_name", sa.String(200), nullable=False),
            sa.Column("branch", sa.String(32), nullable=False),
            sa.Column("commit_sha", sa.String(64), nullable=False),
            sa.Column("previous_commit_sha", sa.String(64), nullable=False),
            sa.Column("image_version", sa.String(200), nullable=False),
            sa.Column("environment", sa.String(32), nullable=False),
            sa.Column("operator", sa.String(100), nullable=False),
            sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_git_deployment_revision_tenant_id", "git_deployment_revision", ["tenant_id"]
        )
        op.create_index(
            "ix_git_deployment_revision_repository_id", "git_deployment_revision", ["repository_id"]
        )
        op.create_index(
            "ix_git_deployment_revision_deployed_at", "git_deployment_revision", ["deployed_at"]
        )
        op.create_index(
            "ix_git_deploy_tenant_service_time",
            "git_deployment_revision",
            ["tenant_id", "service_name", "deployed_at"],
        )


def downgrade() -> None:
    for table in (
        "git_deployment_revision",
        "git_repository_config",
        "http_access_learning_rule",
        "http_access_incident",
    ):
        if table in _tables():
            op.drop_table(table)
    if "http_access_site_config" in _tables():
        columns = _columns("http_access_site_config")
        with op.batch_alter_table("http_access_site_config") as batch:
            if "repository_id" in columns:
                batch.drop_index("ix_http_access_site_config_repository_id")
                batch.drop_column("repository_id")
            if "diagnostic_business_line_id" in columns:
                batch.drop_index("ix_http_access_site_config_diagnostic_business_line_id")
                batch.drop_column("diagnostic_business_line_id")
            if "deployment_service_name" in columns:
                batch.drop_column("deployment_service_name")
    if "change_event" in _tables():
        columns = _columns("change_event")
        with op.batch_alter_table("change_event") as batch:
            if "environment" in columns:
                batch.drop_column("environment")
            if "image_version" in columns:
                batch.drop_column("image_version")
            if "commit_sha" in columns:
                batch.drop_column("commit_sha")
            if "repository_id" in columns:
                batch.drop_index("ix_change_event_repository_id")
                batch.drop_column("repository_id")
