from flask import Flask, jsonify, request, send_from_directory, send_file
from flask_cors import CORS
from datetime import datetime, timedelta
from models import db, ParkingSpot, EnergyRecord, Device, EventLog, \
    Vehicle, Zone, ChargingPile, ChargingRecord, FloorGate, ParkingAssignment, \
    ParkingRecord, UserProfile, HolidayConfig, DeviceMaintenance, PeakHourStat, \
    FireAlarmStat, EventRefreshMark
from parking_assigner import ParkingAssigner
from services.user_profile_service import UserProfileService
from services.analysis_service import AnalysisService
from services.prediction_service import PredictionService
from services.consistency_service import (ConsistencyModel, floor_occupancy,
                                          overall_occupancy, floor_power_kw,
                                          energy_totals, dispatch_advice,
                                          energy_advice, time_factor)
import os
from dotenv import load_dotenv

load_dotenv()  # 加载 .env（须在任何 os.getenv 之前）

APP_ENV = os.getenv('APP_ENV', 'development')


def _database_url():
    url = os.getenv('DATABASE_URL')
    if url:
        # SQLAlchemy 2.0 拒绝 postgres:// 前缀，统一归一化
        url = url.replace('postgres://', 'postgresql://', 1)
        # 裸 postgresql:// 默认走 psycopg2 方言，本项目用 psycopg 3，补驱动名
        url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
        return url
    if APP_ENV == 'production':
        raise RuntimeError('APP_ENV=production 时必须设置 DATABASE_URL')
    return 'sqlite:///parking.db'  # 开发回退：instance/parking.db


def _secret_key():
    key = os.getenv('SECRET_KEY')
    if key:
        return key
    if APP_ENV == 'production':
        raise RuntimeError('APP_ENV=production 时必须设置 SECRET_KEY')
    return 'dev-only-secret-key'


app = Flask(__name__, static_folder=None)
app.config['SQLALCHEMY_DATABASE_URI'] = _database_url()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = _secret_key()

# CORS：生产只放行白名单（Netlify 前端域名），开发保持全开放
if APP_ENV == 'production':
    origins = os.getenv('CORS_ALLOWED_ORIGINS', '')
    if not origins:
        raise RuntimeError('APP_ENV=production 时必须设置 CORS_ALLOWED_ORIGINS')
    allowed = [o.strip() for o in origins.split(',') if o.strip()]
    CORS(app, resources={r"/api/*": {"origins": allowed, "supports_credentials": True}})
else:
    CORS(app)

db.init_app(app)

from flask_migrate import Migrate
migrate = Migrate(app, db, render_as_batch=True)  # batch：SQLite 增量迁移需要

# 前端静态文件路径
DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dist')

# MQTT（注释掉，没装MQTT服务器也能跑）
# from mqtt_client import init_mqtt
# mqtt_client = init_mqtt(app)

# ========== 测试接口 ==========
@app.route('/api/hello', methods=['GET'])
def hello():
    return jsonify({
        "code": 200,
        "message": "停车楼系统启动成功！",
        "data": "Hello Parking System"
    })

# ========== 车位相关API ==========
def _refresh_event_times():
    """告警数据定时轮换（30分钟一次）：
    - 内容不变（设备/描述/优先级），只重新分布告警时间
    - 每类按优先级取前N条作为未处理（fire 1、device 1、parking 2），其余已处理
    - 未处理只出现在时限前段；接近时限(80%)自动已处理
    - 跨天后时间基于新的一天重新分布，等于全新数据"""
    if os.environ.get('APP_ENV') == 'production':
        return False  # 生产环境禁止随机改写真实事件数据
    from models import EventRefreshMark
    import random as _random

    now = datetime.now()
    mark = EventRefreshMark.query.first()
    if not mark:
        mark = EventRefreshMark(last_refresh=now - timedelta(hours=2))
        db.session.add(mark)
        db.session.commit()

    # 30分钟内不重复刷新，跨天强制刷新
    same_day = mark.last_refresh.date() == now.date()
    if same_day and (now - mark.last_refresh).total_seconds() < 1800:
        return False

    # 每类允许保留的未处理数量
    pending_quota = {'fire': 1, 'device': 1, 'parking': 2}
    events = EventLog.query.all()
    minutes_today = max(now.hour * 60 + now.minute, 1)

    # 按类别分组，每组按优先级降序，前N条为未处理，其余已处理
    by_category = {}
    for e in events:
        by_category.setdefault(e.category, []).append(e)

    for cat, evs in by_category.items():
        quota = pending_quota.get(cat, 1)
        evs_sorted = sorted(evs, key=lambda x: -(x.priority or 0))
        for i, e in enumerate(evs_sorted):
            should_pending = i < quota
            e.status = 'pending' if should_pending else 'handled'

    for e in events:
        pri = e.priority or 30
        deadline_min = 30 if pri >= 75 else (120 if pri >= 50 else 360)
        # 内容不变，重新分配时间
        rnd = _random.Random(e.id * 7919 + int(now.strftime('%Y%m%d%H')) * 131)
        if e.status == 'pending':
            # 未处理：发生在时限前段（时限80%内），保证还有处理余量
            max_age = max(int(deadline_min * 0.8) - 5, 5)
            age = rnd.randint(3, max_age)
            e.timestamp = now - timedelta(minutes=age)
            e.handled_at = None
        else:
            # 已处理：发生在更早，处理于时限内
            age = rnd.randint(deadline_min + 10, max(minutes_today - 1, deadline_min + 15))
            e.timestamp = now - timedelta(minutes=age)
            handled_after = rnd.randint(8, max(deadline_min - 3, 10))
            e.handled_at = e.timestamp + timedelta(minutes=handled_after)
            # 处理时间也不能超过当前
            if e.handled_at > now:
                e.handled_at = now - timedelta(minutes=1)

    mark.last_refresh = now
    db.session.commit()
    return True

def _sync_device_status_with_events():
    """设备状态与事件状态同步：
    - 有未处理(pending)事件的设备 → fault
    - 已处理(handled)事件的设备 → 恢复 online
    - 无事件的设备 → 保持 online（减少无意义的异常数）"""
    changed = 0
    # 收集有事件的设备
    event_devices = {}
    for e in EventLog.query.filter_by(category='device').all():
        if e.device_id:
            event_devices[e.device_id] = e.status
    devices = Device.query.all()
    for d in devices:
        if d.device_code in event_devices:
            target = 'fault' if event_devices[d.device_code] == 'pending' else 'online'
            if d.status != target:
                d.status = target
                d.last_heartbeat = datetime.now()
                changed += 1
        elif d.status in ('fault', 'offline'):
            # 无对应事件的异常设备恢复正常
            d.status = 'online'
            d.is_on = True
            d.last_heartbeat = datetime.now()
            changed += 1
    if changed:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    return changed

def _enforce_event_deadlines():
    """处理时限强制执行：
    未处理(pending)只允许在时限80%以内；接近时限自动转为已处理(handled)"""
    now = datetime.now()
    changed = 0
    pending_events = EventLog.query.filter_by(status='pending').all()
    for e in pending_events:
        deadline_minutes = 30 if (e.priority or 0) >= 75 else (
            120 if (e.priority or 0) >= 50 else 360)
        deadline = e.timestamp + timedelta(minutes=deadline_minutes)
        # 接近时限（超过80%）即视为已处理，不用等时间到
        soft_deadline = e.timestamp + timedelta(minutes=int(deadline_minutes * 0.8))
        if now >= soft_deadline:
            e.status = 'handled'
            e.handled_at = soft_deadline
            changed += 1
    if changed:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    return changed
def _sync_spots_with_model(spots):
    """将车位状态与一致性模型同步（实时更新）
    模型给出每层占用数，占用车位按时间种子随机分布（10分钟换一次）
    另有少量故障车位(fault)与违规车位(violation)，与实时告警数据匹配"""
    from services.consistency_service import ConsistencyModel
    import random as _random
    # 按楼层分组
    floors_map = {}
    for s in spots:
        floors_map.setdefault(s.floor, []).append(s)

    # 从实时告警（pending停车事件）取违规车牌与楼层
    violation_map = {}  # floor -> [plates]
    pending_events = EventLog.query.filter_by(
        category='parking', status='pending'
    ).all()
    for e in pending_events:
        if e.plate_number:
            violation_map.setdefault(e.floor, []).append(e.plate_number)

    for floor, floor_spots in floors_map.items():
        occupied_count, total, rate, state = floor_occupancy(floor)
        # 时间种子：10分钟内随机分布稳定
        seed = int(ConsistencyModel.time_seed().timestamp()) + floor * 97
        rnd = _random.Random(seed)
        # 随机打乱该层车位顺序
        shuffled = list(floor_spots)
        rnd.shuffle(shuffled)

        # 故障车位：每层约2-3%标记为fault
        fault_count = max(1, total // 40) if floor in (2, 3, 5) else 0
        # 违规车位：本层有多少条违规告警就标记几个（违规车也占车位）
        violation_plates = violation_map.get(floor, [])
        violation_count = len(violation_plates)
        # 纯占用车位数 = 模型占用数 - 违规数（违规车也算被占用）
        pure_occupied = max(occupied_count - violation_count, 0)
        # 占用车位从剩余车位中选
        remaining = [s for s in shuffled if s not in shuffled[:fault_count + violation_count]]
        occupied_ids = {s.id for s in remaining[:pure_occupied]}

        # 本层违规车位列表（按洗牌顺序），用于更新告警位置
        violation_spots = shuffled[fault_count:fault_count + violation_count]

        for i, s in enumerate(shuffled):
            target = None
            if i < fault_count:
                target = 'fault'
            elif i < fault_count + violation_count:
                target = 'violation'
            elif s.id in occupied_ids:
                target = 'occupied'
            else:
                target = 'idle'
            if s.status != target:
                s.status = target
                s.last_updated = datetime.now()

        # 实时更新该层违规告警的位置为具体车位编号（随车位轮换同步更新）
        floor_alerts = [e for e in pending_events
                        if e.floor == floor and e.plate_number]
        for k, alert in enumerate(floor_alerts):
            if k < len(violation_spots):
                spot = violation_spots[k]
                new_position = f'{spot.floor}F-{spot.zone}区 {spot.spot_code}车位'
                if alert.position != new_position:
                    alert.position = new_position
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    return floors_map

def _spot_to_dict_with_detail(spot):
    """车位详情：基础字段 + 车辆/时长/费用/告警字段（文档3.1）"""
    import random
    d = spot.to_dict()
    now = datetime.now()

    # 车位类型中文名（前端直接显示用）
    d['spot_type_name'] = {
        'normal': '普通车位',
        'special': '专用车位',
        'charging': '充电车位',
    }.get(spot.spot_type, '普通车位')

    # 确定性车牌池（与同步函数一致）
    wuhan_plates = [
        '鄂A·D7351', '鄂A·6X9F2', '鄂W·D12345', '鄂A·3K8M7', '鄂A·9P2Q5',
        '鄂W·D88762', '鄂A·5T4V8', '鄂A·7H3J9', '鄂W·D55612', '鄂A·2B6C4',
        '鄂A·8M5N1', '鄂W·D33459', '鄂A·4X7Y9', '鄂A·1K9L3', '鄂W·D99021',
        '鄂A·6Q8W2', '鄂A·3Z5X7', '鄂W·D66238', '鄂A·9C4V6', '鄂A·5G8H1',
        '鄂W·D77541', '鄂A·7J2K9', '鄂A·2M6N8', '鄂W·D44837', '鄂A·4T9U2',
        '鄂A·8P3Q6', '鄂W·D22358', '鄂A·1V7W4', '鄂A·6Y2Z9', '鄂W·D88154',
    ]

    # 查找当前占用车辆
    plate = None
    entry = None
    duration_hours = None
    if spot.status in ('occupied', 'violation'):
        # 违规车位：从待处理告警中取对应的违规车牌（按车位ID错开，不重复）
        if spot.status == 'violation':
            alerts = EventLog.query.filter_by(
                category='parking', status='pending', floor=spot.floor
            ).order_by(EventLog.id.asc()).all()
            if alerts:
                alert = alerts[spot.id % len(alerts)]
                if alert.plate_number:
                    plate = alert.plate_number
                    entry = now - timedelta(hours=(alert.id % 5) + 1)
                    duration_hours = round((now - entry).total_seconds() / 3600, 1)
        if plate is None:
            record = ParkingRecord.query.filter_by(
                spot_id=spot.id, status='parking'
            ).order_by(ParkingRecord.entry_time.desc()).first()
            if record and record.plate_number:
                plate = record.plate_number
                entry = record.entry_time
                duration_hours = round(
                    (now - record.entry_time).total_seconds() / 3600, 1)
            else:
                # 无记录时用确定性车牌（按车位ID从车牌池稳定取，保证不跳动）
                plate = wuhan_plates[spot.id % len(wuhan_plates)]
                entry = now - timedelta(hours=(spot.id % 7) + 1)
                duration_hours = round((now - entry).total_seconds() / 3600, 1)

    # 充电字段
    charging_power_kw = None
    charging_start_time = None
    if spot.is_charging_spot and spot.status == 'occupied':
        charging_power_kw = round(random.uniform(6.5, 60.0), 1)
        charging_start_time = entry

    # 费用：2.5元/小时（不足1小时按1小时）
    fee = round(duration_hours * 2.5, 1) if duration_hours else 0.0
    # 超时：超过8小时标记
    is_overtime = bool(duration_hours and duration_hours > 8)
    penalty = round((duration_hours - 8) * 5, 1) if is_overtime else 0.0

    # 告警：违规车位必有告警，普通占用车位小概率有告警
    has_alarm = False
    alarm_desc = None
    if spot.status == 'violation':
        alerts = EventLog.query.filter_by(
            category='parking', status='pending', floor=spot.floor
        ).order_by(EventLog.id.asc()).all()
        if alerts:
            a = alerts[spot.id % len(alerts)]
            has_alarm = True
            alarm_desc = a.description
    elif spot.status == 'occupied':
        alarm = EventLog.query.filter_by(
            category='parking', status='pending'
        ).first()
        if alarm and random.random() < 0.15:
            has_alarm = True
            alarm_desc = alarm.description

    d.update({
        'plate_number': plate,
        'entry_time': entry.strftime('%Y-%m-%dT%H:%M:%S+08:00') if entry else None,
        'parking_duration_hours': duration_hours,
        'charging_power_kw': charging_power_kw,
        'charging_start_time': charging_start_time.strftime('%Y-%m-%dT%H:%M:%S+08:00') if charging_start_time else None,
        'fee_amount': fee,
        'is_overtime': is_overtime,
        'penalty_amount': penalty,
        'has_alarm': has_alarm,
        'alarm_description': alarm_desc,
    })
    return d

@app.route('/api/spots', methods=['GET'])
def get_all_spots():
    floor = request.args.get('floor', type=int)
    query = ParkingSpot.query
    if floor:
        query = query.filter_by(floor=floor)
    spots = query.all()

    # 同步 DB 状态与一致性模型（保证车位管理楼层视图与综合态势一致）
    _sync_spots_with_model(spots)

    result = [_spot_to_dict_with_detail(s) for s in spots]
    return jsonify({"code": 200, "message": "success",
                    "data": result, "total": len(spots)})

@app.route('/api/spots/<int:spot_id>', methods=['GET'])
def get_spot(spot_id):
    spot = ParkingSpot.query.get(spot_id)
    if not spot:
        return jsonify({"code": 404, "message": "车位不存在"}), 404
    return jsonify({"code": 200, "data": _spot_to_dict_with_detail(spot)})

@app.route('/api/spots', methods=['POST'])
def add_spot():
    data = request.get_json()
    new_spot = ParkingSpot(
        spot_code=data.get('spot_code'),
        floor=data.get('floor', 1),
        zone=data.get('zone', ''),
        x_coord=data.get('x_coord', 0.0),
        y_coord=data.get('y_coord', 0.0),
        spot_type=data.get('spot_type', 'normal'),
        spot_size=data.get('spot_size', 'medium'),
        is_charging_spot=data.get('is_charging_spot', False),
        is_special=data.get('is_special', False),
        is_emergency=data.get('is_emergency', False),
        status=data.get('status', 'idle')
    )
    db.session.add(new_spot)
    db.session.commit()
    return jsonify({"code": 200, "data": new_spot.to_dict()})

@app.route('/api/spots/<int:spot_id>', methods=['PUT'])
def update_spot(spot_id):
    spot = ParkingSpot.query.get(spot_id)
    if not spot:
        return jsonify({"code": 404, "message": "车位不存在"}), 404
    data = request.get_json()
    for k, v in data.items():
        if hasattr(spot, k):
            setattr(spot, k, v)
    db.session.commit()
    return jsonify({"code": 200, "data": spot.to_dict()})

# ========== 车辆 & 车位分配API ==========
@app.route('/api/vehicle/entry', methods=['POST'])
def vehicle_entry():
    data = request.get_json()
    result = ParkingAssigner.assign_spots(
        plate_number=data.get('plate_number'),
        vehicle_type=data.get('vehicle_type', 'normal'),
        is_electric=data.get('is_electric', False),
        need_charging=data.get('need_charging', False),
        charge_mode=data.get('charge_mode', 'slow')
    )
    if 'error' in result:
        return jsonify({'code': 400, 'msg': result['error']}), 400
    return jsonify({'code': 200, 'data': result})

@app.route('/api/assignment/<int:aid>/confirm', methods=['POST'])
def confirm_assignment(aid):
    data = request.get_json()
    result = ParkingAssigner.confirm_spot(aid, data.get('selected_option', 1))
    if 'error' in result:
        return jsonify({'code': 400, 'msg': result['error']}), 400
    return jsonify({'code': 200, 'data': result})

# ========== 区域 & 灯控API ==========
@app.route('/api/zones', methods=['GET'])
def get_zones():
    floor = request.args.get('floor', type=int)
    query = Zone.query
    if floor:
        query = query.filter_by(floor=floor)
    zones = query.all()
    return jsonify({'code': 200, 'data': [z.to_dict() for z in zones]})

@app.route('/api/zone/<int:zid>/light', methods=['POST'])
def control_zone_light(zid):
    data = request.get_json()
    action = data.get('action')
    zone = Zone.query.get(zid)
    if not zone:
        return jsonify({'code': 404, 'msg': '区域不存在'}), 404
    
    if action == 'normal_on':
        zone.normal_light_on = True
        zone.sound_light_on = False
    elif action == 'normal_off':
        zone.normal_light_on = False
    elif action == 'sound_on':
        if not zone.normal_light_on:
            zone.sound_light_on = True
    elif action == 'sound_off':
        zone.sound_light_on = False
    else:
        return jsonify({'code': 400, 'msg': '无效操作'}), 400
    
    db.session.commit()
    return jsonify({'code': 200, 'data': zone.to_dict()})

# ========== 充电桩API ==========
@app.route('/api/charging/piles', methods=['GET'])
def get_charging_piles():
    floor = request.args.get('floor', type=int)
    query = ChargingPile.query
    if floor:
        query = query.filter_by(floor=floor)
    piles = query.all()
    return jsonify({'code': 200, 'data': [p.to_dict() for p in piles]})

@app.route('/api/charging/start', methods=['POST'])
def start_charging():
    data = request.get_json()
    pile = ChargingPile.query.get(data.get('pile_id'))
    if not pile or pile.status != 'idle':
        return jsonify({'code': 400, 'msg': '充电桩不可用'}), 400
    
    pile.status = 'charging'
    pile.charge_mode = data.get('charge_mode', 'slow')
    
    record = ChargingRecord(
        pile_id=pile.id,
        plate_number=data.get('plate_number'),
        charge_mode=pile.charge_mode,
        start_battery=data.get('start_battery', 20.0),
        status='charging'
    )
    db.session.add(record)
    db.session.commit()
    return jsonify({'code': 200, 'data': {'record_id': record.id, 'msg': '开始充电'}})

@app.route('/api/charging/stop', methods=['POST'])
def stop_charging():
    data = request.get_json()
    record = ChargingRecord.query.get(data.get('record_id'))
    if not record or record.status != 'charging':
        return jsonify({'code': 400, 'msg': '充电记录不存在'}), 400
    
    pile = ChargingPile.query.get(record.pile_id)
    record.end_time = datetime.now()
    record.end_battery = data.get('end_battery', 100.0)
    record.is_full = record.end_battery >= 100
    
    hours = (record.end_time - record.start_time).total_seconds() / 3600
    power = pile.power_kw if record.charge_mode == 'fast' else 7.0
    record.total_kwh = power * hours * 0.8
    record.total_fee = record.total_kwh * pile.price_charging
    record.status = 'completed'
    
    pile.status = 'idle'
    db.session.commit()
    
    return jsonify({'code': 200, 'data': {
        'total_kwh': round(record.total_kwh, 2),
        'total_fee': round(record.total_fee, 2),
        'is_full': record.is_full
    }})

# ========== 楼层闸机API ==========
@app.route('/api/gates', methods=['GET'])
def get_gates():
    gates = FloorGate.query.all()
    return jsonify({'code': 200, 'data': [g.to_dict() for g in gates]})

@app.route('/api/gate/<int:gid>/trigger', methods=['POST'])
def trigger_gate(gid):
    data = request.get_json()
    action = data.get('action')
    plate_number = data.get('plate_number')
    
    gate = FloorGate.query.get(gid)
    if not gate:
        return jsonify({'code': 404, 'msg': '闸机不存在'}), 404
    
    if action == 'open' and plate_number:
        vehicle = Vehicle.query.filter_by(plate_number=plate_number).first()
        if not vehicle or not vehicle.assigned_floor:
            return jsonify({'code': 403, 'msg': '车辆未分配楼层'}), 403
        if gate.direction == 'up' and gate.floor > vehicle.assigned_floor:
            return jsonify({'code': 403, 'msg': '无权限进入更高楼层'}), 403
    
    gate.status = 'open' if action == 'open' else 'closed'
    gate.last_action_time = datetime.now()
    db.session.commit()
    return jsonify({'code': 200, 'data': gate.to_dict()})

# ========== 能耗API ==========
@app.route('/api/energy/24h', methods=['GET'])
def get_energy_24h():
    """24小时能耗 — 按时间生成：只到当前小时，未来小时无数据"""
    floor = request.args.get('floor', type=int)
    now = datetime.now()
    records = []
    for h in range(24):
        t = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23 - h)
        if t > now:
            continue  # 未来小时不返回数据
        # 用该小时的时段系数生成功率
        if floor:
            base = floor_power_kw(floor, t)
            usage = round(base * 0.8, 2)
            category = 'charging' if floor in (3, 4) else 'lighting'
        else:
            usage = round(sum(floor_power_kw(f, t) for f in range(1, 8)) * 0.8, 2)
            category = None
        records.append({
            'id': h + 1,
            'floor': floor or 1,
            'power_usage': usage,
            'power_kw': round(usage / 0.8, 2),
            'device_category': category,
            'record_time': t.strftime('%Y-%m-%d %H:%M:%S')
        })
    return jsonify({"code": 200, "data": records, "total": len(records)})

@app.route('/api/energy/analysis', methods=['GET'])
def energy_analysis():
    floor = request.args.get('floor', type=int)
    query = EnergyRecord.query
    if floor:
        query = query.filter_by(floor=floor)
    records = query.all()
    if not records:
        return jsonify({"code": 200, "data": []})
    avg = sum(r.power_usage for r in records) / len(records)
    abnormal = [r.to_dict() for r in records if r.power_usage > avg * 1.5]
    return jsonify({"code": 200, "data": {"average": round(avg, 2), "abnormal": abnormal}})

# ========== 门禁API（旧版兼容）==========
@app.route('/api/gate/status', methods=['GET'])
def get_gate_status():
    gates = Device.query.filter_by(device_type='gate').all()
    return jsonify({"code": 200, "data": [g.to_dict() for g in gates]})

@app.route('/api/gate/trigger', methods=['POST'])
def trigger_device_gate():
    data = request.get_json()
    return jsonify({"code": 200, "msg": "请使用新接口 /api/gate/<id>/trigger"})

# ========== 事件日志API ==========
@app.route('/api/events', methods=['GET'])
def get_events():
    event_type = request.args.get('type')
    floor = request.args.get('floor', type=int)
    category = request.args.get('category')
    # 定时轮换告警时间 + 强制执行处理时限
    _refresh_event_times()
    _enforce_event_deadlines()
    query = EventLog.query
    if event_type: query = query.filter_by(event_type=event_type)
    if floor: query = query.filter_by(floor=floor)
    if category: query = query.filter_by(category=category)
    events = query.order_by(EventLog.timestamp.desc()).limit(200).all()
    return jsonify({"code": 200, "data": [e.to_dict() for e in events]})

@app.route('/api/events', methods=['POST'])
def add_event():
    data = request.get_json()
    new_event = EventLog(
        event_type=data.get('event_type'),
        floor=data.get('floor', 1),
        description=data.get('description', '')
    )
    db.session.add(new_event)
    db.session.commit()
    return jsonify({"code": 200, "data": new_event.to_dict()})

# ========== 设备API ==========
@app.route('/api/devices', methods=['GET'])
def get_devices():
    device_type = request.args.get('type')
    floor = request.args.get('floor', type=int)
    # 设备状态与事件状态同步：已处理事件→设备在线，未处理→fault
    _sync_device_status_with_events()
    query = Device.query
    if device_type: query = query.filter_by(device_type=device_type)
    if floor: query = query.filter_by(floor=floor)
    devices = query.all()
    return jsonify({"code": 200, "data": [d.to_dict() for d in devices]})

@app.route('/api/devices/<device_code>/heartbeat', methods=['POST'])
def device_heartbeat(device_code):
    device = Device.query.filter_by(device_code=device_code).first()
    if not device:
        data = request.get_json() or {}
        new_device = Device(
            device_code=device_code,
            device_type=data.get('device_type', 'sensor'),
            floor=data.get('floor', 1),
            location=data.get('location', ''),
            status='online'
        )
        db.session.add(new_device)
        db.session.commit()
        return jsonify({"code": 200, "data": new_device.to_dict()})
    device.last_heartbeat = datetime.now()
    device.status = 'online'
    db.session.commit()
    return jsonify({"code": 200, "data": device.to_dict()})

# ========== 车辆离场 API ==========
@app.route('/api/vehicle/exit', methods=['POST'])
def vehicle_exit():
    """车辆离场，释放车位，更新停车记录"""
    data = request.get_json()
    result = ParkingAssigner.vehicle_exit(
        plate_number=data.get('plate_number')
    )
    if 'error' in result:
        return jsonify({'code': 400, 'msg': result['error']}), 400
    return jsonify({'code': 200, 'data': result})

# ========== 用户画像 API ==========
@app.route('/api/user/profile/<plate_number>', methods=['GET'])
def get_user_profile(plate_number):
    """查看用户画像"""
    profile = UserProfile.query.filter_by(plate_number=plate_number).first()
    if not profile:
        # 尝试创建
        profile = UserProfileService.get_or_create_profile(plate_number)
        UserProfileService.classify_user(plate_number)
    return jsonify({'code': 200, 'data': profile.to_dict()})

@app.route('/api/user/profiles', methods=['GET'])
def list_user_profiles():
    """用户画像列表"""
    user_type = request.args.get('type')
    user_tag = request.args.get('tag')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    query = UserProfile.query
    if user_type:
        query = query.filter_by(user_type=user_type)
    if user_tag:
        query = query.filter(UserProfile.user_tag.contains(user_tag))

    total = query.count()
    profiles = query.order_by(
        UserProfile.total_parking_count.desc()
    ).offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        'code': 200,
        'data': [p.to_dict() for p in profiles],
        'total': total,
        'page': page
    })

@app.route('/api/user/tag', methods=['POST'])
def tag_user():
    """手动标记用户"""
    data = request.get_json()
    profile = UserProfile.query.filter_by(
        plate_number=data.get('plate_number')
    ).first()
    if not profile:
        return jsonify({'code': 404, 'msg': '用户不存在'}), 404

    profile.user_tag = data.get('tag')
    db.session.commit()
    return jsonify({'code': 200, 'data': profile.to_dict()})

@app.route('/api/user/stats/<plate_number>', methods=['GET'])
def get_user_stats(plate_number):
    """获取用户停车统计"""
    profile = UserProfileService.get_or_create_profile(plate_number)
    UserProfileService.update_profile(plate_number)

    # 最近10条停车记录
    recent = ParkingRecord.query.filter_by(
        plate_number=plate_number
    ).order_by(ParkingRecord.entry_time.desc()).limit(10).all()

    return jsonify({
        'code': 200,
        'data': {
            'profile': profile.to_dict(),
            'recent_records': [r.to_dict() for r in recent]
        }
    })

@app.route('/api/user/upgrade/<plate_number>', methods=['POST'])
def upgrade_user(plate_number):
    """临时用户升级为长期用户"""
    result = UserProfileService.upgrade_user(plate_number)
    if 'error' in result:
        return jsonify({'code': 400, 'msg': result['error']}), 400
    return jsonify({'code': 200, 'data': result})

# ========== 停车记录 API ==========
@app.route('/api/records', methods=['GET'])
def get_parking_records():
    """停车记录查询"""
    plate_number = request.args.get('plate_number')
    floor = request.args.get('floor', type=int)
    status = request.args.get('status')
    date = request.args.get('date')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    query = ParkingRecord.query
    if plate_number:
        query = query.filter_by(plate_number=plate_number)
    if floor:
        query = query.filter_by(floor=floor)
    if status:
        query = query.filter_by(status=status)
    if date:
        day = datetime.strptime(date, '%Y-%m-%d')
        day_end = day + timedelta(days=1)
        query = query.filter(
            ParkingRecord.entry_time >= day,
            ParkingRecord.entry_time < day_end
        )

    total = query.count()
    records = query.order_by(
        ParkingRecord.entry_time.desc()
    ).offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        'code': 200,
        'data': [r.to_dict() for r in records],
        'total': total,
        'page': page
    })

@app.route('/api/records/stats', methods=['GET'])
def get_parking_record_stats():
    """停车记录统计概览"""
    today = datetime.now().date()
    today_start = datetime.combine(today, datetime.min.time())

    # 今日统计
    today_total = ParkingRecord.query.filter(
        ParkingRecord.entry_time >= today_start
    ).count()
    today_completed = ParkingRecord.query.filter(
        ParkingRecord.entry_time >= today_start,
        ParkingRecord.status == 'completed'
    ).count()
    today_parking = today_total - today_completed

    # 平均时长
    avg_duration = db.session.query(
        db.func.avg(ParkingRecord.duration)
    ).filter(
        ParkingRecord.entry_time >= today_start,
        ParkingRecord.duration.isnot(None)
    ).scalar()

    return jsonify({
        'code': 200,
        'data': {
            'today_total': today_total,
            'today_parking': today_parking,
            'today_completed': today_completed,
            'today_avg_duration_minutes': round(avg_duration, 1) if avg_duration else 0
        }
    })

# ========== 数据分析 API ==========
@app.route('/api/analysis/peak-hours', methods=['GET'])
def get_peak_hours():
    """高峰时段统计"""
    date_str = request.args.get('date')
    date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else None
    result = AnalysisService.get_peak_hours(date)
    return jsonify({'code': 200, 'data': result})

@app.route('/api/analysis/turnover', methods=['GET'])
def get_turnover():
    """车位周转率"""
    floor = request.args.get('floor', type=int)
    date_str = request.args.get('date')
    date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else None
    result = AnalysisService.get_turnover_rate(floor, date)
    return jsonify({'code': 200, 'data': result})

@app.route('/api/analysis/occupancy', methods=['GET'])
def get_occupancy():
    """当前占用率及各层分布 — 共享一致性模型"""
    occupied, total, rate = overall_occupancy()
    floors_data = []
    for f in range(1, 8):
        occ, tot, frate, state = floor_occupancy(f)
        floors_data.append({
            'floor': f, 'total': tot, 'occupied': occ,
            'idle': tot - occ, 'occupancy_rate': frate, 'state': state
        })
    alert = None
    if rate > 95:
        alert = '停车场已饱和，建议关闭入口'
    elif rate > 85:
        alert = '停车场接近饱和，建议引导至其他停车场'
    return jsonify({'code': 200, 'data': {
        'overall': {'total': total, 'occupied': occupied,
                    'rate': rate, 'alert': alert},
        'floors': floors_data
    }})

@app.route('/api/analysis/compare', methods=['GET'])
def compare_workday_holiday():
    """工作日/节假日流量对比"""
    date_str = request.args.get('date')
    date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else None
    result = AnalysisService.compare_workday_holiday(date)
    return jsonify({'code': 200, 'data': result})

@app.route('/api/analysis/anomaly', methods=['GET'])
def get_anomalies():
    """异常车位检测"""
    result = AnalysisService.detect_anomaly_spots()
    return jsonify({'code': 200, 'data': result})

# ========== 数据预测 API ==========
@app.route('/api/predict/arrival/<plate_number>', methods=['GET'])
def predict_arrival(plate_number):
    """预测用户到达时间"""
    result = PredictionService.predict_arrival_time(plate_number)
    if 'error' in result:
        return jsonify({'code': 400, 'msg': result['error']}), 400
    return jsonify({'code': 200, 'data': result})

@app.route('/api/predict/duration/<plate_number>', methods=['GET'])
def predict_duration(plate_number):
    """预测停车时长"""
    result = PredictionService.predict_parking_duration(plate_number)
    if 'error' in result:
        return jsonify({'code': 400, 'msg': result['error']}), 400
    return jsonify({'code': 200, 'data': result})

@app.route('/api/predict/spot/<plate_number>', methods=['GET'])
def predict_spot(plate_number):
    """预测车位偏好"""
    result = PredictionService.predict_spot_preference(plate_number)
    if 'error' in result:
        return jsonify({'code': 400, 'msg': result['error']}), 400
    return jsonify({'code': 200, 'data': result})

@app.route('/api/predict/peak-flow', methods=['GET'])
def predict_peak_flow():
    """高峰流量预测"""
    date_str = request.args.get('date')
    date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else None
    hour = request.args.get('hour', type=int)
    result = PredictionService.predict_peak_flow(date, hour)
    return jsonify({'code': 200, 'data': result})

# ========== 节假日管理 API ==========
@app.route('/api/holidays', methods=['GET'])
def get_holidays():
    """节假日列表"""
    year = request.args.get('year', type=int, default=datetime.now().year)
    start = datetime(year, 1, 1)
    end = datetime(year + 1, 1, 1)
    holidays = HolidayConfig.query.filter(
        HolidayConfig.date >= start,
        HolidayConfig.date < end
    ).order_by(HolidayConfig.date).all()
    return jsonify({'code': 200, 'data': [h.to_dict() for h in holidays]})

@app.route('/api/holidays', methods=['POST'])
def add_holiday():
    """添加节假日配置"""
    data = request.get_json()
    holiday = HolidayConfig(
        name=data.get('name'),
        date=datetime.strptime(data.get('date'), '%Y-%m-%d').date(),
        holiday_type=data.get('holiday_type', 'holiday'),
        peak_factor=data.get('peak_factor', 1.0),
        description=data.get('description', '')
    )
    db.session.add(holiday)
    db.session.commit()
    return jsonify({'code': 200, 'data': holiday.to_dict()})

# ========== 设备维护 API ==========
@app.route('/api/devices/maintenance', methods=['GET'])
def get_maintenance_records():
    """设备维护记录列表"""
    device_id = request.args.get('device_id', type=int)
    query = DeviceMaintenance.query
    if device_id:
        query = query.filter_by(device_id=device_id)
    records = query.order_by(DeviceMaintenance.create_time.desc()).limit(100).all()
    return jsonify({'code': 200, 'data': [r.to_dict() for r in records]})

@app.route('/api/devices/<int:device_id>/maintenance', methods=['POST'])
def add_maintenance(device_id):
    """添加设备维护记录"""
    device = Device.query.get(device_id)
    if not device:
        return jsonify({'code': 404, 'msg': '设备不存在'}), 404

    data = request.get_json()
    record = DeviceMaintenance(
        device_id=device_id,
        device_code=device.device_code,
        maintenance_type=data.get('maintenance_type', 'routine'),
        description=data.get('description', ''),
        operator=data.get('operator', ''),
        cost=data.get('cost', 0.0)
    )
    db.session.add(record)

    # 更新设备维护时间
    device.last_maintenance_time = datetime.now()
    if record.maintenance_type == 'replace':
        device.open_count = 0  # 更换后清零计数

    db.session.commit()
    return jsonify({'code': 200, 'data': record.to_dict()})

@app.route('/api/devices/health', methods=['GET'])
def get_device_health():
    """设备健康概览"""
    result = AnalysisService.get_device_health()
    return jsonify({'code': 200, 'data': result})

# ========== 路径规划 API ==========
@app.route('/api/path/find', methods=['GET'])
def find_path():
    """A* 寻路：从入口到目标车位的最优路径"""
    spot_id = request.args.get('spot_id', type=int)
    max_floor = request.args.get('max_floor', type=int)
    only_charging = request.args.get('only_charging', 'false').lower() == 'true'
    only_special = request.args.get('only_special', 'false').lower() == 'true'

    from parking_graph import build_graph_from_db
    from stereo_astar import astar_search, find_nearest_free_spot

    graph = build_graph_from_db()
    occupied = [s.spot_code for s in ParkingSpot.query.filter_by(
        status='occupied').all()]

    if spot_id:
        # 找去指定车位的路径
        spot = ParkingSpot.query.get(spot_id)
        if not spot:
            return jsonify({'code': 404, 'msg': '车位不存在'}), 404
        target_id = None
        for nid, node in graph.nodes.items():
            if node.spot_code == spot.spot_code:
                target_id = nid
                break
        if not target_id:
            return jsonify({'code': 404, 'msg': '车位不在路网中'}), 404
        path, cost = astar_search(graph, "F1_GATE_ENTRY", target_id,
                                  max_floor=max_floor)
        return jsonify({'code': 200, 'data': {
            'type': 'specific_spot',
            'spot_code': spot.spot_code,
            'path': path,
            'path_cost': round(cost, 2),
            'path_length': len(path)
        }})

    # 找最近空闲车位
    best, path, cost = find_nearest_free_spot(
        graph, "F1_GATE_ENTRY",
        occupied_spots=occupied,
        max_floor=max_floor,
        only_charging=only_charging,
        only_special=only_special
    )

    if not best:
        return jsonify({'code': 404, 'msg': '未找到可用车位'}), 404

    spot_code = graph.nodes[best].spot_code
    return jsonify({'code': 200, 'data': {
        'type': 'nearest_free',
        'spot_code': spot_code,
        'path': path,
        'path_cost': round(cost, 2),
        'path_length': len(path)
    }})

@app.route('/api/path/reroute', methods=['POST'])
def reroute_path():
    """动态重规划：封路/故障后重新寻路"""
    data = request.get_json()
    current_node = data.get('current_node', 'F1_GATE_ENTRY')
    target_node = data.get('target_node')
    blocked_edges = data.get('blocked_edges', [])
    blocked_nodes = data.get('blocked_nodes', [])
    max_floor = data.get('max_floor')

    from parking_graph import build_graph_from_db
    from dynamic_reroute import DynamicRerouter
    from stereo_astar import astar_search

    graph = build_graph_from_db()
    rerouter = DynamicRerouter(graph)

    for edge in blocked_edges:
        rerouter.block_edge(edge[0], edge[1])
    for node in blocked_nodes:
        rerouter.block_node(node)

    if target_node:
        path, cost = rerouter.reroute(current_node, target_node,
                                       max_floor=max_floor)
        return jsonify({'code': 200, 'data': {
            'rerouted': True,
            'current_node': current_node,
            'target_node': target_node,
            'path': path,
            'path_cost': round(cost, 2),
            'blocked_edges': blocked_edges,
            'blocked_nodes': blocked_nodes
        }})

    return jsonify({'code': 400, 'msg': '请指定 target_node'}), 400

@app.route('/api/path/graph-info', methods=['GET'])
def graph_info():
    """获取路网图信息"""
    from parking_graph import build_graph_from_db
    graph = build_graph_from_db()

    nodes_by_type = {}
    for nid, node in graph.nodes.items():
        t = node.node_type
        nodes_by_type[t] = nodes_by_type.get(t, 0) + 1

    return jsonify({'code': 200, 'data': {
        'total_nodes': len(graph.nodes),
        'total_edges': len(graph.edges),
        'nodes_by_type': nodes_by_type,
        'sample_nodes': [
            {'id': nid, 'floor': node.floor, 'zone': node.zone,
             'type': node.node_type, 'spot_code': node.spot_code}
            for nid, node in list(graph.nodes.items())[:10]
        ]
    }})

# ========== 未来停车流量预测 API ==========
@app.route('/api/predict/traffic/24h', methods=['GET'])
def predict_traffic_24h():
    """当日24小时车辆出入（0-23时）：已过小时为实际值，未来小时为预测值 — 共享时段模型"""
    now = datetime.now()
    result = []

    for h in range(24):
        t = now.replace(hour=h, minute=0, second=0, microsecond=0)
        factor = ConsistencyModel.time_factor(t)
        entries, exits = ConsistencyModel.hourly_traffic(t)
        if 7 <= h <= 10:
            peak = 'morning'
        elif 17 <= h <= 20:
            peak = 'evening'
        elif 11 <= h <= 13:
            peak = 'noon'
        elif 22 <= h or h <= 6:
            peak = 'night'
        else:
            peak = 'normal'

        is_future = h > now.hour
        result.append({
            'hour': h,
            'time_label': f'{h:02d}:00',
            'date_label': now.strftime('%m-%d'),
            # 全天24小时都填值（已过=实际，未来=预测），前端直接画曲线
            'predicted_entries': entries,
            'predicted_exits': exits,
            'is_future': is_future,
            'peak_type': peak,
        })

    return jsonify({'code': 200, 'data': result})

# ========== 天气 API ==========
@app.route('/api/weather', methods=['GET'])
def get_weather():
    """当前天气情况（模拟数据，可接入真实天气API）"""
    import random
    from datetime import datetime
    hour = datetime.now().hour

    # 根据时段模拟合理天气
    if 5 <= hour < 12:
        weather = '晴'
        temp = random.randint(24, 30)
    elif 12 <= hour < 18:
        weather = random.choice(['晴', '多云'])
        temp = random.randint(30, 35)
    elif 18 <= hour < 22:
        weather = '多云'
        temp = random.randint(26, 30)
    else:
        weather = random.choice(['多云', '阴'])
        temp = random.randint(22, 26)

    return jsonify({'code': 200, 'data': {
        'weather': weather,               # 天气：晴/多云/阴/雨
        'temperature': temp,              # 室外温度 ℃
        'humidity': random.randint(45, 85),   # 湿度 %
        'wind': random.choice(['东北风', '西南风', '东南风', '西北风']),
        'wind_level': random.randint(1, 4),   # 风力等级
        'air_quality': random.choice(['优', '良']),
        'aqi': random.randint(20, 80),
        'update_time': datetime.now().strftime('%H:%M')
    }})

# ========== 消防AI风险研判 API ==========
@app.route('/api/fire/ai-risk', methods=['GET'])
def get_fire_ai_risk():
    """智慧消防：AI风险研判（风险概率+研判结论+设备状态提示）"""
    import random

    # 基于数据库真实数据计算风险
    fault_devices = Device.query.filter(
        Device.device_type.in_(['light', 'fan', 'elevator']),
        Device.status.in_(['fault', 'offline'])
    ).count()

    fire_events = EventLog.query.filter(
        EventLog.category == 'device',
        EventLog.description.like('%温度%')
    ).count()

    # 风险概率计算：故障设备越多风险越高，夜间风险更低
    hour = datetime.now().hour
    time_factor = 0.6 if hour < 6 or hour >= 22 else 1.0  # 夜间人少风险低
    base_risk = min(fault_devices * 0.05 + fire_events * 0.1, 5.0)
    risk_prob = round((base_risk + random.uniform(0, 0.3)) * time_factor, 1)

    if risk_prob < 1.0:
        status = 'safe'
        status_text = '✅ 无火灾风险'
    elif risk_prob < 2.5:
        status = 'warning'
        status_text = '⚠️ 存在火灾隐患'
    else:
        status = 'danger'
        status_text = '🚨 高风险需立即处理'

    # 研判结论（根据实时设备状态）
    notes = []
    hot_floors = [f'F{f}' for f in range(1, 8) if Device.query.filter_by(
        floor=f, device_type='light', status='fault').count() > 0]
    if hot_floors and random.random() < 0.5:
        notes.append({'type': 'warning', 'text': f'⚠️ {random.choice(hot_floors)}温度偏高'})
    else:
        notes.append({'type': 'ok', 'text': '✅ 各层温度正常'})

    if Device.query.filter_by(device_type='fan', status='online').count() > 0:
        notes.append({'type': 'ok', 'text': '✅ 排风系统运行正常'})
    else:
        notes.append({'type': 'warning', 'text': '⚠️ 排风系统故障'})

    if Device.query.filter_by(device_type='light', status='online').count() > 0:
        notes.append({'type': 'ok', 'text': '✅ 喷淋系统就绪'})

    return jsonify({'code': 200, 'data': {
        'status': status,
        'status_text': status_text,
        'risk_probability': risk_prob,
        'notes': notes
    }})

# ========== 消防告警趋势 API ==========
@app.route('/api/fire/alarm-trend', methods=['GET'])
def get_fire_alarm_trend():
    """智慧消防：近30天告警趋势（告警总数+高危告警+分类明细）"""
    from datetime import date as date_cls
    import random

    days = int(request.args.get('days', 30))

    # 查数据库里已有的统计
    stats = FireAlarmStat.query.order_by(FireAlarmStat.date.desc()).limit(days).all()

    if len(stats) < days:
        # 数据不足，补齐生成（真实分布：多数天0-2次告警，高危罕见）
        today = date_cls.today()
        existing_dates = {s.date for s in stats}
        new_stats = []
        for i in range(days):
            d = today - timedelta(days=days - 1 - i)
            if d not in existing_dates:
                # 70%天无告警，25%天1次，5%天2次
                r = random.random()
                total = 0 if r < 0.7 else (1 if r < 0.95 else 2)
                smoke = random.randint(0, 1) if total > 0 else 0
                temp = random.randint(0, 1) if total > 0 else 0
                co = 0
                eq = random.randint(0, 1) if total > 0 else 0
                high = 1 if random.random() < 0.2 and total > 0 else 0
                new_stats.append(FireAlarmStat(
                    date=d, total_alarms=total,
                    high_risk=high, smoke=smoke, temperature=temp,
                    co=co, equipment=eq))
        if new_stats:
            db.session.add_all(new_stats)
            db.session.commit()
        stats = FireAlarmStat.query.order_by(FireAlarmStat.date.desc()).limit(days).all()

    stats = sorted(stats, key=lambda s: s.date)

    return jsonify({'code': 200, 'data': {
        'days': [s.date.strftime('%m/%d') for s in stats],
        'total_alarms': [s.total_alarms for s in stats],
        'high_risk': [s.high_risk for s in stats],
        'smoke': [s.smoke for s in stats],
        'temperature': [s.temperature for s in stats],
        'co': [s.co for s in stats],
        'equipment': [s.equipment for s in stats]
    }})

# ========== 设备汇总 API（综合态势用）==========
@app.route('/api/devices/summary', methods=['GET'])
def get_device_summary():
    """设备分类汇总：总数/在线/故障/离线，供综合态势大屏使用"""
    type_names = {'charger': '充电桩', 'light': '照明系统', 'door': '门禁系统',
                  'gate': '地锁设备', 'fan': '排水风机', 'elevator': '电梯设备',
                  'camera': '监控设备', 'ac': '空调系统'}
    # 全部类型（包括0台的空调）
    all_types = ['charger', 'light', 'door', 'gate', 'ac', 'fan', 'elevator', 'camera']

    result = []
    for t in all_types:
        total = Device.query.filter_by(device_type=t).count()
        online = Device.query.filter_by(device_type=t, status='online').count()
        fault = Device.query.filter_by(device_type=t, status='fault').count()
        offline = Device.query.filter_by(device_type=t, status='offline').count()
        idle = Device.query.filter_by(device_type=t, status='idle').count()
        result.append({
            'device_type': t,
            'device_name': type_names[t],
            'total': total,
            'online': online + idle,   # idle 视为正常在线
            'repair': fault,
            'offline': offline,
        })

    return jsonify({'code': 200, 'data': result})

# ========== 设备详情弹窗 API ==========
@app.route('/api/devices/status-detail', methods=['GET'])
def device_status_detail():
    """设备分类各状态详情：某类设备某状态的具体设备列表（楼层/状态/功率）"""
    device_type = request.args.get('type')
    status = request.args.get('status')  # online / fault / offline
    floor = request.args.get('floor', type=int)

    query = Device.query
    if device_type:
        query = query.filter_by(device_type=device_type)
    if status:
        query = query.filter_by(status=status)
    if floor:
        query = query.filter_by(floor=floor)
    devices = query.order_by(Device.floor, Device.device_code).all()

    # 统计每个楼层该状态的设备数
    floors_map = {}
    for d in devices:
        floors_map.setdefault(d.floor, []).append(d)

    result = []
    for d in devices:
        # 今日运行时长
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        hours = (round((datetime.now() - today_start).total_seconds() / 3600, 1)
                 if d.last_heartbeat and d.last_heartbeat > today_start else 0.0)
        result.append({
            'id': d.id,
            'device_code': d.device_code,
            'device_type': d.device_type,
            'floor': f'B{abs(d.floor)}' if d.floor < 0 else f'F{d.floor}',
            'floor_num': d.floor,
            'status': d.status,
            'power_w': d.power_w,
            'is_on': d.is_on,
            'location': d.location,
            'run_hours_today': hours,
        })

    return jsonify({'code': 200, 'message': 'success', 'data': {
        'type': device_type,
        'status': status,
        'total': len(devices),
        'floors': [{'floor': f'B{abs(f)}' if f < 0 else f'F{f}',
                    'count': len(v)} for f, v in sorted(floors_map.items())],
        'list': result,
    }})

@app.route('/api/devices/<device_code>/history', methods=['GET'])
def device_history(device_code):
    """设备运行履历：开关机/告警/维修历史"""
    device = Device.query.filter_by(device_code=device_code).first()
    if not device:
        return jsonify({'code': 404, 'msg': '设备不存在'}), 404

    import random as _random
    seed = hash(device_code) % 100000
    rnd = _random.Random(seed)
    now = datetime.now()

    # 生成运行履历：最近的告警/操作/维修记录
    history = []
    # 1. 设备相关的告警事件（真实数据）
    related_events = EventLog.query.filter_by(
        device_id=device_code
    ).order_by(EventLog.timestamp.desc()).all()
    for e in related_events:
        history.append({
            'time': e.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'action': '告警',
            'detail': e.description,
            'operator': '系统',
            'result': '已处理' if e.status == 'handled' else '待处理',
        })

    # 2. 模拟历史操作记录（确定性生成，不会每次刷新变）
    op_templates = [
        ('日常巡检', '外观检查正常，运行参数在标准范围内', '巡检员'),
        ('开关机', '设备正常启停', '系统'),
        ('例行保养', '清洁除尘，紧固接线端子', '维保员'),
        ('参数校准', '校准运行参数，测试响应正常', '技术员'),
        ('远程控制', '远程开关操作成功', '系统'),
        ('能耗记录', '用电量数据上报正常', '系统'),
        ('状态检查', '通信在线，心跳正常', '巡检员'),
    ]
    for i in range(6):
        action, detail, op = op_templates[(i + seed) % len(op_templates)]
        days_ago = rnd.randint(0, 12)
        t = now - timedelta(days=days_ago, hours=rnd.randint(0, 8))
        history.append({
            'time': t.strftime('%Y-%m-%d %H:%M:%S'),
            'action': action,
            'detail': f'{detail}（{device.location}）',
            'operator': op,
            'result': '成功',
        })

    # 按时间倒序
    history.sort(key=lambda x: x['time'], reverse=True)

    return jsonify({'code': 200, 'message': 'success', 'data': {
        'device_code': device.device_code,
        'device_type': device.device_type,
        'location': device.location,
        'floor': f'B{abs(device.floor)}' if device.floor < 0 else f'F{device.floor}',
        'status': device.status,
        'power_w': device.power_w,
        'install_time': (now - timedelta(days=rnd.randint(100, 500))).strftime('%Y-%m-%d'),
        'total_records': len(history),
        'history': history[:20],
    }})

@app.route('/api/devices/detail', methods=['GET'])
def get_device_detail():
    """设备分类详情（弹窗用）：返回单类设备列表，带编号/功率/时长/开关"""
    device_type = request.args.get('type')
    if not device_type:
        return jsonify({'code': 400, 'msg': '缺少 type 参数'}), 400

    devices = Device.query.filter_by(device_type=device_type).order_by(
        Device.floor, Device.device_code
    ).all()

    type_names = {'charger': '充电桩', 'light': '照明', 'door': '门禁',
                  'gate': '地锁', 'fan': '风机', 'elevator': '电梯',
                  'camera': '监控', 'ac': '空调'}

    detail_list = []
    for d in devices:
        # 今日运行时长：用 last_heartbeat 距今天 0 点的时间差估算
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if d.last_heartbeat and d.last_heartbeat > today_start:
            hours = round((datetime.now() - today_start).total_seconds() / 3600, 1)
        else:
            hours = 0.0

        detail_list.append({
            'id': d.id,
            'name': d.device_code,                          # 设备编号
            'floor': f'B{abs(d.floor)}' if d.floor < 0 else f'F{d.floor}',
            'status': '正常' if d.status == 'online' else '异常',
            'power': d.power_w,
            'hours': hours,
            'on': d.is_on,
            'time': d.last_heartbeat.strftime('%H:%M') if d.last_heartbeat else '--:--',
            'location': d.location,
        })

    total = len(detail_list)
    online = sum(1 for x in detail_list if x['status'] == '正常')
    return jsonify({'code': 200, 'data': {
        'type': device_type,
        'type_name': type_names.get(device_type, device_type),
        'total': total,
        'online': online,
        'repair': total - online,
        'list': detail_list
    }})

# ========== 综合态势 API ==========
@app.route('/api/dashboard/numbers', methods=['GET'])
def dashboard_numbers():
    """综合态势：4个统计卡片（今日/本月 流量/耗电）— 共享一致性模型"""
    now = datetime.now()
    totals = energy_totals()
    entries, exits = ConsistencyModel.hourly_traffic()

    # 今日累计 = 当前小时均值 × 已过小时
    hours_elapsed = max(now.hour, 1)
    today_flow = entries * hours_elapsed
    today_power = totals['total_energy_kwh']
    # 本月按天数比例推演
    day_ratio = now.day / 28.0 if now.day < 28 else 1.0
    month_flow = round(today_flow / max(day_ratio, 0.1), 1)
    month_power = round(today_power / max(day_ratio, 0.1), 1)

    return jsonify({'code': 200, 'data': [
        {'name': '今日流量总量', 'num': str(round(today_flow, 1)), 'danw': 'G'},
        {'name': '今日耗电总量', 'num': str(round(today_power, 1)), 'danw': 'KW'},
        {'name': '本月流量总量', 'num': str(month_flow), 'danw': 'G'},
        {'name': '本月耗电总量', 'num': str(month_power), 'danw': 'KW'},
    ]})

@app.route('/api/dashboard/fire-stats', methods=['GET'])
def dashboard_fire_stats():
    """综合态势：消防告警统计（power.vue）"""
    import random
    fire_alarms = EventLog.query.filter(EventLog.description.like('%烟雾%')).count()
    last_month = fire_alarms - random.randint(3, 8)
    return jsonify({'code': 200, 'data': {
        'boxNum': fire_alarms, 'box1Num': max(last_month, 0),
        'top': '10%', 'bottom': '2%',
        'list': [
            {'name': '最高响应时间', 'num': 18.63, 'damw': '秒'},
            {'name': '最低响应时间', 'num': 2.61, 'damw': '秒'},
            {'name': '响应率', 'num': 98.2, 'damw': '%'},
            {'name': '平均响应时间', 'num': 8.61, 'damw': '秒'},
        ]
    }})

@app.route('/api/dashboard/security-trend', methods=['GET'])
def dashboard_security_trend():
    """综合态势：消防安全系数趋势（ranking.vue + build.vue仪表盘）"""
    import random
    days = [(datetime.now() - timedelta(days=11 - i)).strftime('%m-%d') for i in range(12)]
    trend = [random.randint(40, 90) for _ in range(12)]
    gauge = random.randint(78, 88)
    return jsonify({'code': 200, 'data': {
        'gauge_value': gauge,
        'gauge_label': '非常安全' if gauge >= 75 else '一般',
        'trend_days': days,
        'trend_data': trend,
        'list': [
            {'name': '消防水压', 'num': 234, 'pre': '50%', 'percentage': 96},
            {'name': '告警响应率', 'num': 153, 'pre': '40%', 'percentage': 86},
            {'name': '安全出口', 'num': 103, 'pre': '35%', 'percentage': 80},
            {'name': '日常巡检与维护', 'num': 63, 'pre': '30%', 'percentage': 76},
            {'name': '报警主机在线数', 'num': 3, 'pre': '25%', 'percentage': 72},
            {'name': '设备电流稳定率', 'num': 30, 'pre': '20%', 'percentage': 70},
        ]
    }})

@app.route('/api/dashboard/cooling-chart', methods=['GET'])
def dashboard_cooling_chart():
    """综合态势：制冷/制热趋势（mouth.vue）"""
    import random
    days = [(datetime.now() - timedelta(days=11 - i)).strftime('%m-%d') for i in range(12)]
    cooling = [random.randint(26, 80) for _ in range(12)]
    heating = [random.randint(20, 60) for _ in range(12)]
    return jsonify({'code': 200, 'data': {
        'days': days, 'cooling': cooling, 'heating': heating
    }})

# ========== 设备运维指标 API ==========
@app.route('/api/devices/priority', methods=['GET'])
def devices_priority():
    """AI故障优先级处理 — 从异常诊断（device事件）取数，按优先级排序"""
    _refresh_event_times()
    _enforce_event_deadlines()
    events = EventLog.query.filter_by(
        category='device', status='pending'
    ).order_by(EventLog.priority.desc()).all()

    result = []
    for i, e in enumerate(events):
        # 优先级文案
        if e.priority >= 75:
            level_name = '紧急'
        elif e.priority >= 50:
            level_name = '重要'
        else:
            level_name = '一般'
        # 建议处理时限
        if e.priority >= 75:
            deadline = '30分钟内'
        elif e.priority >= 50:
            deadline = '2小时内'
        else:
            deadline = '当日处理'
        result.append({
            'rank': i + 1,
            'device_id': e.device_id,
            'device': e.device or e.device_id,
            'position': e.position or f'{e.floor}F',
            'floor': e.floor,
            'description': e.description,
            'level': e.lv,
            'level_name': level_name,
            'priority': e.priority,
            'recommendation': e.recommendation,
            'deadline': deadline,
            'timestamp': e.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        })
    return jsonify({'code': 200, 'message': 'success', 'data': result})

@app.route('/api/devices/ops-metrics', methods=['GET'])
def devices_ops_metrics():
    """设备管理：运维效率6指标 + 设备维保统计3项"""
    total = Device.query.count()
    fault = Device.query.filter_by(status='fault').count()
    health = round((total - fault) / total * 100, 1) if total > 0 else 0
    return jsonify({'code': 200, 'data': {
        'ops_efficiency': [
            {'val': '3', 'unit': '秒', 'label': '异常发现'},
            {'val': '98.5', 'unit': '%', 'label': '运维响应率'},
            {'val': '99.2', 'unit': '%', 'label': '运营连续性'},
            {'val': '1.8', 'unit': 'h', 'label': '平均修复时间'},
            {'val': '89', 'unit': '%', 'label': '工单完成率'},
            {'val': '76', 'unit': '%', 'label': '月度巡检进度'},
        ],
        'maintenance': [
            {'label': '本周保养完成', 'val': 12, 'unit': '/15 项'},
            {'label': '逾期未处理', 'val': fault, 'unit': '项'},
            {'label': '设备综合健康度', 'val': health, 'unit': '%'},
        ]
    }})

# ========== 能耗趋势 API ==========
@app.route('/api/energy/month-trend', methods=['GET'])
def energy_month_trend():
    """能耗计量：本月用水/耗电趋势（month.vue）— 只显示到今天"""
    import random
    today = datetime.now().day
    days = [str(i) for i in range(1, today + 1)]
    # 月初到月底能耗递增（业务量增长），周末偏低
    water, power = [], []
    for d in range(1, today + 1):
        wd = (datetime.now().replace(day=d)).weekday()
        weekend_factor = 0.7 if wd >= 5 else 1.0
        water.append(round(random.randint(20, 50) * weekend_factor + d * 0.4, 1))
        power.append(round(random.randint(25, 55) * weekend_factor + d * 0.5, 1))
    return jsonify({'code': 200, 'data': {
        'days': days, 'water': water, 'power': power
    }})

@app.route('/api/energy/today-trend', methods=['GET'])
def energy_today_trend():
    """能耗计量：今日用水/用电趋势（today.vue）— 只显示到当前小时"""
    import random
    current_hour = datetime.now().hour
    hours = [f'{h:02d}:00' for h in range(8, min(current_hour + 1, 24))]
    water, power = [], []
    for h in range(8, min(current_hour + 1, 24)):
        # 早晚高峰能耗高，午间平稳，夜间低
        if h < 10:      # 早高峰
            factor = random.uniform(1.2, 1.6)
        elif 10 <= h < 16:  # 平峰
            factor = random.uniform(0.8, 1.1)
        elif 16 <= h < 19:  # 晚高峰
            factor = random.uniform(1.1, 1.5)
        else:           # 晚间
            factor = random.uniform(0.5, 0.8)
        water.append(int(3000 * factor))
        power.append(int(4000 * factor))
    return jsonify({'code': 200, 'data': {
        'hours': hours, 'water': water, 'power': power
    }})

# ========== 安防 API ==========
@app.route('/api/security/fire-alarms', methods=['GET'])
def security_fire_alarms():
    """安防：消防报警表格（BuildingAssess.vue）— 只取消防类事件，按时间倒序"""
    from models import EventLog as EL
    _refresh_event_times()
    _enforce_event_deadlines()
    alarms = EL.query.filter(
        EL.category == 'fire'
    ).order_by(EL.timestamp.desc()).limit(11).all()

    # 设备类型由事件的 device 字段决定
    result = []
    for i, a in enumerate(alarms):
        result.append({
            'uid': i + 1,
            'type': a.device or '烟雾传感器',
            'id': a.device_id or f'FIRE-{i+1:02d}',
            'describe': a.description,
            'time': a.timestamp.strftime('%H:%M:%S'),
            'statusCode': {'pending': 0, 'handled': 1, 'timeout': 2}.get(a.status, 0),
            'point': [17.67 + i, 104.37, 32.0],
        })
    return jsonify({'code': 200, 'data': result})

@app.route('/api/security/energy-assess', methods=['GET'])
def security_energy_assess():
    """安防：停车楼能耗评估（BuildingAssess(1).vue）"""
    times = ['00:00', '04:00', '08:00', '12:00', '16:00', '20:00', '24:00']
    energy = [25, 40, 55, 70, 75, 100, 40]
    temp = [0, 20, 10, 25, 30, 37, 13]
    return jsonify({'code': 200, 'data': {
        'times': times, 'energy': energy, 'temperature': temp
    }})

@app.route('/api/security/lamp-efficiency', methods=['GET'])
def security_lamp_efficiency():
    """安防：日光灯管发光效率（Personnel(1).vue）"""
    import random
    hours = [f'{h:02d}:00' for h in range(6, 22)]
    t8 = [random.randint(120, 634) for _ in range(16)]
    t4 = [random.randint(0, 390) for _ in range(16)]
    return jsonify({'code': 200, 'data': {
        'hours': hours, 't8': t8, 't4': t4
    }})

# ========== 空间资源 API ==========
@app.route('/api/space/device-online', methods=['GET'])
def space_device_online():
    """空间资源：设备在线数进度条（space.vue）"""
    return jsonify({'code': 200, 'data': [
        {'name': '烟雾传感器', 'percentage': 92.8, 'num': 832.1},
        {'name': '湿度传感器', 'percentage': 89.7, 'num': 550},
        {'name': '可燃气体探头', 'percentage': 82.9, 'num': 350.3},
        {'name': '消防栓', 'percentage': 70, 'num': 80},
        {'name': '报警装置', 'percentage': 65, 'num': 40},
    ]})

@app.route('/api/space/floors', methods=['GET'])
def space_floors():
    """空间资源：楼层选择列表（Control(1).vue）"""
    left = [-3, -1, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32]
    right = [-2, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31]
    return jsonify({'code': 200, 'data': {
        'left_floors': left, 'right_floors': right,
        'default_active': 22,
        'control_list': ['视频监控', '空调设备', '车位区管理', '工位管理', '人脸识别设备']
    }})

# ========== 车位管理：楼层资源状态 + 智能调度建议 ==========
@app.route('/api/floors/status', methods=['GET'])
def floors_status():
    """各楼层资源状态：占用率 + 状态标记（空闲/较满/饱和）— 按DB实际状态统计"""
    result = []
    for f in range(1, 8):
        tot = ParkingSpot.query.filter_by(floor=f).count()
        occ = ParkingSpot.query.filter_by(floor=f, status='occupied').count()
        vio = ParkingSpot.query.filter_by(floor=f, status='violation').count()
        flt = ParkingSpot.query.filter_by(floor=f, status='fault').count()
        cars = occ + vio  # 违规车也算占位
        rate = round(cars / tot * 100, 1) if tot else 0
        state = '空闲' if rate < 50 else ('较满' if rate < 85 else '饱和')
        result.append({
            'floor': f,
            'floor_label': f'F{f}',
            'total': tot,
            'occupied': cars,
            'available': tot - cars - flt,
            'occupancy_rate': rate,
            'state': state,   # 空闲 / 较满 / 饱和
        })
    return jsonify({'code': 200, 'message': 'success', 'data': result})

@app.route('/api/floors/advice', methods=['GET'])
def floors_advice():
    """车位管理：智能调度建议 — 基于楼层状态生成"""
    return jsonify({'code': 200, 'message': 'success',
                    'data': dispatch_advice()})

@app.route('/api/energy/advice', methods=['GET'])
def energy_advice_api():
    """能耗管理：AI智能调度建议 — 基于功率结构生成"""
    return jsonify({'code': 200, 'message': 'success',
                    'data': energy_advice()})

@app.route('/api/energy/assessment', methods=['GET'])
def energy_assessment():
    """能耗管理：综合研判 — 结合实时功率/预测/对标生成研判结论"""
    totals = energy_totals()
    current = totals['total_energy_kwh']
    current_power = totals['current_total_kw']
    peak = totals['peak_power_kw']
    avg = totals['average_power_kw']

    # 分类占比分析
    categories = totals['categories']
    charging = next(c for c in categories if c['category'] == 'charging')
    charging_ratio = round(charging['power_kw'] / current_power * 100, 1) if current_power else 0

    # 负荷预测趋势
    now = datetime.now()
    f_now = max(time_factor(), 0.2)
    f_next = ConsistencyModel.time_factor(now + timedelta(hours=1))
    trend = '上升' if f_next > f_now * 1.05 else ('下降' if f_next < f_now * 0.95 else '平稳')

    # 研判结论列表
    conclusions = []
    # 1. 总体研判
    if current_power > avg * 1.3:
        conclusions.append({'level': 'warning',
                            'text': f'当前总功率{current_power}kW，超日均水平30%以上，建议核查高耗能设备'})
    else:
        conclusions.append({'level': 'normal',
                            'text': f'当前总功率{current_power}kW，处于日均水平（{avg}kW）正常区间'})
    # 2. 充电占比研判
    if charging_ratio > 55:
        conclusions.append({'level': 'warning',
                            'text': f'充电系统占比{charging_ratio}%，占比偏高，建议引导错峰充电'})
    else:
        conclusions.append({'level': 'normal',
                            'text': f'充电系统占比{charging_ratio}%，结构合理'})
    # 3. 负荷趋势研判
    if trend == '上升':
        conclusions.append({'level': 'info',
                            'text': '未来1小时负荷呈上升趋势，建议提前增开通风设备'})
    elif trend == '下降':
        conclusions.append({'level': 'info',
                            'text': '未来1小时负荷下降，可安排设备轮换检修'})
    else:
        conclusions.append({'level': 'info', 'text': '未来1小时负荷平稳，维持当前运行策略'})
    # 4. 能耗对标研判
    if current > 5600 * 0.9:
        conclusions.append({'level': 'warning',
                            'text': '今日累计能耗接近阈值，注意控制非必要用电'})
    else:
        conclusions.append({'level': 'normal',
                            'text': '今日累计能耗远低于阈值，节能空间充足'})

    has_warning = any(c['level'] == 'warning' for c in conclusions)
    if has_warning:
        summary = '部分系统负荷偏高，建议按研判结论执行调整，重点关注功率与充电占比。'
    elif charging_ratio > 50:
        summary = '能耗总体正常，充电系统为主要负荷，建议关注高峰时段错峰用电。'
    else:
        summary = '能耗总体良好，各系统运行平稳，暂无异常。'
    return jsonify({'code': 200, 'message': 'success', 'data': {
        'overall_level': 'warning' if has_warning else 'normal',
        'summary': summary,
        'conclusions': conclusions,
        'metrics': {
            'current_power_kw': current_power,
            'average_power_kw': avg,
            'peak_power_kw': peak,
            'today_energy_kwh': current,
            'charging_ratio_pct': charging_ratio,
            'trend': trend,
        }
    }})

# ========== P1: 综合态势停车汇总 ==========
@app.route('/api/parking/summary', methods=['GET'])
def parking_summary():
    """综合态势停车汇总 — 共享一致性模型"""
    occupied, total, rate = overall_occupancy()
    floors = []
    for f in range(1, 8):
        occ, tot, frate, state = floor_occupancy(f)
        floors.append({'floor': f'F{f}', 'total': tot, 'occupied': occ,
                       'available': tot - occ, 'state': state})
    entries, exits = ConsistencyModel.hourly_traffic()
    return jsonify({'code': 200, 'message': 'success', 'data': {
        'total_spaces': total,
        'occupied_spaces': occupied,
        'available_spaces': total - occupied,
        'occupancy_rate': rate,
        'average_parking_hours': round(2.5 + time_factor() * 1.5, 1),
        'average_traffic_per_hour': entries,
        'today_entries': entries * max(datetime.now().hour, 1),
        'today_exits': exits * max(datetime.now().hour, 1),
        'floors': floors
    }})

# ========== P1: 当日车辆出入统计 ==========
@app.route('/api/traffic/hourly', methods=['GET'])
def traffic_hourly():
    """当日车辆出入小时统计（0-23时共24条）— 共享时段模型
    已过小时：entries/exits 为实际值
    未来小时：entries/exits=null，predicted_entries/predicted_exits 为预测值"""
    now = datetime.now()
    result = []
    for h in range(24):
        t = now.replace(hour=h, minute=0)
        entries, exits = ConsistencyModel.hourly_traffic(t)
        is_future = h > now.hour
        result.append({
            'hour': h,
            'entries': None if is_future else entries,
            'exits': None if is_future else exits,
            'predicted_entries': entries,
            'predicted_exits': exits,
            'is_future': is_future,
        })
    return jsonify({'code': 200, 'message': 'success', 'data': result})

# ========== P1: 能耗管理总览 ==========
@app.route('/api/energy/overview', methods=['GET'])
def energy_overview():
    """能耗管理总览 — 共享一致性模型（单次快照，内部完全一致）"""
    totals = energy_totals()
    return jsonify({'code': 200, 'message': 'success', 'data': {
        'peak_power_kw': totals['peak_power_kw'],
        'peak_time': datetime.now().replace(hour=14, minute=36).strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'average_power_kw': totals['average_power_kw'],
        'change_vs_yesterday_percent': round((time_factor() - 1.0) * 5, 1),
        'total_energy_kwh': totals['total_energy_kwh'],
        'current_total_kw': totals['current_total_kw'],
        'floors': totals['floors'],
        'device_categories': totals['categories']
    }})

# ========== P1: 未来8小时负荷预测 ==========
@app.route('/api/energy/forecast', methods=['GET'])
def energy_forecast():
    """未来8小时能耗负荷预测 — 基于当前总功率，随时段平稳波动（不放大失真）"""
    now = datetime.now()
    totals = energy_totals()
    base = totals['current_total_kw']
    points = []
    for i in range(8):
        t = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=i + 1)
        f_future = ConsistencyModel.time_factor(t)
        # 预测功率 = 当前功率 × (0.55 + 0.45×未来时段系数)
        # 夜间≈0.66倍，高峰≈1.23倍，范围平稳不失控
        points.append({
            'time': t.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
            'predicted_power_kw': round(base * (0.55 + 0.45 * f_future), 1)
        })
    trend = 'rising' if points[3]['predicted_power_kw'] > points[0]['predicted_power_kw'] else 'falling'
    return jsonify({'code': 200, 'message': 'success', 'data': {
        'model': 'LSTM-AutoEncoder',
        'generated_at': now.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'confidence': 94.8,
        'trend': trend,
        'safe_load_kw': 450,
        'points': points
    }})

# ========== P1: 能耗对标分析 ==========
@app.route('/api/energy/benchmark', methods=['GET'])
def energy_benchmark():
    """能耗对标分析 — 上周值按周变一次，上月值按月变一次，今日实时计算"""
    import random as _random
    totals = energy_totals()
    current = round(totals['total_energy_kwh'])
    now = datetime.now()

    # 上周同期：以(年, 周数)为种子，与本期同一时刻同口径（本期×周系数）
    iso = now.isocalendar()
    week_seed = int(f'{iso[0]}{iso[1]:02d}')
    rnd_w = _random.Random(week_seed)
    # 与本期接近，浮动 ±10% 以内
    last_week = round(current * rnd_w.uniform(0.90, 1.10))

    # 上月同期：以(年, 月)为种子，与本期同口径（本期×月系数）
    month_seed = int(f'{now.year}{now.month:02d}')
    rnd_m = _random.Random(month_seed)
    last_month = round(current * rnd_m.uniform(0.85, 1.15))

    # 阈值固定
    threshold = 5600
    is_over = current > threshold

    # 建筑面积约 4500 m²，单车能耗按日均车流量估算
    area = 4500
    entries, exits = ConsistencyModel.hourly_traffic()
    car_count = max(entries + exits, 50)

    assessment = ('总能耗处于合理区间，与上月同期基本持平，'
                  '主要得益于错峰充电策略执行到位。'
                  if not is_over else '能耗已超阈值，建议排查高耗能设备运行状态。')
    return jsonify({'code': 200, 'message': 'success', 'data': {
        'current_energy_kwh': current,
        'last_week_energy_kwh': last_week,
        'last_month_energy_kwh': last_month,
        'threshold_kwh': threshold,
        'current_per_area_kwh_m2': round(current / area, 2),
        'last_week_per_area_kwh_m2': round(last_week / area, 2),
        'last_month_per_area_kwh_m2': round(last_month / area, 2),
        'current_per_car_kwh': round(current / car_count, 2),
        'last_week_per_car_kwh': round(last_week / car_count, 2),
        'last_month_per_car_kwh': round(last_month / car_count, 2),
        'is_over_threshold': is_over,
        'assessment': assessment
    }})

# ========== P1: 运维维保综合统计 ==========
@app.route('/api/maintenance/summary', methods=['GET'])
def maintenance_summary():
    """运维维保综合统计"""
    total_orders = 28
    closed = 27
    return jsonify({'code': 200, 'message': 'success', 'data': {
        'average_repair_hours': 1.8,
        'work_order_closure_rate': round(closed / total_orders * 100, 1),
        'operation_continuity_rate': 99.2,
        'total_work_orders': total_orders,
        'closed_work_orders': closed
    }})

# ========== P1: 消防传感器实时数据 ==========
@app.route('/api/fire/sensors/latest', methods=['GET'])
def fire_sensors_latest():
    """消防传感器实时数据（F1-F7）— 符合物理逻辑：
    - 楼层越高温度越高（每层+0.3~0.5°C），夜间整体降温
    - CO2随楼层人流/车流变化，CO/烟感车库正常范围，不超阈值
    - 相邻层数据接近，仅个别层（对应报警事件）偏高但不离谱"""
    import random as _random
    now = datetime.now()
    hour = now.hour
    # 时间种子：10分钟内稳定，保证楼层间关系稳定
    seed = int(ConsistencyModel.time_seed().timestamp())
    result = []
    for i, f in enumerate(['F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7']):
        rnd = _random.Random(seed + i * 131)
        floor_num = i + 1
        # 细微波动 ±2%
        jitter = _random.uniform(0.98, 1.02)

        # === 温度：随楼层线性上升，夜间整体降温 ===
        # 白天基准：F1=26.5°C，每层+0.4°C；夜间(22-6点)整体-3°C
        day_base = 26.5 + (floor_num - 1) * 0.4
        if hour >= 22 or hour < 6:
            day_base -= 3.0
        elif hour < 10:  # 早晨升温中
            day_base -= 1.0
        # F3 有温度偏高事件：略高于其他层但不超过1.5°C
        if floor_num == 3:
            day_base += 1.2
        temp = round(day_base * jitter, 1)

        # === 烟感：正常0.03-0.07 mg/m³；F5报警层刚过阈值0.15 ===
        if floor_num == 5:
            smoke = round(_random.uniform(0.151, 0.158), 3)  # 刚触发报警线
        else:
            smoke = round(rnd.uniform(0.03, 0.07) * jitter, 3)

        # === CO：车库正常 0.8-2.0 ppm，随车流波动 ===
        # 白天高峰车多CO略高，夜间低
        if 7 <= hour <= 10 or 17 <= hour <= 20:
            co_base = 1.6
        elif hour >= 22 or hour < 6:
            co_base = 0.9
        else:
            co_base = 1.2
        co = round(co_base * jitter, 2)

        # === CO2：正常380-550ppm；F1入口人流多略高 ===
        co2_base = 480 if floor_num == 1 else 420 + floor_num * 10
        co2 = round(co2_base * jitter)

        result.append({
            'sensor_id': f'ENV-{f}-01',
            'floor': f,
            'smoke_mg_m3': smoke,
            'temperature_c': temp,
            'co_ppm': co,
            'co2_ppm': co2,
            'recorded_at': now.strftime('%Y-%m-%dT%H:%M:%S+08:00')
        })
    return jsonify({'code': 200, 'message': 'success', 'data': result})

# ========== P1: 消防隐患点位 ==========
@app.route('/api/fire/risks', methods=['GET'])
def fire_risks():
    """消防隐患点位"""
    return jsonify({'code': 200, 'message': 'success', 'data': [
        {'id': 'TP-301', 'position': 'F3东通道', 'device_id': 'TP-301',
         'device_name': '温度传感器 TP-301', 'reason': '30天内17次高于32°C',
         'risk_level': 'high', 'occurrence_count_30d': 17, 'status': 'open',
         'generated_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00')},
        {'id': 'SK-105', 'position': 'F5车位区', 'device_id': 'SK-105',
         'device_name': '烟雾传感器 SK-105', 'reason': '灵敏度漂移，需校准',
         'risk_level': 'medium', 'occurrence_count_30d': 6, 'status': 'open',
         'generated_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00')},
    ]})

# ========== P1: 消防设备检测 ==========
@app.route('/api/fire/inspections/latest', methods=['GET'])
def fire_inspections_latest():
    """应急通道和消防设备检测（4类）"""
    now = datetime.now()
    return jsonify({'code': 200, 'message': 'success', 'data': [
        {'id': 1, 'check_type': 'broadcast', 'check_name': '消防广播测试',
         'qualified': True, 'reason': None,
         'checked_at': (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S+08:00')},
        {'id': 2, 'check_type': 'fire_passage', 'check_name': '消防通道检测',
         'qualified': False, 'reason': 'F3东通道有临时物料占道，清理后需复检。',
         'checked_at': (now - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%S+08:00')},
        {'id': 3, 'check_type': 'emergency_light', 'check_name': '消防应急照明测试',
         'qualified': True, 'reason': None,
         'checked_at': (now - timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%S+08:00')},
        {'id': 4, 'check_type': 'extinguisher', 'check_name': '灭火器检测/更新',
         'qualified': True, 'reason': None,
         'checked_at': (now - timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%S+08:00')},
    ]})

# ========== P1: 消防摄像头 ==========
@app.route('/api/fire/cameras', methods=['GET'])
def fire_cameras():
    """消防摄像头列表"""
    return jsonify({'code': 200, 'message': 'success', 'data': [
        {'id': 'CAM-F1-01', 'name': 'F1大厅', 'floor': 'F1',
         'location': 'F1大厅东侧', 'stream_url': 'http://10.102.77.149:5000/mock/cam-f1.m3u8',
         'status': 'online'},
        {'id': 'CAM-F3-01', 'name': 'F3东通道', 'floor': 'F3',
         'location': 'F3东侧通道', 'stream_url': 'http://10.102.77.149:5000/mock/cam-f3.m3u8',
         'status': 'online'},
        {'id': 'CAM-F5-01', 'name': 'F5西侧', 'floor': 'F5',
         'location': 'F5西侧车位区', 'stream_url': 'http://10.102.77.149:5000/mock/cam-f5.m3u8',
         'status': 'online'},
        {'id': 'CAM-F7-01', 'name': 'F7顶层出口', 'floor': 'F7',
         'location': 'F7顶层出口', 'stream_url': 'http://10.102.77.149:5000/mock/cam-f7.m3u8',
         'status': 'offline'},
    ]})

# ========== P1: 消防应急处置总览 ==========
@app.route('/api/fire/emergency', methods=['GET'])
def fire_emergency():
    """消防应急处置总览：紧急事件 + 风险点位 + 传感器异常 + 处置建议"""
    now = datetime.now()

    # 1. 紧急事件：消防类事件 + 高优先级设备事件，优先级降序
    emerg_events = EventLog.query.filter(db.or_(
        EventLog.category == 'fire',
        db.and_(EventLog.category == 'device', EventLog.priority >= 75)
    )).order_by(EventLog.priority.desc(), EventLog.timestamp.desc()).limit(20).all()
    emergencies = [e.to_dict() for e in emerg_events]

    # 2. 风险点位：未处理的消防高/中危事件，按设备去重，字段对齐 /api/fire/risks
    risk_events = EventLog.query.filter(
        EventLog.category == 'fire',
        EventLog.lv.in_(['high', 'mid']),
        EventLog.status == 'pending'
    ).all()
    seen_devices, risk_points = set(), []
    for e in risk_events:
        key = e.device_id or e.device
        if key in seen_devices:
            continue
        seen_devices.add(key)
        risk_points.append({
            'id': f'EV-{e.id}',
            'position': e.position,
            'device_id': e.device_id,
            'device_name': e.device,
            'reason': e.description,
            'risk_level': e.lv,
            'occurrence_count_30d': EventLog.query.filter(
                EventLog.category == 'fire',
                EventLog.device_id == e.device_id,
                EventLog.timestamp >= now - timedelta(days=30)).count(),
            'status': e.status,
            'generated_at': e.timestamp.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        })

    # 3. 传感器异常：消防相关设备（照明/风机/空调）故障或离线
    sensor_anomalies = [{
        'floor': d.floor,
        'device_code': d.device_code,
        'device_type': d.device_type,
        'status': d.status,
        'location': d.location,
        'severity': 'high' if d.status == 'fault' else 'medium',
    } for d in Device.query.filter(
        Device.device_type.in_(['light', 'fan', 'ac']),
        Device.status.in_(['fault', 'offline'])
    ).limit(10).all()]

    # 4. 处置建议：未处理事件的 recommendation 按优先级去重排序
    advice, seen_advice = [], set()
    for e in sorted(emerg_events, key=lambda x: -(x.priority or 0)):
        if e.status == 'pending' and e.recommendation and e.recommendation not in seen_advice:
            seen_advice.add(e.recommendation)
            advice.append({
                'priority': e.priority,
                'target': e.device or e.position,
                'recommendation': e.recommendation,
            })

    return jsonify({'code': 200, 'message': 'success', 'data': {
        'summary': {
            'total_emergencies': len(emergencies),
            'pending_count': sum(1 for e in emergencies if e['status'] == 'pending'),
            'critical_count': sum(1 for e in emergencies if (e['priority'] or 0) >= 90),
            'updated_at': now.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        },
        'emergencies': emergencies,
        'risk_points': risk_points,
        'sensor_anomalies': sensor_anomalies,
        'disposal_advice': advice,
    }})

# ========== 前端页面托管 ==========
@app.route('/')
def serve_index():
    """托管前端首页"""
    return send_file(os.path.join(DIST_DIR, 'index.html'))

@app.route('/<path:filename>')
def serve_static(filename):
    """托管前端静态资源（JS/CSS/图片等）"""
    file_path = os.path.join(DIST_DIR, filename)
    if os.path.isfile(file_path):
        return send_from_directory(DIST_DIR, filename)
    # 非API路径返回前端首页（SPA路由）
    if not filename.startswith('api/'):
        return send_file(os.path.join(DIST_DIR, 'index.html'))
    return jsonify({'code': 404, 'msg': 'Not found'}), 404

@app.route('/flask/<path:subpath>')
def proxy_flask(subpath):
    """兼容前端开发代理 /flask → 重定向到后端"""
    return send_from_directory(DIST_DIR, subpath) if os.path.isfile(
        os.path.join(DIST_DIR, subpath)) else send_file(os.path.join(DIST_DIR, 'index.html'))

# ========== 启动 ==========
if __name__ == '__main__':
    # 建表统一走 flask db upgrade（见 docs/DEPLOY_RENDER.md），此处不再 create_all
    port = int(os.getenv('PORT', '5000'))
    app.run(debug=(APP_ENV != 'production'), host='0.0.0.0', port=port)