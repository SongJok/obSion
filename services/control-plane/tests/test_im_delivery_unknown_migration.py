import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_delivery_migration_preserves_receipts_and_blocks_unsafe_downgrade() -> None:
    path = (
        Path(__file__).resolve().parents[1] / "alembic/versions/a82c03d14e25_im_delivery_unknown.py"
    )
    spec = importlib.util.spec_from_file_location("delivery_unknown_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            metadata = sa.MetaData()
            table = sa.Table(
                "im_deliveries",
                metadata,
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("status", sa.String(32), nullable=False),
                sa.Column("vendor_message_id", sa.String(500)),
                sa.CheckConstraint(
                    "status IN ('PENDING', 'SENT', 'FAILED')",
                    name="ck_im_deliveries_valid_status",
                ),
            )
            metadata.create_all(connection)
            connection.execute(
                table.insert(),
                [
                    {"id": 1, "status": "PENDING", "vendor_message_id": None},
                    {"id": 2, "status": "FAILED", "vendor_message_id": None},
                    {"id": 3, "status": "SENT", "vendor_message_id": "real-vendor-id"},
                ],
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
                rows = connection.execute(sa.select(table).order_by(table.c.id)).all()
                assert rows == [
                    (1, "UNKNOWN", None),
                    (2, "UNKNOWN", None),
                    (3, "SENT", "real-vendor-id"),
                ]
                # 升级后新产生的 PENDING/FAILED 也不能交回旧版自动重发。
                for unsafe_status in ("UNKNOWN", "PENDING", "FAILED"):
                    connection.execute(
                        table.update().where(table.c.id.in_([1, 2])).values(status=unsafe_status)
                    )
                    with pytest.raises(RuntimeError, match="Reconcile all non-SENT"):
                        migration.downgrade()
                connection.execute(
                    table.update().where(table.c.status != "SENT").values(status="SENT")
                )
                migration.downgrade()
                checks = sa.inspect(connection).get_check_constraints("im_deliveries")
                assert "UNKNOWN" not in checks[0]["sqltext"]
    finally:
        engine.dispose()
