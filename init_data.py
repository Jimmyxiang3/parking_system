# -*- coding: utf-8 -*-
"""初始化数据库数据 — 全武汉车牌 + 表间数据关联一致

可重复执行：已有数据时自动跳过填充；--force 清空后重新填充。
建表统一走 Flask-Migrate，表缺失时自动补执行迁移。
"""
import argparse
from app import app, db
from models import ParkingSpot, Vehicle, ParkingRecord, EventLog, Device, Zone, EnergyRecord
from datetime import datetime, timedelta
import random


def _ensure_tables():
    """表不存在时程序化执行迁移（等价 flask db upgrade）"""
    from sqlalchemy import inspect
    if not inspect(db.engine).has_table('vehicles'):
        from flask_migrate import upgrade
        upgrade()


def seed(force=False):
    with app.app_context():
        _ensure_tables()
        if not force and (ParkingSpot.query.count() > 0 or Vehicle.query.count() > 0):
            print('检测到已有数据，跳过填充（--force 可清空后重建）')
            return
        if force:
            db.drop_all()
            from flask_migrate import upgrade
            upgrade()
        now = datetime.now()

        # ====== 车牌池：100%武汉（鄂A燃油 + 鄂WD新能源绿牌）======
        # 鄂A 格式：鄂A·XXXXX（字母+数字），鄂WD 格式：鄂WD·XXXXX（纯数字）
        wuhan_plates = set()
        letters = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
        while len(wuhan_plates) < 60:
            if random.random() < 0.25:  # 25%新能源绿牌
                p = f'鄂WD{random.randint(10000, 99999)}'
            else:  # 75%燃油蓝牌
                p = f'鄂A{random.choice(letters)}{random.randint(10000, 99999)}'
            wuhan_plates.add(p)
        plates_pool = list(wuhan_plates)

        # ====== 车位：418个 ======
        # 每层车位数量：F1=20, F2=42, F3=69, F4=67, F5=74, F6=74, F7=74，共420
        floor_spot_counts = {1: 20, 2: 42, 3: 69, 4: 67, 5: 74, 6: 74, 7: 74}
        for floor, spot_count in floor_spot_counts.items():
            ftype = {1:'special', 2:'normal', 3:'charging', 4:'charging',
                     5:'normal', 6:'normal', 7:'normal'}[floor]
            n = 0
            for zone in ['A', 'B', 'C']:
                # 该区车位数量（余数分给A区）
                zone_cnt = spot_count // 3
                if zone == 'A':
                    zone_cnt += spot_count % 3
                for k in range(1, zone_cnt + 1):
                    n += 1
                    db.session.add(ParkingSpot(
                        spot_code=f'{floor}F-{zone}-{k:03d}', floor=floor, zone=zone,
                        spot_type=ftype, spot_size=random.choice(['small','medium','large']),
                        is_charging_spot=(ftype=='charging'), is_special=(ftype=='special'),
                        is_emergency=(floor==1 and k<=5), status='idle'))
        db.session.commit()
        print(f'车位: {ParkingSpot.query.count()}')

        # ====== 占用30个车位：每车关联车位+停车记录+车辆 ======
        all_spots = ParkingSpot.query.all()
        random.shuffle(all_spots)
        occupied_vehicles = []  # 记录场内车辆信息用于事件关联
        for i in range(30):
            s = all_spots[i]
            p = plates_pool[i]
            entry = now - timedelta(hours=random.randint(1, 10))
            s.status = 'occupied'
            s.last_updated = entry
            v = Vehicle(
                plate_number=p,
                vehicle_type='electric' if s.is_charging_spot else
                              ('special' if s.is_special else 'normal'),
                vehicle_size=s.spot_size,
                is_electric=s.is_charging_spot,
                entry_time=entry,
                assigned_floor=s.floor,
                assigned_zone=s.zone,
                user_type=random.choice(['long_term']*4 + ['temp']),
                status='inside'
            )
            db.session.add(v)
            db.session.flush()
            db.session.add(ParkingRecord(
                vehicle_id=v.id, plate_number=p, spot_id=s.id,
                spot_code=s.spot_code, entry_time=entry,
                floor=s.floor, zone=s.zone, status='parking'))
            occupied_vehicles.append({'plate': p, 'spot': s, 'vehicle': v})
        db.session.commit()
        print(f'占用: {ParkingSpot.query.filter_by(status="occupied").count()} / {ParkingSpot.query.count()}')

        # ====== 设备 ======
        # 充电桩82个（3F/4F）
        for i in range(1, 83):
            floor = 3 if i <= 41 else 4
            db.session.add(Device(device_code=f'CHG-{i:03d}', device_type='charger',
                floor=floor, location=f'{floor}F-充电区',
                status=random.choice(['online']*48 + ['idle', 'fault']),
                power_w=60000 if i % 5 == 0 else 7000, is_on=True,
                last_heartbeat=now-timedelta(minutes=random.randint(0, 30))))

        # 照明418个
        for i in range(1, 419):
            floor = random.randint(1, 7)
            db.session.add(Device(device_code=f'LIGHT-{i:03d}', device_type='light',
                floor=floor, location=f'{floor}F-照明',
                status=random.choice(['online']*96 + ['fault']),
                power_w=random.randint(60, 100),
                is_on=random.random() > 0.2,
                last_heartbeat=now-timedelta(minutes=random.randint(0, 30))))

        # 门禁1个
        db.session.add(Device(device_code='DOOR-001', device_type='door',
            floor=1, location='F1-主入口', status='online', power_w=50, is_on=True,
            last_heartbeat=now-timedelta(minutes=5)))

        # 地锁418个
        for i in range(1, 419):
            floor = random.randint(1, 7)
            db.session.add(Device(device_code=f'LOCK-{i:03d}', device_type='gate',
                floor=floor, location=f'{floor}F-车位地锁',
                status=random.choice(['online']*47 + ['fault', 'offline']),
                open_count=random.randint(100, 20000),
                power_w=random.randint(100, 300), is_on=True,
                last_heartbeat=now-timedelta(minutes=random.randint(0, 30))))

        # 空调2个
        for i, f in [(1, 1), (2, 2)]:
            db.session.add(Device(device_code=f'AC-{i:03d}', device_type='ac',
                floor=f, location=f'{f}F-空调', status='online',
                power_w=random.randint(3000, 5000), is_on=True,
                last_heartbeat=now-timedelta(minutes=random.randint(0, 15))))

        # 风机14个（分布在1-2层）
        for i in range(1, 15):
            db.session.add(Device(device_code=f'FAN-{i:02d}', device_type='fan',
                floor=random.randint(1, 2), location=f'{random.randint(1,2)}F-排风',
                status=random.choice(['online']*12 + ['offline']),
                power_w=random.randint(500, 1500),
                is_on=random.random() > 0.3,
                last_heartbeat=now-timedelta(minutes=random.randint(0, 30))))

        # 电梯2个
        for code, name in [('ELEV-A', 'A梯-客梯'), ('ELEV-B', 'B梯-货梯')]:
            db.session.add(Device(device_code=code, device_type='elevator',
                floor=1, location=name, status='online',
                power_w=random.randint(8000, 15000), is_on=True,
                last_heartbeat=now-timedelta(minutes=10)))

        # 监控72个
        for i in range(1, 73):
            db.session.add(Device(device_code=f'CAM-{i:03d}', device_type='camera',
                floor=random.randint(1, 7), location=f'{random.randint(1,7)}F-监控点',
                status=random.choice(['online']*48 + ['offline']),
                power_w=random.randint(5, 15), is_on=True,
                last_heartbeat=now-timedelta(minutes=random.randint(0, 30))))
        db.session.commit()

        # ====== 区域：数量与实际车位状态一致 ======
        for f in range(1, 8):
            for z in ['A', 'B', 'C']:
                t = ParkingSpot.query.filter_by(floor=f, zone=z).count()
                o = ParkingSpot.query.filter_by(floor=f, zone=z, status='occupied').count()
                if t > 0:
                    db.session.add(Zone(floor=f, zone_code=z, name=f'{f}F-{z}区',
                                        total_spots=t, occupied_count=o))

        # ====== 能耗记录：与充电设备关联 ======
        # 每个充电桩产生一条近期能耗记录
        chargers = Device.query.filter_by(device_type='charger').all()
        for c in random.sample(chargers, 24):  # 取24条做24h曲线
            h = random.randint(0, 23)
            # 白天用电多，夜间少
            day_factor = 1.5 if 8 <= h <= 20 else 0.4
            usage = round(c.power_w / 1000 * random.uniform(0.5, 1.2) * day_factor, 2)
            db.session.add(EnergyRecord(
                floor=c.floor, power_usage=usage,
                record_time=now - timedelta(hours=h)))

        # ====== 事件：车牌必须是场内真实车辆（含文档扩展字段）======
        # 设备故障（无车牌，引用真实故障设备，含地下B1/B2设备）
        # 按优先级分时限：紧急30分钟/重要2小时/一般6小时，未处理必须时限内
        device_pri_levels = [92, 68, 45, 30]
        fault_devices = Device.query.filter(Device.status.in_(['fault', 'offline'])).limit(4).all()
        for idx, d in enumerate(fault_devices):
            pri = device_pri_levels[min(idx, len(device_pri_levels) - 1)]
            lv = 'high' if pri >= 75 else ('mid' if pri >= 50 else 'low')
            deadline_min = 30 if pri >= 75 else (120 if pri >= 50 else 360)
            rec_map = {
                92: '照明回路异常可能导致区域停电，立即安排电工排查线路',
                68: '灯组故障影响照明质量，2小时内安排更换',
                45: '灯组故障影响照明质量，当日安排检修',
                30: '个别灯具老化，可结合例行巡检处理',
            }
            desc = f'{d.device_code} {d.location} 状态异常'
            # 未处理只留第1条（额度控制），其余已处理
            is_pending = idx < 1
            if is_pending:
                # 未处理：时限前段（80%内）
                max_age = max(int(deadline_min * 0.8) - 5, 5)
                age = random.randint(3, max_age)
            else:
                # 已处理：超过时限发生，且在时限内处理完
                age = random.randint(deadline_min + 10, max(now.hour * 60 + now.minute - 5, deadline_min + 15))
            ev = EventLog(
                event_type='warning' if d.status == 'fault' else 'info',
                floor=d.floor, plate_number=None, category='device',
                description=desc,
                device_id=d.device_code, device=d.device_code,
                position=d.location,
                lv=lv, priority=pri,
                recommendation=rec_map.get(pri, '建议安排运维人员现场检修'),
                status='pending' if is_pending else 'handled',
                timestamp=now - timedelta(minutes=age))
            if not is_pending:
                handled_after = random.randint(8, max(deadline_min - 3, 10))
                ev.handled_at = ev.timestamp + timedelta(minutes=handled_after)
                if ev.handled_at > now:
                    ev.handled_at = now - timedelta(minutes=1)
            db.session.add(ev)

        # 消防事件（消防设备报警，与车位无关）— 多样数据，仅1条待处理
        fire_event_templates = [
            # (类型, 设备编号, 设备名, 描述, 级别, 优先级, 建议, 状态)
            ('alarm', 'SD-105', '烟雾传感器', 'F5车位区烟雾浓度超标', 'high', 90, '立即派员核查F5车位区，必要时启动排烟', 'pending'),
            ('warning', 'TP-301', '温度传感器', 'F3东通道温度持续偏高', 'high', 75, '检查F3东通道散热及电气设备', 'handled'),
            ('warning', 'CG-204', '可燃气体探头', 'F2设备间可燃气体浓度异常', 'mid', 65, '排查F2设备间管道泄漏', 'handled'),
            ('alarm', 'FH-112', '消防栓', 'F4消防栓水压低于标准值', 'mid', 60, '检查F4消防供水管网压力', 'handled'),
            ('warning', 'HS-306', '湿度传感器', 'F3充电区湿度过高', 'low', 40, 'F3充电区加强通风除湿', 'handled'),
            ('info', 'SD-208', '烟雾传感器', 'F1入口烟雾探测器灵敏度测试', 'low', 20, '例行测试记录', 'handled'),
            ('info', 'FH-205', '消防栓', 'F2消防栓例行外观检查合格', 'low', 15, '记录存档', 'handled'),
            ('info', 'EX-107', '应急照明灯', 'F1应急照明电池续航测试通过', 'low', 15, '记录存档', 'handled'),
            ('info', 'ME-309', '灭火器', 'F3灭火器压力表正常，在有效期内', 'low', 10, '记录存档', 'handled'),
            ('info', 'SD-406', '烟雾传感器', 'F4烟雾传感器自检完成，灵敏度正常', 'low', 10, '记录存档', 'handled'),
            ('info', 'TP-512', '温度传感器', 'F5温度传感器校准完成', 'low', 10, '记录存档', 'handled'),
            ('info', 'CG-601', '可燃气体探头', 'F6可燃气体探头零点漂移校正', 'low', 10, '记录存档', 'handled'),
        ]
        for i, (t, did, dname, desc, lv, pri, rec, st) in enumerate(fire_event_templates):
            # 从描述中提取楼层，如 "F5车位区..." → 5
            import re as _re
            m = _re.search(r'F(\d)', desc)
            floor_num = int(m.group(1)) if m else 1
            # 处理时限：紧急30分钟/重要2小时/一般6小时
            deadline_min = 30 if pri >= 75 else (120 if pri >= 50 else 360)
            if st == 'pending':
                # 未处理事件：必须发生在时限之内（接近当前时间）
                age = random.randint(3, max(deadline_min - 5, 5))
            else:
                # 已处理事件：发生在更早（超过时限），且已及时处理
                age = random.randint(deadline_min + 10, max(now.hour * 60 + now.minute - 5, deadline_min + 30))
            ev = EventLog(
                event_type=t, floor=floor_num, plate_number=None, category='fire',
                description=desc, device_id=did, device=dname,
                position=f'{floor_num}F',
                lv=lv, priority=pri, recommendation=rec, status=st,
                timestamp=now - timedelta(minutes=age))
            if st == 'handled':
                # 在时限内完成处理
                handled_after = random.randint(10, deadline_min - 5) if deadline_min > 15 else random.randint(5, 15)
                ev.handled_at = ev.timestamp + timedelta(minutes=handled_after)
            db.session.add(ev)

        # 车位违规：只保留一组待处理告警（每层1条），与各层违规车位一一匹配
        parking_event_templates = [
            ('warning', '非新能源车占用充电车位', 'mid', 55, '请引导车辆驶离，为充电车辆预留位置'),
            ('warning', '无权限车辆停入专用车位区', 'high', 75, '专用车位仅供授权车辆使用，通知安保处理'),
            ('warning', '超时停放超24小时', 'mid', 60, '疑似遗弃车辆，联系车主确认'),
            ('warning', '占用应急通道妨碍通行', 'high', 85, '立即通知车主挪车，否则联系拖车'),
            ('info', '离场车牌识别失败需人工处理', 'mid', 55, '通知出口收费员人工核验'),
            ('info', '充电完成超30分钟未驶离', 'mid', 50, '发送挪车提醒，超时将收取占位费'),
            ('info', '套牌车预警，车牌与登记不符', 'high', 95, '立即通知安保核查车辆信息'),
        ]
        # 车位违规：未处理只留2条（额度控制），其余已处理
        random.shuffle(parking_event_templates)
        for floor in range(1, 8):
            t, d, lv, pri, rec = parking_event_templates[(floor - 1) % len(parking_event_templates)]
            deadline_min = 30 if pri >= 75 else (120 if pri >= 50 else 360)
            is_pending = floor <= 2  # 仅前2层未处理
            if is_pending:
                max_age = max(int(deadline_min * 0.8) - 5, 5)
                age = random.randint(3, max_age)
            else:
                age = random.randint(deadline_min + 10, max(now.hour * 60 + now.minute - 5, deadline_min + 15))
            # 选该层真实占用车辆
            floor_vehicles = [v for v in occupied_vehicles if v['spot'].floor == floor]
            vinfo = random.choice(floor_vehicles) if floor_vehicles else random.choice(occupied_vehicles)
            ev = EventLog(
                event_type=t, floor=vinfo['spot'].floor,
                plate_number=vinfo['plate'], category='parking',
                description=d,
                position=f"{vinfo['spot'].floor}F-{vinfo['spot'].zone}区",
                lv=lv, priority=pri, recommendation=rec,
                status='pending' if is_pending else 'handled',
                timestamp=now - timedelta(minutes=age))
            if not is_pending:
                handled_after = random.randint(8, max(deadline_min - 3, 10))
                ev.handled_at = ev.timestamp + timedelta(minutes=handled_after)
                if ev.handled_at > now:
                    ev.handled_at = now - timedelta(minutes=1)
            db.session.add(ev)
        db.session.commit()

        # ====== 统计 ======
        print(f'车位: {ParkingSpot.query.count()} / 占用 {ParkingSpot.query.filter_by(status="occupied").count()}')
        for t, name in [('charger','充电桩'),('light','照明'),('door','门禁'),('gate','地锁'),
                        ('fan','风机'),('elevator','电梯'),('camera','监控'),('ac','空调')]:
            total = Device.query.filter_by(device_type=t).count()
            online = Device.query.filter_by(device_type=t, status='online').count()
            fault = Device.query.filter_by(device_type=t, status='fault').count()
            print(f'{name}: {total} (在线{online} 故障{fault})')
        print(f'车辆: {Vehicle.query.count()} (车牌100%鄂A/鄂WD)')
        print(f'事件: device={EventLog.query.filter_by(category="device").count()} '
              f'parking={EventLog.query.filter_by(category="parking").count()} '
              f'fire={EventLog.query.filter_by(category="fire").count()}')
        print('✅ 初始化完成（武汉车牌 + 数据关联一致）')



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='初始化演示数据（幂等，可重复执行）')
    parser.add_argument('--force', action='store_true', help='清空现有数据后重新填充')
    args = parser.parse_args()
    seed(force=args.force)
