# 停车楼系统 · 静态数据读取说明（给前端）

数据已经全部导出为静态文件托管在 Netlify，**前端无需请求后端 API 即可读取全部数据**。数据是数据库的完整快照（17 张表，1558 行）。

## 一、文件地址

部署到 Netlify 后，文件地址形如（把 `你的站点` 换成实际域名）：

```
https://你的站点.netlify.app/data.js        ← 推荐：script 标签引入（无 CORS 限制）
https://你的站点.netlify.app/data.json      ← 备选：fetch 读取
https://你的站点.netlify.app/index.html     ← 数据预览页（可打开看数据统计）
```

## 二、推荐用法（data.js，零配置）

在页面 `<head>` 里加一行：

```html
<script src="https://你的站点.netlify.app/data.js"></script>
```

之后在任意 JS 中直接访问全局变量 `window.PARKING_DATA`，键名是表名：

```js
const spots   = window.PARKING_DATA.parking_spots;   // 车位数组（420 条）
const devices = window.PARKING_DATA.devices;         // 设备数组（1009 条）
const events  = window.PARKING_DATA.event_logs;      // 告警事件数组（23 条）
const energy  = window.PARKING_DATA.energy_records;  // 能耗记录（24 条）
const vehicles= window.PARKING_DATA.vehicles;        // 车辆（30 条）
const zones   = window.PARKING_DATA.zones;           // 区域（21 条）
const records = window.PARKING_DATA.parking_records; // 停车记录（30 条）
```

优点：没有 CORS 问题、没有异步请求、页面打开即用。

## 三、备选用法（data.json，fetch）

```js
const res = await fetch('https://你的站点.netlify.app/data.json');
const D = await res.json();
console.log(D.parking_spots); // 与 data.js 结构完全相同
```

Netlify 上已放置 `_headers` 文件开放跨域（`Access-Control-Allow-Origin: *`），fetch 无跨域问题。

## 四、数据结构

顶层对象 = 数据库表名 → 该表全部行的数组。**字段名与后端 API 返回完全一致**（同一份数据库导出），例如：

```js
// parking_spots 每条的结构
{
  "id": 1,
  "spot_code": "1F-A-001",
  "floor": 1,
  "zone": "A",
  "x_coord": 0.0, "y_coord": 0.0,
  "spot_type": "special",
  "spot_size": "large",
  "is_charging_spot": false,
  "charging_pile_id": null,
  "is_special": true, "is_emergency": true,
  "status": "occupied",          // occupied / idle
  "last_updated": "2026-08-24T13:40:11"
}

// event_logs 每条的结构
{
  "id": 1, "event_type": "warning", "floor": 3,
  "plate_number": null,
  "category": "device",            // device / parking / fire
  "description": "CHG-006 3F-充电区 状态异常",
  "timestamp": "2026-08-24T13:19:18",
  "device_id": "CHG-006", "device": "CHG-006",
  "position": "3F-充电区",
  "lv": "high",                    // high / mid / low
  "priority": 92,                  // 0-100
  "recommendation": "照明回路异常可能导致区域停电，立即安排电工排查线路",
  "status": "pending",             // pending / handled
  "handled_at": null
}
```

全部表名清单：`charging_piles`、`charging_records`、`device_maintenances`、`devices`、`energy_records`、`event_logs`、`event_refresh_marks`、`fire_alarm_stats`、`floor_gates`、`holiday_configs`、`parking_assignments`、`parking_records`、`parking_spots`、`peak_hour_stats`、`user_profiles`、`vehicles`、`zones`。

## 五、注意事项

1. **静态快照**：data.js 是数据库某个时刻的快照，不包含写入能力。需要写数据的操作（车辆入场/出场、车位修改等 POST 接口）仍然走后端 API（`https://parking-system-iv8q.onrender.com`），只读展示类数据可用本静态文件加速。
2. **更新方式**：后端数据更新后，重新执行 `python scripts/export_static_data.py` 生成新的 data.js/data.json，重新上传到 Netlify（拖拽替换文件即可）。
3. **日期格式**：时间字段是 ISO 字符串（如 `"2026-08-24T13:40:11"`），直接用 `new Date(...)` 可解析。
