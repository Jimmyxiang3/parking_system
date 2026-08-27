# 停车楼智能管理系统（后端）

停车楼智能管理系统的 Flask 后端，包含车位分配、车辆管理、能耗监控、告警事件、充电桩等模块，并内置前端大屏页面。

## 技术栈

- Python Flask 3.0 + Flask-SQLAlchemy
- 开发环境 SQLite（`instance/parking.db`，不进仓库）；**生产环境 PostgreSQL**（通过 `DATABASE_URL` 环境变量连接）
- Flask-Migrate/Alembic 数据库迁移
- paho-mqtt（可选，默认关闭）
- 纯后端 API 服务（前端页面由前端项目/托管平台负责，本仓库不含前端产物）

## 快速开始

```powershell
# 1. 创建并激活虚拟环境（Python 3.8+）
python -m venv venv
venv\Scripts\Activate.ps1

# 2. 安装依赖（国内可加清华镜像 -i https://pypi.tuna.tsinghua.edu.cn/simple）
pip install -r requirements.txt

# 3. 建表（Flask-Migrate 迁移，首次运行执行一次）
set FLASK_APP=app.py
flask db upgrade

# 4. （可选）填充演示数据：420 个车位、车辆、设备等（幂等，可重复执行）
python init_data.py

# 5. 启动后端
python app.py
```

启动成功后验证接口：

- 健康检查：<http://127.0.0.1:5000/api/hello>
- 车位数据：<http://127.0.0.1:5000/api/spots>

> 想重置演示数据：`python init_data.py --force`（清空后重新填充）。
> 环境变量见 [.env.example](.env.example)；生产环境必须设置 `APP_ENV=production` + `DATABASE_URL` + `SECRET_KEY` + `CORS_ALLOWED_ORIGINS`，详见部署文档。

## 文档

- [使用说明.md](使用说明.md) — 面向团队成员的完整使用说明（克隆、部署、接口、常见问题）
- [docs/DEPLOY_RENDER.md](docs/DEPLOY_RENDER.md) — **生产部署指南**（Render/Railway + 在线 PostgreSQL）
- [docs/后端启动与依赖安装教程.md](docs/后端启动与依赖安装教程.md) — 详细的依赖安装与启动教程

## 项目结构

```
parking_system/
├── app.py                  # 后端主程序（启动入口）
├── models.py               # SQLAlchemy 数据模型
├── init_data.py            # 数据库初始化脚本（建表 + 演示数据）
├── parking_assigner.py     # 车位分配算法
├── stereo_astar.py         # 立体停车楼路径规划（A*）
├── parking_graph.py        # 停车楼图结构
├── mqtt_client.py          # MQTT 客户端（可选）
├── dynamic_reroute.py      # 动态改道逻辑
├── requirements.txt        # Python 依赖清单
├── init_data.py            # 演示数据初始化（幂等；生产数据不入仓库）
├── instance/               # SQLite 开发数据库（已被 gitignore，不进仓库）
├── migrations/             # Alembic 迁移文件（flask db upgrade 建表/升级）
├── scripts/migrate_to_online.py  # SQLite → PostgreSQL 一键迁移脚本
├── wsgi.py                 # 生产 WSGI 入口（gunicorn）
├── docs/                   # 文档
└── parking_system.spec     # PyInstaller 打包配置
```

## 常用接口速查

| 接口 | 说明 |
| --- | --- |
| `/api/hello` | 健康检查 |
| `/api/spots` | 车位列表（支持 `?floor=` 过滤） |
| `/api/devices` | 设备列表（支持 `?type=`、`?floor=` 过滤） |
| `/api/energy/24h` | 24 小时能耗记录 |
| `/api/events` | 告警/事件列表 |
| `/api/fire/emergency` | 消防应急处置总览 |

## 部署打包（可选）

需要免 Python 环境的 exe 版本时：

```powershell
pip install pyinstaller
pyinstaller parking_system.spec
```

产物在 `dist/parking_system.exe`，双击即运行。
