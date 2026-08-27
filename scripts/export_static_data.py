# -*- coding: utf-8 -*-
"""导出数据库数据为静态文件（供 Netlify 等静态托管，前端直接读取）

用法：
    # 默认：导出本地开发库 instance/parking.db → data/
    python scripts/export_static_data.py

    # 从在线库导出（渲染最新数据快照）
    DATABASE_URL=postgresql+psycopg://用户:密码@主机:5432/库名 python scripts/export_static_data.py

输出（data/ 目录）：
    data.js    —— window.PARKING_DATA 全局变量，<script> 引入零 CORS 限制（推荐）
    data.json  —— 纯 JSON（供 fetch 使用，Netlify 静态文件默认允许跨域）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db  # noqa: E402
from sqlalchemy import text  # noqa: E402


def _json_default(o):
    if hasattr(o, 'isoformat'):
        return o.isoformat()
    return str(o)


def export(out_dir='data'):
    os.makedirs(out_dir, exist_ok=True)
    with app.app_context():
        from sqlalchemy import inspect
        tables = sorted(inspect(db.engine).get_table_names())
        all_data = {}
        total = 0
        print('导出数据:')
        for t in tables:
            if t == 'alembic_version':
                continue
            rows = db.session.execute(
                text(f'SELECT * FROM "{t}"')).mappings().all()
            all_data[t] = [dict(r) for r in rows]
            total += len(rows)
            print(f'  {t}: {len(rows)} 行')

        js = 'window.PARKING_DATA = ' + json.dumps(
            all_data, ensure_ascii=False, default=_json_default) + ';\n'
        with open(os.path.join(out_dir, 'data.js'), 'w', encoding='utf-8') as f:
            f.write(js)
        with open(os.path.join(out_dir, 'data.json'), 'w', encoding='utf-8') as f:
            json.dump(all_data, f, ensure_ascii=False, default=_json_default)

        print(f'导出完成: {out_dir}/data.js、{out_dir}/data.json（共 {total} 行）')
        print(f'文件大小: data.js {os.path.getsize(os.path.join(out_dir, "data.js")) // 1024} KB, '
              f'data.json {os.path.getsize(os.path.join(out_dir, "data.json")) // 1024} KB')


if __name__ == '__main__':
    export()
