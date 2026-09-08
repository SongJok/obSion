"""修复受治理 Memory 的合法撤销，同时保留来源不可变与终态约束。"""

import sqlalchemy as sa
from alembic import op

revision = "a83d14e25f36"
down_revision = "a82c03d14e25"
branch_labels = None
depends_on = None


def _replace_guard(*, allow_revoked: bool) -> None:
    revoked_transition = (
        "OR (OLD.status IN ('CANDIDATE', 'APPROVED') AND NEW.status = 'REVOKED')"
        if allow_revoked
        else ""
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION obsion_guard_memory_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'governed memory cannot be deleted directly'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF OLD.organization_id IS DISTINCT FROM NEW.organization_id
             OR OLD.scope IS DISTINCT FROM NEW.scope
             OR OLD.owner_ref IS DISTINCT FROM NEW.owner_ref
             OR OLD.content::jsonb IS DISTINCT FROM NEW.content::jsonb
             OR OLD.dedupe_key IS DISTINCT FROM NEW.dedupe_key
             OR OLD.sensitivity IS DISTINCT FROM NEW.sensitivity
             OR OLD.policy_decision_id IS DISTINCT FROM NEW.policy_decision_id
             OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'governed memory content and lineage are immutable'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF OLD.status IS DISTINCT FROM NEW.status
             AND NOT (
               (OLD.status = 'CANDIDATE' AND NEW.status IN ('APPROVED', 'REJECTED', 'EXPIRED'))
               OR (OLD.status = 'APPROVED' AND NEW.status = 'EXPIRED')
               {revoked_transition}
             ) THEN
            RAISE EXCEPTION 'invalid governed memory status transition: % -> %',
              OLD.status, NEW.status
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )


def upgrade() -> None:
    # SQLite 本地 ORM 测试没有该 PostgreSQL 触发器，不伪装成数据库不变量验收。
    if op.get_bind().dialect.name == "postgresql":
        op.execute("LOCK TABLE memories IN SHARE ROW EXCLUSIVE MODE")
        _replace_guard(allow_revoked=True)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    connection.execute(sa.text("LOCK TABLE memories IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text("SELECT count(*) FROM memories WHERE status = 'REVOKED'")):
        # 不能把撤销改成拒绝/过期来迁就旧版，更不能让已撤销内容重新可用。
        raise RuntimeError("Cannot downgrade the Memory guard while REVOKED records exist")
    _replace_guard(allow_revoked=False)
