# 生产部署指南（Render / Railway）

本文档介绍把停车楼系统后端部署到 Render（或 Railway）的完整步骤：在线 PostgreSQL + gunicorn + HTTPS + CORS 白名单。

## 1. 架构

```text
GitHub
  └── 后端代码、迁移脚本、部署配置（无数据库文件、无密钥）

Render PostgreSQL（在线数据库）
  └── 车位、车辆、设备、告警、能耗等正式数据（持久化，重启不丢）

Render Web Service（后端云服务）
  ├── 从 GitHub 拉取代码，自动部署
  ├── 从环境变量读取 DATABASE_URL
  └── gunicorn 提供 HTTPS 服务

Netlify
  └── 部署前端，请求后端云服务（域名加入后端 CORS 白名单）
```

## 2. 环境变量（生产必填）

| 变量 | 说明 |
| --- | --- |
| `APP_ENV` | **必须设为 `production`**。不设的话会退回 debug 模式 + SQLite，数据全丢 |
| `DATABASE_URL` | 创建 Render PostgreSQL 后自动注入；也可手动填 `postgresql+psycopg://用户:密码@主机:5432/库名` |
| `SECRET_KEY` | 长随机字符串（Render 环境变量界面有 Generate 按钮） |
| `CORS_ALLOWED_ORIGINS` | 允许的前端域名，逗号分隔，如 `https://your-site.netlify.app` |
| `FLASK_APP` | `app.py`（供 flask db 命令使用） |

生产环境缺失 `DATABASE_URL` / `SECRET_KEY` / `CORS_ALLOWED_ORIGINS` 任何一个，后端启动会直接报错拒绝运行（防呆设计）。

> **密钥安全**：以上敏感值只配置在 Render 环境变量面板，绝不提交到 GitHub（`.env` 已被 gitignore）。

## 3. Render 部署步骤

### 方式 A：Blueprint 一键部署（推荐）

仓库根目录已提供 [render.yaml](../../render.yaml)，自动创建 PostgreSQL + Web Service + 环境变量 + 数据库关联，**启动时自动建表并填充演示数据**（`flask db upgrade && python init_data.py`，均幂等）。

1. Render 控制台 → **New → Blueprint** → 选择 GitHub 仓库 `Jimmyxiang3/parking_system` → **Apply**
2. 等待服务部署完成（首次约 5-10 分钟），访问 `https://parking-system-xxx.onrender.com/api/hello` 验证
3. 如果之前手动建过 PostgreSQL（方式 B 的步骤 1），Blueprint 会再建一个 `parking-db`，旧的那个删掉即可
4. 前端部署到 Netlify 后，把 `CORS_ALLOWED_ORIGINS` 改成真实域名（Render 控制台 → 服务 → Environment）

### 方式 B：手动逐步部署

1. **建 PostgreSQL**：Render 控制台 → New → PostgreSQL（免费档即可），创建后复制连接串（Internal Database URL 供 Web Service 使用）。
2. **建 Web Service**：New → Web Service → 连接 GitHub 仓库 `Jimmyxiang3/parking_system`。
   - Build Command：`pip install -r requirements.txt`
   - Start Command：`flask db upgrade && python init_data.py && gunicorn -w 2 -b 0.0.0.0:$PORT wsgi:app`
   - Python 版本：3.11 或以上
3. **配置环境变量**：按上表填写（`DATABASE_URL` 用步骤 1 的 Internal URL）。
4. **首次部署**：启动命令已自动执行建表 + 演示数据填充，无需额外 Shell 操作。

### 数据库结构变更（后续）

改了 `models.py` 之后：

```bash
# 本地生成迁移文件并提交到 GitHub
flask db migrate -m "变更说明"
git push

# 在 Render 控制台 → 服务 → Shell 执行（不删数据）
flask db upgrade
```

> 迁移是**唯一**的建表/升级入口（迁移文件含 PG 自增主键 Identity 定义）。不要用 `db.create_all()`。

### （可选）迁移旧 SQLite 数据

如果有本机 `instance/parking.db` 数据要导入在线库，本地执行：

```powershell
python scripts/migrate_to_online.py --pg "postgresql+psycopg://用户:密码@主机:5432/库名"
```

## 4. 验证清单

| 检查项 | 方法 |
| --- | --- |
| 健康检查 | 访问 `https://你的服务.onrender.com/api/hello` 返回 code 200 |
| 数据可读 | `/api/spots`、`/api/devices`、`/api/events`、`/api/fire/emergency` 正常返回 |
| 数据持久化 | 写入一条数据（如 POST `/api/spots`）→ Render 重启实例 → 数据仍在 |
| 自增正常 | POST 写入后新记录 id 连续增长（迁移脚本跑过 setval 后不会撞主键） |
| CORS | 从 Netlify 域名请求正常；浏览器控制台无 CORS 报错 |
| debug 关闭 | 接口报错时不应暴露 Werkzeug 调试页 |

## 5. Railway 部署（附）

- 建 PostgreSQL 插件，Railway 会自动注入 `DATABASE_URL` 变量
- 建服务时选 GitHub 仓库，或根目录放 `Procfile`：

  ```text
  web: gunicorn -w 2 -b 0.0.0.0:${PORT:-5000} wsgi:app
  ```

- 其余步骤（环境变量、`flask db upgrade`、验证）与 Render 相同

## 6. 日常更新

- **代码更新**：push 到 GitHub main 分支，Render 自动重新部署
- **数据库结构变更**：改 `models.py` → 本地 `flask db migrate -m "说明"` → 提交迁移文件 → 部署后在 Render Shell 执行 `flask db upgrade`（**不会删数据**）
- **数据不依赖 GitHub**：在线数据库与代码仓库完全分离，重新部署/重启都不会丢数据

## 7. 常见问题

| 问题 | 处理 |
| --- | --- |
| 启动报 `APP_ENV=production 时必须设置 DATABASE_URL` | 环境变量没配全，按第 2 节补齐 |
| 接口报"表不存在" | 忘了执行 `flask db upgrade` |
| 前端跨域被拦 | `CORS_ALLOWED_ORIGINS` 没写对（要带 https:// 前缀，多个域名逗号分隔） |
| 数据莫名变回演示数据 | `APP_ENV` 没设 `production`，退回了本地 SQLite |
| 事件数据不刷新 | 生产环境有意禁用了演示用的随机事件轮换（`_refresh_event_times`），真实事件由设备上报写入 |
