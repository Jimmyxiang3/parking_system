from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

db = SQLAlchemy()

# ========== 1. 车辆表 ==========
class Vehicle(db.Model):
    __tablename__ = 'vehicles'

    id = db.Column(db.Integer, primary_key=True)
    plate_number = db.Column(db.String(20), unique=True)   # 车牌号
    vehicle_type = db.Column(db.String(20), default='normal')  # normal普通 / special专用 / electric电车
    vehicle_size = db.Column(db.String(10), default='medium')  # small / medium / large
    is_electric = db.Column(db.Boolean, default=False)     # 是否电车
    owner_contact = db.Column(db.String(50))               # 车主联系方式
    entry_time = db.Column(db.DateTime)                    # 入场时间
    exit_time = db.Column(db.DateTime)                     # 离场时间
    parking_duration = db.Column(db.Integer)               # 停车时长（分钟）
    assigned_floor = db.Column(db.Integer)                 # 分配的楼层
    assigned_zone = db.Column(db.String(10))               # 分配的区域
    user_type = db.Column(db.String(20), default='temp')    # long_term / temp
    user_tag = db.Column(db.String(50))                    # 用户标记，如"早高峰固定用户"
    status = db.Column(db.String(10), default='outside')   # inside场内 / outside离场 / charging充电中

    def to_dict(self):
        return {
            'id': self.id,
            'plate_number': self.plate_number,
            'vehicle_type': self.vehicle_type,
            'vehicle_size': self.vehicle_size,
            'is_electric': self.is_electric,
            'status': self.status,
            'user_type': self.user_type,
            'user_tag': self.user_tag,
            'assigned_floor': self.assigned_floor,
            'assigned_zone': self.assigned_zone,
            'entry_time': self.entry_time.strftime('%Y-%m-%d %H:%M:%S') if self.entry_time else None,
            'exit_time': self.exit_time.strftime('%Y-%m-%d %H:%M:%S') if self.exit_time else None,
            'parking_duration': self.parking_duration
        }

# ========== 2. 车位表（增强版）==========
class ParkingSpot(db.Model):
    __tablename__ = 'parking_spots'
    
    id = db.Column(db.Integer, primary_key=True)
    spot_code = db.Column(db.String(20), unique=True)      # 车位编号，如 "3F-A-001"
    floor = db.Column(db.Integer, default=1)               # 所在楼层
    zone = db.Column(db.String(10))                        # 分区：A / B / C
    x_coord = db.Column(db.Float, default=0.0)
    y_coord = db.Column(db.Float, default=0.0)
    
    spot_type = db.Column(db.String(20), default='normal') # normal普通 / special专用 / charging充电
    spot_size = db.Column(db.String(10), default='medium')  # small / medium / large
    is_charging_spot = db.Column(db.Boolean, default=False) # 是否充电车位
    charging_pile_id = db.Column(db.Integer, db.ForeignKey('charging_piles.id')) # 关联充电桩
    is_special = db.Column(db.Boolean, default=False)      # 是否专用车位
    is_emergency = db.Column(db.Boolean, default=False)    # 是否紧急车位（救护车/消防车/警车）

    status = db.Column(db.String(10), default='idle')      # idle空闲 / occupied占用 / reserved预约
    last_updated = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'spot_code': self.spot_code,
            'floor': self.floor,
            'zone': self.zone,
            'spot_type': self.spot_type,
            'spot_size': self.spot_size,
            'is_charging_spot': self.is_charging_spot,
            'is_special': self.is_special,
            'is_emergency': self.is_emergency,
            'status': self.status,
            'x_coord': self.x_coord,
            'y_coord': self.y_coord,
            'last_updated': self.last_updated.strftime('%Y-%m-%d %H:%M:%S')
        }

# ========== 3. 区域表（灯控管理）==========
class Zone(db.Model):
    __tablename__ = 'zones'
    
    id = db.Column(db.Integer, primary_key=True)
    floor = db.Column(db.Integer, default=1)               # 楼层
    zone_code = db.Column(db.String(10))                   # 区域编号：A / B / C
    name = db.Column(db.String(50))                        # 区域名称
    
    # 两套灯系统
    normal_light_on = db.Column(db.Boolean, default=False) # 正常照明灯状态
    sound_light_on = db.Column(db.Boolean, default=False)  # 声控灯状态
    
    occupied_count = db.Column(db.Integer, default=0)      # 当前占用车位数
    total_spots = db.Column(db.Integer, default=0)         # 总车位数
    
    def to_dict(self):
        return {
            'id': self.id,
            'floor': self.floor,
            'zone_code': self.zone_code,
            'name': self.name,
            'normal_light_on': self.normal_light_on,
            'sound_light_on': self.sound_light_on,
            'occupied_count': self.occupied_count,
            'total_spots': self.total_spots,
            'occupancy_rate': round(self.occupied_count / self.total_spots * 100, 1) if self.total_spots > 0 else 0
        }

# ========== 4. 充电桩表 ==========
class ChargingPile(db.Model):
    __tablename__ = 'charging_piles'
    
    id = db.Column(db.Integer, primary_key=True)
    pile_code = db.Column(db.String(20), unique=True)      # 充电桩编号
    floor = db.Column(db.Integer, default=3)               # 所在楼层（3、4层）
    zone = db.Column(db.String(10))                        # 所在区域
    spot_id = db.Column(db.Integer, db.ForeignKey('parking_spots.id')) # 关联车位
    
    charge_mode = db.Column(db.String(10), default='slow') # slow慢充 / fast快充
    status = db.Column(db.String(10), default='idle')      # idle空闲 / charging充电中 / fault故障
    power_kw = db.Column(db.Float, default=7.0)            # 功率（kW），慢充7kW，快充60kW
    
    # 计费规则
    price_charging = db.Column(db.Float, default=1.5)      # 充电中单价（元/kWh）
    price_idle = db.Column(db.Float, default=0.5)          # 充满后占位费（元/小时）
    
    def to_dict(self):
        return {
            'id': self.id,
            'pile_code': self.pile_code,
            'floor': self.floor,
            'zone': self.zone,
            'charge_mode': self.charge_mode,
            'status': self.status,
            'power_kw': self.power_kw,
            'price_charging': self.price_charging,
            'price_idle': self.price_idle
        }

# ========== 5. 充电记录表 ==========
class ChargingRecord(db.Model):
    __tablename__ = 'charging_records'
    
    id = db.Column(db.Integer, primary_key=True)
    pile_id = db.Column(db.Integer, db.ForeignKey('charging_piles.id'))
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'))
    plate_number = db.Column(db.String(20))
    
    start_time = db.Column(db.DateTime, default=datetime.now)
    end_time = db.Column(db.DateTime)
    charge_mode = db.Column(db.String(10))                 # slow / fast
    
    start_battery = db.Column(db.Float)                    # 起始电量（%）
    end_battery = db.Column(db.Float)                      # 结束电量（%）
    total_kwh = db.Column(db.Float, default=0.0)           # 总充电量（kWh）
    
    is_full = db.Column(db.Boolean, default=False)         # 是否充满
    total_fee = db.Column(db.Float, default=0.0)           # 总费用（元）
    status = db.Column(db.String(10), default='charging')  # charging进行中 / completed已完成
    
    def to_dict(self):
        return {
            'id': self.id,
            'pile_code': self.pile_code if hasattr(self, 'pile_code') else None,
            'plate_number': self.plate_number,
            'charge_mode': self.charge_mode,
            'start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': self.end_time.strftime('%Y-%m-%d %H:%M:%S') if self.end_time else None,
            'total_kwh': round(self.total_kwh, 2),
            'total_fee': round(self.total_fee, 2),
            'status': self.status
        }

# ========== 6. 楼层闸机表 ==========
class FloorGate(db.Model):
    __tablename__ = 'floor_gates'
    
    id = db.Column(db.Integer, primary_key=True)
    gate_code = db.Column(db.String(20), unique=True)      # 闸机编号
    floor = db.Column(db.Integer, default=1)               # 控制哪一层入口
    direction = db.Column(db.String(10), default='up')     # up上行 / down下行
    
    status = db.Column(db.String(10), default='closed')    # open开启 / closed关闭 / fault故障
    last_action_time = db.Column(db.DateTime)
    
    def to_dict(self):
        return {
            'id': self.id,
            'gate_code': self.gate_code,
            'floor': self.floor,
            'direction': self.direction,
            'status': self.status,
            'last_action_time': self.last_action_time.strftime('%Y-%m-%d %H:%M:%S') if self.last_action_time else None
        }

# ========== 7. 车位分配记录表 ==========
class ParkingAssignment(db.Model):
    __tablename__ = 'parking_assignments'
    
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'))
    plate_number = db.Column(db.String(20))
    
    # 分配的两个备选车位
    option1_spot_id = db.Column(db.Integer, db.ForeignKey('parking_spots.id'))
    option2_spot_id = db.Column(db.Integer, db.ForeignKey('parking_spots.id'))
    selected_spot_id = db.Column(db.Integer, db.ForeignKey('parking_spots.id')) # 最终选择
    
    need_charging = db.Column(db.Boolean, default=False)   # 是否需要充电
    charge_mode = db.Column(db.String(10))                 # 充电模式选择
    assigned_floor = db.Column(db.Integer)
    
    create_time = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(10), default='pending')   # pending待选择 / confirmed已确认 / expired已过期
    
    def to_dict(self):
        return {
            'id': self.id,
            'plate_number': self.plate_number,
            'need_charging': self.need_charging,
            'charge_mode': self.charge_mode,
            'assigned_floor': self.assigned_floor,
            'status': self.status,
            'create_time': self.create_time.strftime('%Y-%m-%d %H:%M:%S')
        }

# ========== 8. 设备表（保留原有）==========
class Device(db.Model):
    __tablename__ = 'devices'
    
    id = db.Column(db.Integer, primary_key=True)
    device_code = db.Column(db.String(50), unique=True)
    device_type = db.Column(db.String(20))
    floor = db.Column(db.Integer, default=1)
    location = db.Column(db.String(100))
    status = db.Column(db.String(10), default='online')
    last_heartbeat = db.Column(db.DateTime, default=datetime.now)
    open_count = db.Column(db.Integer, default=0)           # 地锁升降总次数
    last_maintenance_time = db.Column(db.DateTime)          # 上次维护时间
    power_w = db.Column(db.Float, default=0.0)              # 功率（瓦）
    is_on = db.Column(db.Boolean, default=True)             # 开关状态
    stream_url = db.Column(db.String(500))                  # 摄像头/监控流地址

    def to_dict(self):
        return {
            'id': self.id,
            'device_code': self.device_code,
            'device_type': self.device_type,
            'floor': self.floor,
            'location': self.location,
            'status': self.status,
            'open_count': self.open_count,
            'power_w': self.power_w,
            'is_on': self.is_on,
            'stream_url': self.stream_url,
            'last_heartbeat': self.last_heartbeat.strftime('%Y-%m-%d %H:%M:%S'),
            'last_updated': self.last_heartbeat.strftime('%Y-%m-%d %H:%M:%S'),
            'last_maintenance_time': self.last_maintenance_time.strftime('%Y-%m-%d %H:%M:%S') if self.last_maintenance_time else None
        }

# ========== 9. 能耗记录表（保留原有）==========
class EnergyRecord(db.Model):
    __tablename__ = 'energy_records'

    id = db.Column(db.Integer, primary_key=True)
    floor = db.Column(db.Integer, default=1)
    power_usage = db.Column(db.Float, default=0.0)
    record_time = db.Column(db.DateTime, default=datetime.now)
    power_kw = db.Column(db.Float, default=0.0)            # 当时功率 kW
    device_category = db.Column(db.String(30))             # lighting/charging/electromechanical/supporting

    def to_dict(self):
        return {
            'id': self.id,
            'floor': self.floor,
            'power_usage': self.power_usage,
            'power_kw': self.power_kw,
            'device_category': self.device_category,
            'record_time': self.record_time.strftime('%Y-%m-%d %H:%M:%S')
        }

# ========== 10. 事件日志表（保留原有）==========
class EventLog(db.Model):
    __tablename__ = 'event_logs'

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(20))
    floor = db.Column(db.Integer, default=1)
    plate_number = db.Column(db.String(20))                # 关联车牌号
    category = db.Column(db.String(20), default='parking')  # device设备故障 / parking车位违规 / fire消防
    description = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=datetime.now)

    # 文档扩展字段
    device_id = db.Column(db.String(64))                   # 关联设备编号
    device = db.Column(db.String(100))                     # 设备显示名称
    position = db.Column(db.String(255))                   # 具体位置
    lv = db.Column(db.String(20))                          # high / mid / low
    priority = db.Column(db.Integer)                       # 0-100 AI优先级
    recommendation = db.Column(db.String(500))             # 处理建议
    status = db.Column(db.String(20), default='pending')   # pending / handled / timeout
    handled_at = db.Column(db.DateTime)                    # 处理完成时间

    def to_dict(self):
        # 处理时限：按优先级/难度划分（紧急30分钟、重要2小时、一般6小时）
        deadline_minutes = 30 if (self.priority or 0) >= 75 else (
            120 if (self.priority or 0) >= 50 else 360)
        deadline = self.timestamp + timedelta(minutes=deadline_minutes)
        return {
            'id': self.id,
            'event_type': self.event_type,
            'floor': self.floor,
            'plate_number': self.plate_number,
            'category': self.category,
            'description': self.description,
            'msg': self.description,
            'device_id': self.device_id,
            'device': self.device,
            'position': self.position,
            'lv': self.lv,
            'priority': self.priority,
            'recommendation': self.recommendation,
            'status': self.status,
            'deadline_minutes': deadline_minutes,
            'deadline': deadline.strftime('%Y-%m-%d %H:%M:%S'),
            'timestamp': self.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'handled_at': self.handled_at.strftime('%Y-%m-%d %H:%M:%S') if self.handled_at else None
        }

# ========== 17. 事件刷新标记表 ==========
class EventRefreshMark(db.Model):
    __tablename__ = 'event_refresh_marks'

    id = db.Column(db.Integer, primary_key=True)
    last_refresh = db.Column(db.DateTime, default=datetime.now)

# ========== 16. 消防告警统计表 ==========
class FireAlarmStat(db.Model):
    __tablename__ = 'fire_alarm_stats'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date)                               # 日期
    total_alarms = db.Column(db.Integer, default=0)         # 告警总数
    high_risk = db.Column(db.Integer, default=0)            # 高危告警数
    smoke = db.Column(db.Integer, default=0)                # 烟感告警
    temperature = db.Column(db.Integer, default=0)          # 高温告警
    co = db.Column(db.Integer, default=0)                   # CO告警
    equipment = db.Column(db.Integer, default=0)            # 设备告警

    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date.strftime('%m/%d'),
            'total_alarms': self.total_alarms,
            'high_risk': self.high_risk,
            'smoke': self.smoke,
            'temperature': self.temperature,
            'co': self.co,
            'equipment': self.equipment
        }

# ========== 11. 停车记录表 ==========
class ParkingRecord(db.Model):
    __tablename__ = 'parking_records'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'))
    plate_number = db.Column(db.String(20))
    spot_id = db.Column(db.Integer, db.ForeignKey('parking_spots.id'))
    spot_code = db.Column(db.String(20))

    entry_time = db.Column(db.DateTime, default=datetime.now)
    exit_time = db.Column(db.DateTime)
    duration = db.Column(db.Integer)                        # 停车时长（分钟）
    floor = db.Column(db.Integer)
    zone = db.Column(db.String(10))
    total_fee = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(10), default='parking')   # parking / completed

    def to_dict(self):
        return {
            'id': self.id,
            'plate_number': self.plate_number,
            'spot_code': self.spot_code,
            'floor': self.floor,
            'zone': self.zone,
            'duration': self.duration,
            'total_fee': self.total_fee,
            'status': self.status,
            'entry_time': self.entry_time.strftime('%Y-%m-%d %H:%M:%S'),
            'exit_time': self.exit_time.strftime('%Y-%m-%d %H:%M:%S') if self.exit_time else None
        }

# ========== 12. 用户画像表 ==========
class UserProfile(db.Model):
    __tablename__ = 'user_profiles'

    id = db.Column(db.Integer, primary_key=True)
    plate_number = db.Column(db.String(20), unique=True)
    user_type = db.Column(db.String(20), default='temp')    # long_term / temp
    user_tag = db.Column(db.String(50))                     # 标记：早高峰固定用户、周末用户等

    # 统计数据
    total_parking_count = db.Column(db.Integer, default=0)  # 累计停车次数
    monthly_parking_count = db.Column(db.Integer, default=0) # 本月停车次数
    avg_duration = db.Column(db.Float, default=0.0)         # 平均停车时长（分钟）
    preferred_floor = db.Column(db.Integer)                 # 偏好楼层
    preferred_zone = db.Column(db.String(10))               # 偏好区域
    preferred_spot_type = db.Column(db.String(20))          # 偏好车位类型

    # 时间特征
    first_seen = db.Column(db.DateTime)                     # 首次出现时间
    last_seen = db.Column(db.DateTime)                      # 最后活跃时间
    peak_entry_hour = db.Column(db.Integer)                 # 最常入场时段（小时）
    peak_exit_hour = db.Column(db.Integer)                  # 最常出场时段（小时）

    # 连续活跃追踪
    active_months = db.Column(db.Integer, default=0)        # 连续活跃月数
    consecutive_peak_entry = db.Column(db.Integer, default=0) # 连续早高峰进场次数

    create_time = db.Column(db.DateTime, default=datetime.now)
    update_time = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'plate_number': self.plate_number,
            'user_type': self.user_type,
            'user_tag': self.user_tag,
            'total_parking_count': self.total_parking_count,
            'monthly_parking_count': self.monthly_parking_count,
            'avg_duration': round(self.avg_duration, 1),
            'preferred_floor': self.preferred_floor,
            'preferred_zone': self.preferred_zone,
            'preferred_spot_type': self.preferred_spot_type,
            'peak_entry_hour': self.peak_entry_hour,
            'peak_exit_hour': self.peak_exit_hour,
            'active_months': self.active_months,
            'first_seen': self.first_seen.strftime('%Y-%m-%d') if self.first_seen else None,
            'last_seen': self.last_seen.strftime('%Y-%m-%d %H:%M:%S') if self.last_seen else None
        }

# ========== 13. 节假日配置表 ==========
class HolidayConfig(db.Model):
    __tablename__ = 'holiday_configs'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))                         # 节假日名称
    date = db.Column(db.Date)                               # 日期
    holiday_type = db.Column(db.String(20), default='holiday') # holiday / workday_adjust / special_event
    peak_factor = db.Column(db.Float, default=1.0)          # 流量影响系数（>1高峰期，<1低谷期）
    description = db.Column(db.String(200))

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'date': self.date.strftime('%Y-%m-%d'),
            'holiday_type': self.holiday_type,
            'peak_factor': self.peak_factor,
            'description': self.description
        }

# ========== 14. 设备维护记录表 ==========
class DeviceMaintenance(db.Model):
    __tablename__ = 'device_maintenances'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id'))
    device_code = db.Column(db.String(50))
    maintenance_type = db.Column(db.String(20))             # routine / emergency / replace
    description = db.Column(db.String(200))
    operator = db.Column(db.String(50))                     # 操作人
    cost = db.Column(db.Float, default=0.0)                 # 维修费用
    create_time = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'device_code': self.device_code,
            'maintenance_type': self.maintenance_type,
            'description': self.description,
            'operator': self.operator,
            'cost': self.cost,
            'create_time': self.create_time.strftime('%Y-%m-%d %H:%M:%S')
        }

# ========== 15. 高峰时段统计表 ==========
class PeakHourStat(db.Model):
    __tablename__ = 'peak_hour_stats'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date)
    hour = db.Column(db.Integer)                            # 小时 (0-23)
    entry_count = db.Column(db.Integer, default=0)          # 入场数
    exit_count = db.Column(db.Integer, default=0)           # 出场数
    is_peak = db.Column(db.Boolean, default=False)          # 是否高峰时段
    peak_type = db.Column(db.String(20))                    # morning / evening / none
    occupancy_rate = db.Column(db.Float, default=0.0)       # 该时段占用率
    weather = db.Column(db.String(20))                      # 天气状况

    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date.strftime('%Y-%m-%d'),
            'hour': self.hour,
            'entry_count': self.entry_count,
            'exit_count': self.exit_count,
            'is_peak': self.is_peak,
            'peak_type': self.peak_type,
            'occupancy_rate': self.occupancy_rate,
            'weather': self.weather
        }
