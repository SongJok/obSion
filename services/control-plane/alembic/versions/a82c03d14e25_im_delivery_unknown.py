"""保留 IM 投递不确定结果，禁止将历史失败当成可重发证明。"""

import sqlalchemy as sa
from alembic import op

revision = "a82c03d14e25"
down_revision = "a81b92c03d14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("im_deliveries") as batch:
        batch.drop_constraint(op.f("ck_im_deliveries_valid_status"), type_="check")
        batch.create_check_constraint(
            op.f("ck_im_deliveries_valid_status"),
            "status IN ('PENDING', 'SENT', 'FAILED', 'UNKNOWN')",
        )
    # PENDING 可能已发送；FAILED 没有厂商拒绝证明，不能恢复发送资格。
    op.execute(
        sa.text("UPDATE im_deliveries SET status = 'UNKNOWN' WHERE status IN ('PENDING', 'FAILED')")
    )


def downgrade() -> None:
    # 旧版会重新发送 FAILED/PENDING；存在未对账记录时禁止安全性降级。
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE im_deliveries IN ACCESS EXCLUSIVE MODE"))
    pending = connection.scalar(
        sa.text("SELECT count(*) FROM im_deliveries WHERE status <> 'SENT'")
    )
    if pending:
        raise RuntimeError("Reconcile all non-SENT IM deliveries before downgrading")
    with op.batch_alter_table("im_deliveries") as batch:
        batch.drop_constraint(op.f("ck_im_deliveries_valid_status"), type_="check")
        batch.create_check_constraint(
            op.f("ck_im_deliveries_valid_status"),
            "status IN ('PENDING', 'SENT', 'FAILED')",
        )
