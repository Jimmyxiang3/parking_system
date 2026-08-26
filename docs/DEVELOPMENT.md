# 🚀 二次开发与部署运行指南

> 版本: 0.2.0 | 更新: 2025-07

---

## 一、快速开始

### 1.1 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| **Node.js** | 16.x ~ 18.x (推荐 18 LTS) | v20+ 部分依赖兼容性问题 |
| **npm** | 8.x+ | 随 Node 自带 |
| **Git** | 2.x+ | 版本管理 |
| **AirCity Cloud** | (内网) | 必须可访问 AirCity 服务才能加载3D场景 |

### 1.2 克隆与安装

```bash
# 1. 克隆仓库
git clone https://jihulab.com/system1/system.git parking-system
cd parking-system

# 2. 复制环境变量模板
cp .env.example .env.local

# 3. 编辑 .env.local，填入实际 AirCity 地址和 API 地址
# (详见下方 二、环境配置)

# 4. 安装依赖 (推荐淘宝镜像)
npm install --registry=https://registry.npmmirror.com

# 如果 sass 编译报错，执行:
# npm rebuild node-sass  (仅当仍使用旧版 node-sass)
```

### 1.3 启动开发服务器

```bash
npm run serve
# 浏览器访问 http://localhost:8080
```

### 1.4 构建生产版本

```bash
npm run build          # 普通构建
npm run build:report   # 构建 + 打包分析报告
```

产物在 `dist/` 目录，部署到任意静态服务器即可（Nginx/Apache/CDN）。

---

## 二、环境配置 (.env.local)

所有环境相关配置统一在 `.env.local` 中管理。创建该文件并覆盖以下关键变量：

```bash
# ===== 必填 =====

# AirCity 引擎连接地址 (需根据实际内网环境修改)
VUE_APP_AIRCITY_MANAGER=192.168.5.197:30005
VUE_APP_AIRCITY_PLAYER=192.168.5.197:30005
VUE_APP_AIRCITY_USERNAME=admin
VUE_APP_AIRCITY_PASSWORD=your_password_here

# 后端 API 地址
VUE_APP_API_TARGET=http://your-api-server:8020

# WebSocket 中间服务器地址 (UE ↔ Web 路由同步)
VUE_APP_WS_URL=ws://your-ws-server:96

# ===== 可选 =====

# 无后端时使用本地 Mock 数据
VUE_APP_MOCK_ENABLED=true
VUE_APP_SERVER_TARGET=http://127.0.0.1:8080/mock/lcJson/

# 开发服务器端口
VUE_APP_DEV_PORT=8080
```

> ⚠️ **安全提醒**: `.env.local` 已在 `.gitignore` 中排除，**切勿**提交含真实密码的 `.env` 文件到仓库！

---

## 三、与 UE 端的对接

### 3.1 前提条件

前端依赖两个 UE 侧服务：

1. **AirCity Cloud** — 提供像素流渲染 (UE5 → WebRTC → 浏览器)
   - 地址: `ac_conf.js` 中的 `HostConfig.Manager/Player`
   - 负责: 3D场景渲染、点击事件、相机控制

2. **WebSocket 中间服务器** — UE ↔ Web 消息路由
   - 地址: `VUE_APP_WS_URL`
   - 负责: UE侧点击设备 → 发送 `link-xxx` → 前端路由跳转

### 3.2 通信协议对接清单

当 UE 端开发时，需要实现以下消息发送：

```cpp
// UE 端 C++ 伪代码示例
void OnDeviceClicked(FString DeviceType)
{
    // 发送约定好的 link 消息
    if (DeviceType == "AirConditioner")
        WebSocket->Send("link-SBGLKT");    // 跳转空调页面
    else if (DeviceType == "FireAlarm")
        WebSocket->Send("link-ZHXF");      // 跳转智慧消防
    // ... 完整映射表见 ARCHITECTURE.md §2.2.1
}
```

### 3.3 新增 UE 交互事件

在 `src/AirCityUtils/Event.js` 中添加新的事件处理：

```javascript
// 示例: 新增"充电桩点击"交互
if (e.PropertyName && e.PropertyName === '充电桩') {
  store.commit('updateTagId', '充电桩详情')
  // 可同时高亮、发请求等
  __g.tileLayer.highlightActor(e.Id, e.ObjectID)
}
```

---

## 四、开发指南

### 4.1 新增页面/模块

```bash
# 1. 创建视图文件
src/views/YourModule/YourModule.vue

# 2. 创建组件
src/components/YourModule/Top.vue
src/components/YourModule/Bottom.vue
src/components/YourModule/echarts/

# 3. 注册路由 (src/router/index.js)
{
  path: "YourModule",
  name: "YourModule",
  component: () => import("../views/YourModule/YourModule.vue"),
}

# 4. 添加左侧菜单 (src/components/navlink/linksArr.js)
{
  path: "/Home/YourModule",
  key: "YourModule",
  name: "你的模块",
  img: require("@/assets/img/your_icon.png"),
  message: "link-YOURMSG",   // 发送给UE的消息
}

# 5. 添加 WS 消息映射 (src/api/Websocket.js ROUTE_MAP)
'link-YOURMSG': '/Home/YourModule',
```

### 4.2 图表开发规范

```javascript
// ✅ 正确: 使用按需引入
import echarts from '@/plugins/echarts'

// ❌ 错误: 全量引入 (会打包整个 echarts 库)
import * as echarts from 'echarts'

// 如按需引入缺少组件，在 src/plugins/echarts.js 中取消对应注释
```

### 4.3 SCSS 响应式

所有尺寸使用 `common.scss` 中定义的 mixin，基于 1920×1080 基准：

```scss
.my-box {
  @include Width(300);       // 基于 1920 宽度的比例
  @include hHeight(200);     // 基于 1080 高度的比例
  @include FontSize(16);     // 字体大小
}
```

### 4.4 调试技巧

```javascript
// 1. 查看 AirCity 是否就绪
console.log(store.state.isOnReady)

// 2. 查看 WebSocket 连接状态
import ws from '@/api/Websocket'
console.log(ws.isConnected())

// 3. 手动触发路由
ws.send('link-ZHTS')   // 跳转综合态势

// 4. 查看图层树
const tree = await __g.infoTree.get()
console.log(tree.infotree)

// 5. 3D场景中点击设备，打印事件信息
// 在 Event.js 中 console.log(e) 查看完整事件结构
```

---

## 五、部署

### 5.1 Nginx 部署 (推荐)

```nginx
server {
    listen 80;
    server_name parking.example.com;

    root /var/www/parking-system/dist;
    index index.html;

    # SPA fallback (hash路由不需要，history模式才需要)
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 代理
    location /api/ {
        proxy_pass http://backend:8020/;
        proxy_set_header Host $host;
    }

    # WebSocket 代理 (可选，如需要穿透)
    location /ws/ {
        proxy_pass http://ws-server:96;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # 静态资源强缓存
    location /static/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

### 5.2 Docker 部署

```dockerfile
# Dockerfile
FROM node:18-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --registry=https://registry.npmmirror.com
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

### 5.3 环境变量在构建时注入

```bash
# CI/CD 构建命令
VUE_APP_AIRCITY_MANAGER=prod-aircity:30005 \
VUE_APP_API_TARGET=http://prod-api:8020 \
npm run build
```

> 注意: Vue CLI 的环境变量在 **构建时** 注入，运行时无法修改。不同环境需要分别构建。

---

## 六、常见问题

### Q1: `npm install` 报 node-sass 错误

**方案A** (推荐): 已升级为 dart-sass，重新安装即可
```bash
rm -rf node_modules package-lock.json
npm install
```

**方案B**: 仍使用旧版 node-sass
```bash
npm install node-sass@4.14.1 --sass_binary_site=https://npmmirror.com/mirrors/node-sass/
```

### Q2: 3D 场景黑屏/加载不出

1. 检查 AirCity 服务是否运行: 浏览器访问 `http://192.168.5.197:30005`
2. 检查 `public/aircity/ac_conf.js` 中 Manager 地址
3. 检查浏览器控制台是否有 `__g is not defined` 错误
4. 确认 VPN/内网连通性

### Q3: WebSocket 连接不上

1. 检查 `.env.local` 中 `VUE_APP_WS_URL` 地址
2. 浏览器控制台查看 `[WS]` 日志
3. 无 WS 连接不影响页面展示，只影响 UE 点击路由同步

### Q4: 页面放大/缩小后错位

- 系统基于 1920×1080 设计基准，使用 SCSS mixin (`vw`/`vh`) 做等比缩放
- 不同比例的屏幕可能需要微调个别组件

### Q5: 打包后体积过大

```bash
npm run build:report
# 查看 dist/report.html 分析:
# - 大字体文件 → 改为 CDN 外链
# - echarts 全量 → 确认使用 @/plugins/echarts 按需引入
# - 图片 → 压缩 / webp 格式
```

---

## 七、技术债务 & 迁移路线

| 优先级 | 事项 | 预估工作量 |
|--------|------|-----------|
| P0 | 迁移到 Vue 3 + Vite (Vue 2 已 EOL) | 3-5天 |
| P1 | 添加 TypeScript 类型覆盖 | 2-3天 |
| P1 | 字体文件 CDN 化 (减少 25MB 打包) | 0.5天 |
| P2 | 统一路由命名 (Security→DeviceMgmt 等) | 1天 |
| P2 | 单元测试 / E2E 测试覆盖 | 3天 |
| P3 | PWA 离线支持 | 1天 |

---

## 八、团队协作

### Git 分支规范

```
main        ← 生产分支 (只接受 merge request)
├── dev     ← 开发分支
├── feat/xxx ← 功能分支
├── fix/xxx  ← 修复分支
└── release/x.x.x ← 发布分支
```

### 提交规范

```
feat: 新增空调能耗预测图表
fix: 修复 WebSocket 断线不重连问题
refactor: 重构 http.js 错误处理
docs: 更新 API 文档
chore: 升级 axios 到 0.27.2
```

---

> 📚 更多技术细节见 [ARCHITECTURE.md](./ARCHITECTURE.md)
