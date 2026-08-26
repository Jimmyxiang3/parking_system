# 停车楼智能管理系统（后端）

停车楼智能管理系统的 Flask 后端，包含车位分配、车辆管理、能耗监控、告警事件、充电桩等模块，并内置前端大屏页面。

## 技术栈

- Python Flask 3.0 + Flask-SQLAlchemy
- SQLite 数据库（`instance/parking.db`，仓库已内置演示数据）
- paho-mqtt（可选，默认关闭）
- 前端 Vue 构建产物（`dist/`，由 Flask 直接托管）

## 快速开始

```powershell
# 1. 创建并激活虚拟环境（Python 3.8+）
python -m venv venv
venv\Scripts\Activate.ps1

# 2. 安装依赖（国内可加清华镜像 -i https://pypi.tuna.tsinghua.edu.cn/simple）
pip install -r requirements.txt

# 3. 启动后端（数据库已随仓库提供，无需初始化）
python app.py
```

启动成功后浏览器访问：

- 前端大屏：<http://127.0.0.1:5000/>
- 健康检查：<http://127.0.0.1:5000/api/hello>

> 如果删除了数据库或想重置数据，先执行 `python init_data.py` 重新初始化（自动建表并填充 420 个车位、车辆、设备等演示数据）。

## 文档

- [使用说明.md](使用说明.md) — 面向团队成员的完整使用说明（克隆、部署、接口、常见问题）
- [docs/后端启动与依赖安装教程.md](docs/后端启动与依赖安装教程.md) — 详细的依赖安装与启动教程
- [使用说明.txt](使用说明.txt) — PyInstaller 打包版 exe 的使用说明

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
├── instance/parking.db     # SQLite 数据库（含演示数据）
├── dist/                   # 前端构建产物（Flask 托管）
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

## 部署打包（可选）

需要免 Python 环境的 exe 版本时：

```powershell
pip install pyinstaller
pyinstaller parking_system.spec
```

产物在 `dist/parking_system.exe`，双击即运行。
