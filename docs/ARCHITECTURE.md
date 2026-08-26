# 🏗️ 智慧停车楼数字孪生平台 — 系统架构与通信接口文档

> 版本: 0.2.0 | 更新: 2025-07 | 基于 Vue 2.7 + AirCity SDK

---

## 一、系统总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        浏览器 (Browser)                          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                  Vue 2.7 SPA (hash路由)                    │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐  │  │
│  │  │ 综合态势  │ │ 能耗管理 │ │ 设备管理  │ │ 智慧消防    │  │  │
│  │  │comps+view │ │comps+view│ │comps+view│ │ comps+view  │  │  │
│  │  └─────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬──────┘  │  │
│  │        └─────────────┴────────────┴───────────────┘         │  │
│  │                         │ Vuex Store                         │  │
│  │  ┌──────────────────────┼──────────────────────────────────┐│  │
│  │  │ 通信层               │                                   ││  │
│  │  │ ┌─────────┐ ┌───────┴──────┐ ┌──────────────────────┐  ││  │
│  │  │ │  axios  │ │  WebSocket   │ │  AirCity Event Bus   │  ││  │
│  │  │ │ HTTP    │ │  客户端      │ │  (__g 全局对象)      │  ││  │
│  │  │ └────┬────┘ └──────┬───────┘ └──────────┬───────────┘  ││  │
│  │  └──────┼─────────────┼────────────────────┼──────────────┘│  │
│  └─────────┼─────────────┼────────────────────┼───────────────┘  │
│            │             │                    │                   │
│  ┌─────────┴─────────────┴────────────────────┴───────────────┐  │
│  │              AirCity SDK (ac.min.js)                         │  │
│  │  像素流解码 → WebRTC/WebSocket → UE5 Render                  │  │
│  └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
          │                  │                    │
          ▼                  ▼                    ▼
   ┌──────────┐    ┌──────────────┐    ┌──────────────────┐
   │ REST API │    │ WS 中间服务器 │    │ AirCity Cloud     │
   │ 后端服务  │    │ 消息路由转发  │    │ 192.168.5.197    │
   │ :8020    │    │ :96          │    │ :30005 (UE像素流) │
   └──────────┘    └──────────────┘    └──────────────────┘
```

---

## 二、通信接口详解

### 2.1 HTTP/HTTPS (REST API)

**文件**: `src/api/http.js`  
**客户端**: axios 0.27.2  
**配置**: `src/config/index.js` → `config.http`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| timeout | 15000ms | 请求超时 |
| withCredentials | true | 携带Cookie |
| retryCount | 2 | 失败重试次数 |

**代理映射** (`vue.config.js` devServer.proxy):

| 前缀 | 目标 | 用途 |
|------|------|------|
| `/api` | `VUE_APP_API_TARGET` (默认 `http://120.77.174.86:8020`) | 主业务API |
| `/Tapi` | `VUE_APP_TAPI_TARGET` (默认 `http://10.22.119.222:8017`) | 备用API |
| `/server` | Mock/后端 | 楼层数据 |

**错误码**:

| HTTP状态码 | 含义 | 处理 |
|-----------|------|------|
| 200 | 成功 | 检查 `response.data.err` 业务码 |
| 401 | 未授权 | 自动跳转登录页 |
| 5xx | 服务器错误 | toast提示 + reject |

**请求示例**:
```javascript
import http from '@/api/http'

// GET
const res = await http.get('/api/parking/status?floor=1')

// POST
const res = await http.post('/api/device/control', { id: 'AC001', action: 'on' })
```

---

### 2.2 WebSocket (双向路由同步)

**文件**: `src/api/Websocket.js` + `src/api/websocket-client.js`  
**连接地址**: `config.websocket.url` (默认 `ws://82.157.25.61:96`)  
**协议**: 纯文本消息 (无JSON封装)

#### 2.2.1 消息协议

**UE → Web (设备点击路由跳转)**:

| 消息 | 目标路由 | 说明 |
|------|----------|------|
| `link-ZHTS` | `/Home/ComprehensiveOverview` | 综合态势 |
| `link-NHGLNHT` | `/Home/EnergyMetering/All` | 能耗一张图 |
| `link-NHGLNHPG` | `/Home/EnergyMetering/NewTrendSystem` | 能耗评估 |
| `link-SBGLKT` | `/Home/Security/AllS` | 空调控制 |
| `link-SBGLZM` | `/Home/Security/Personnel` | 照明控制 |
| `link-SBGLCDCP` | `/Home/Security/Spaceenvironment` | 充电车棚 |
| `link-building` | `/Home/Security/Building` | 光伏 |
| `link-ZHXF` | `/Home/Communication/All` | 智慧消防 |
| `link-firefight` | `/Home/Communication/All` | 智慧消防(别名) |
| `link-PDF` | `/Home/Equipment/Operation` | 配电房 |
| `link-photovoltaic` | `/Home/SpaceResources` | 光伏(空间资源) |

**Web → UE (前端点击菜单同步)**:
> 通过 `ws.send(message)` 发送，消息体同上述 link-xxx 格式

#### 2.2.2 新增特性 (v0.2.0)

| 特性 | 说明 |
|------|------|
| 自动重连 | 断线后自动重连 (指数退避, 最多10次) |
| 心跳保活 | 每30s发送 ping 维持连接 |
| 离线队列 | 断线期间的消息暂存，重连后批量发送 |
| 事件总线 | `ws.on(event, handler)` 注册/取消监听 |

#### 2.2.3 用法示例

```javascript
// 旧代码 (仍然兼容)
import ws from '@/api/Websocket'
ws.send('link-ZHTS')   // 通知UE切换到综合态势

// 新代码 (推荐)
import { createWSClient } from '@/api/websocket-client'
const ws2 = createWSClient({ url: 'ws://custom:9999' })
ws2.connect()
ws2.on('custom-event', (msg) => { /* ... */ })
ws2.send('hello')
```

---

### 2.3 AirCity 引擎事件 (3D交互)

**文件**: `src/AirCityUtils/Event.js`, `src/AirCityUtils/onReady.js`  
**全局对象**: `__g` (TypeScript 声明见 `src/AirCity.d.ts`)

#### 2.3.1 引擎初始化流程

```
1. public/index.html 加载 ac.min.js → 全局 __g 对象就绪
2. AirCityUtils/onReady.js 监听就绪事件
3. 执行:
   - __g.settings.setMainUIVisibility(false)   // 隐藏引擎UI
   - __g.settings.setCampassVisible(false)      // 隐藏指南针
   - store.commit('isOnReady', true)            // 通知Vuex
   - __g.infoTree.get() → store.commit('infoTree', ...)
   - 创建 ChaiLou 拆楼实例
4. 各视图通过 __g.* API 控制3D场景
```

#### 2.3.2 3D点击 → Vue事件映射

| 3D交互 (e.eventtype) | 识别字段 | Vue动作 |
|---------------------|----------|---------|
| `LeftMouseButtonClick` + `e.GroupID === "showDialog"` | `e.Id` 含 "zj" | 弹出闸机Dialog |
| `LeftMouseButtonClick` | `e.Id` 含 "kongtiao" | 弹出空调Dialog |
| `LeftMouseButtonClick` | `e.PropertyName` 含 "摄像头" | 弹出枪式摄像机Dialog |
| `LeftMouseButtonClick` | `e.Id` 含 "jinBao" | 弹出空调告警Dialog |
| `LeftMouseButtonClick` | `e.PropertyName === "静态人"` | 高亮Actor + 人脸识别弹窗 |
| `LeftMouseButtonClick` | `e.GroupID === "elevator"` | 电梯状态更新 |
| `LeftMouseButtonClick` | `e.PropertyName === "能耗热力"` | 能耗等级高亮 + 弹窗 |
| `LeftMouseButtonClick` + `e.Type === "CustomObj"` | `e.UserData === "43"` | 拆楼展开 |
| `CameraTourFinished` | - | 导览结束 (loopcream循环) |

#### 2.3.3 常用 __g API 速查

```javascript
// 视角
__g.camera.set(x, y, z, pitch, yaw, roll)
__g.camera.playAnimation(index)

// 图层
__g.tileLayer.hideActor(tileLayerId, objectId)
__g.tileLayer.showAllActors(tileLayerId)
__g.tileLayer.enableXRay(ids, color)     // X光透视
__g.tileLayer.disableXRay(ids)
__g.tileLayer.highlightActor(id, objectId)
__g.tileLayer.stopHighlightActor()

// 自定义对象 (用于拆楼/旋转等)
__g.customObject.addByTileLayer({ id, tileLayerId, objectId, smoothMotion })
__g.customObject.setRotation(id, [x, y, z])
__g.customObject.setLocation(id, [x, y, z])
__g.customObject.delete(id)

// 标签
__g.tag.add({ id, coordinate, text, ... })

// 信息树
__g.infoTree.get()          // 获取完整图层树
__g.infoTree.hide(id)       // 隐藏图层
__g.infoTree.show(id)       // 显示图层

// 设置
__g.settings.setMainUIVisibility(false)
__g.settings.setCampassVisible(false)

// 标记
__g.marker.add({ ... })
__g.marker.clear()

// 3D多边形
__g.polygon3d.add({ ... })
```

---

## 三、Vuex 状态管理

### 3.1 Store 结构

```
store/
├── index.js              # 根state + mutations (tagId, floor, isOnReady...)
└── modules/
    ├── AirCity.js         # AirCityPlayer / AirCityApi (引擎实例)
    ├── BuildingRun.js     # BuildClass / Objectids (拆楼状态)
    ├── Rotation.js        # 设备旋转动画控制
    └── Elevator.js        # 电梯激活状态
```

### 3.2 关键状态一览

| State | 路径 | 类型 | 说明 |
|-------|------|------|------|
| isOnReady | `state.isOnReady` | Boolean | AirCity引擎是否就绪 |
| infoTree | `state.infoTree` | Array | 图层树信息 |
| floor | `state.floor` | Number | 当前楼层 |
| tagId | `state.tagId` | String | 当前弹出的Dialog类型 |
| objectID | `state.objectID` | String | 当前交互的3D物体ID |
| showDialog | `state.showDialog` | Boolean | Dialog显示开关 |
| uefloor | `state.uefloor` | Object | UE拆楼特殊楼层 |
| AirCityPlayer | `AirCity/AirCityPlayer` | Object | 引擎播放器实例 |
| BuildClass | `BuildingRun/BuildClass` | ChaiLou | 拆楼类实例 |

---

## 四、项目文件结构

```
停车场代码/
├── public/
│   ├── index.html            # 入口HTML (加载AirCity SDK)
│   ├── aircity/
│   │   ├── ac.min.js         # AirCity 引擎SDK
│   │   ├── ac_conf.js        # 引擎连接配置
│   │   └── userinfo.js       # 引擎登录凭据
│   ├── mock/lcJson/          # 楼层Mock数据 (b1~l25.json)
│   ├── customTag/            # 自定义标签模板
│   └── poi_img/              # POI图标
├── src/
│   ├── main.js               # 应用入口
│   ├── App.vue               # 根组件
│   ├── config/index.js       # 🆕 集中配置
│   ├── plugins/echarts.js    # 🆕 ECharts按需引入
│   ├── api/
│   │   ├── http.js           # 🔧 HTTP封装 (已修复)
│   │   ├── Websocket.js      # 🔧 WebSocket路由同步 (已重构)
│   │   └── websocket-client.js # 🆕 WS客户端类
│   ├── AirCityUtils/
│   │   ├── onReady.js        # 引擎就绪回调
│   │   └── Event.js          # 3D点击事件分发
│   ├── store/                # Vuex状态管理
│   ├── router/               # 路由配置
│   ├── views/                # 页面视图 (7大模块)
│   ├── components/           # 公共组件
│   ├── styles/common.scss    # SCSS全局样式
│   └── util/                 # 工具函数
│       ├── building.js       # ChaiLou 拆楼类
│       ├── chai.js           # 楼层拆解
│       └── ...
├── .env.example              # 🆕 环境变量模板
├── vue.config.js             # 🔧 构建配置 (已重构)
└── package.json              # 🔧 依赖升级
```

---

## 五、v0.2.0 修复清单

| # | 类别 | 文件 | 修复内容 |
|---|------|------|----------|
| 1 | 🔴安全 | `src/api/http.js` | 移除 `Promise.resolve(error.response)` 吞错误反模式 |
| 2 | 🔴安全 | `package.json` | axios 0.21.1 → 0.27.2 (修复 CVE-2021-3749 SSRF) |
| 3 | 🔴安全 | 全部 | 所有IP/密码硬编码提取到 `.env` 环境变量 |
| 4 | 🔴功能 | `src/api/Websocket.js` | 重构为可配置客户端, 添加自动重连+心跳 |
| 5 | 🟡性能 | `src/plugins/echarts.js` | ECharts 全量引入 → 按需引入 (~1MB → ~300KB) |
| 6 | 🟡兼容 | `package.json` | node-sass 4.x → sass (dart-sass) 1.58 |
| 7 | 🟡稳定 | `src/api/http.js` | 超时 2s → 15s, 添加HTTP错误码映射 |
| 8 | 🟡架构 | `src/config/index.js` | 新增集中配置模块 |
| 9 | 🟡架构 | `vue.config.js` | proxy target 使用环境变量 |
| 10 | 🟢代码 | `src/api/Serversocket.vue` | 标记废弃 (全注释空壳文件) |
