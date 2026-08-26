# -*- coding: utf-8 -*-
"""SQLite → 在线数据库（PostgreSQL）一键迁移脚本

用法：
    # 默认：instance/parking.db → 环境变量 DATABASE_URL 指向的库
    python scripts/migrate_to_online.py

    # 显式指定源/目标
    python scripts/migrate_to_online.py --sqlite instance/parking.db --pg postgresql+psycopg://user:pass@host:5432/db

    # 目标库已有数据时清空重建（谨慎）
    python scripts/migrate_to_online.py --drop-first

    # 本地演练（无 PG 时验证复制逻辑）：SQLite → SQLite
    python scripts/migrate_to_online.py --pg sqlite:///instance/copy.db

建议：目标为 PG 时先用 `flask db upgrade` 建表（含 Identity 主键），再跑本脚本复制数据。
目标库无表时本脚本会用 create_all 兜底建表（SERIAL 主键，同样可用）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

# 按外键依赖的拓扑顺序复制（环形外键的两个表相邻，数据中相关字段多为 NULL）
TABLES = [
    'zones', 'holiday_configs', 'peak_hour_stats', 'floor_gates', 'vehicles',
    'charging_piles', 'parking_spots', 'parking_assignments', 'parking_records',
    'charging_records', 'user_profiles', 'devices', 'device_maintenances',
    'energy_records', 'event_logs', 'fire_alarm_stats', 'event_refresh_marks',
]


def parse_args():
    p = argparse.ArgumentParser(description='SQLite → 在线数据库迁移',
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--sqlite', default='instance/parking.db',
                   help='源 SQLite 文件（默认 instance/parking.db）')
    p.add_argument('--pg', default=os.getenv('DATABASE_URL'),
                   help='目标数据库连接串（默认读环境变量 DATABASE_URL）')
    p.add_argument('--drop-first', action='store_true',
                   help='目标库已有数据时清空后重建')
    args = p.parse_args()
    if not args.pg:
        print('错误：未指定目标数据库（--pg 或环境变量 DATABASE_URL）')
        sys.exit(1)
    return args


def copy_table(src_conn, dst_conn, table):
    rows = src_conn.execute(text(f'SELECT * FROM "{table}"')).mappings().all()
    if not rows:
        print(f'  {table}: 0 行（跳过）')
        return 0
    cols = list(rows[0].keys())
    col_sql = ', '.join(f'"{c}"' for c in cols)
    ph_sql = ', '.join(f':{c}' for c in cols)
    stmt = text(f'INSERT INTO "{table}" ({col_sql}) VALUES ({ph_sql})')
    n = 0
    # 每 500 行一个事务，可断点重试（已提交的批次不会重复）
    for i in range(0, len(rows), 500):
        dst_conn.execute(stmt, [dict(r) for r in rows[i:i + 500]])
        dst_conn.commit()
        n += len(rows[i:i + 500])
    print(f'  {table}: {n} 行')
    return n


def reset_sequences(dst_conn, inspector, tables):
    """PG 自增序列重置：显式插入 id 后序列不会自动跟进，必须 setval"""
    if dst_conn.dialect.name != 'postgresql':
        return
    print('重置 PG 自增序列:')
    for t in tables:
        if not inspector.has_table(t):
            continue
        cols = [c['name'] for c in inspector.get_columns(t)]
        if 'id' not in cols:
            continue
        seq = dst_conn.execute(
            text(f"SELECT pg_get_serial_sequence('{t}', 'id')")).scalar()
        if not seq:
            print(f'  {t}: 无序列（跳过）')
            continue
        max_id = dst_conn.execute(
            text(f'SELECT COALESCE(MAX(id), 0) FROM "{t}"')).scalar()
        dst_conn.execute(text(
            f"SELECT setval('{seq}', {max_id}, {'true' if max_id > 0 else 'false'})"))
        dst_conn.commit()
        print(f'  {t}: 序列重置到 {max_id}')


def main():
    args = parse_args()
    src = create_engine(f'sqlite:///{args.sqlite}')
    dst = create_engine(args.pg)

    src_conn = src.connect()
    dst_conn = dst.connect()
    dst_insp = inspect(dst)

    src_tables = {t for t in inspect(src).get_table_names() if t in TABLES}
    existing = [t for t in dst_insp.get_table_names() if t != 'alembic_version']

    if existing and not args.drop_first:
        print(f'目标库已有表 {existing}，中止（用 --drop-first 清空后重迁）')
        sys.exit(1)

    if existing and args.drop_first:
        print(f'清空目标库现有表: {existing}')
        from models import db
        db.metadata.drop_all(bind=dst_conn)

    # 建表兜底（正式部署推荐先 flask db upgrade，这里保证脚本可独立运行）
    if not dst_insp.has_table('vehicles'):
        print('目标库无表，create_all 兜底建表（正式部署建议先用 flask db upgrade 建表）')
        from models import db
        db.metadata.create_all(bind=dst_conn)

    print(f'开始复制 {len(src_tables)} 张表: {args.sqlite} -> {args.pg}')
    total = 0
    for t in TABLES:
        if t not in src_tables:
            print(f'  {t}: 源库无此表（跳过）')
            continue
        total += copy_table(src_conn, dst_conn, t)

    reset_sequences(dst_conn, dst_insp, TABLES)
    print(f'迁移完成，共复制 {total} 行')
    src_conn.close()
    dst_conn.close()


if __name__ == '__main__':
    main()
