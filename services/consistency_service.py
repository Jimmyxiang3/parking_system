# -*- coding: utf-8 -*-
"""一致性数据服务 — 所有大屏接口共享同一套时间模型，保证数据互相配合

核心思想：
- 以当前时间为种子生成楼层占用率曲线（早高峰高、夜间低）
- 功率与占用率正相关
- 所有接口从这里取数，保证综合态势/车位管理/能耗管理/设备管理数据一致
"""
import math
import random
from datetime import datetime, timedelta


class ConsistencyModel:
    """停车楼实时状态模型"""

    # 楼层基础配置：每层车位类型与功率基准
    FLOOR_CONFIG = {
        1: {'spot_type': 'special', 'base_power_kw': 45.0, 'base_occ': 0.55},
        2: {'spot_type': 'normal', 'base_power_kw': 40.0, 'base_occ': 0.60},
        3: {'spot_type': 'charging', 'base_power_kw': 75.0, 'base_occ': 0.50},
        4: {'spot_type': 'charging', 'base_power_kw': 70.0, 'base_occ': 0.45},
        5: {'spot_type': 'normal', 'base_power_kw': 38.0, 'base_occ': 0.55},
        6: {'spot_type': 'normal', 'base_power_kw': 35.0, 'base_occ': 0.40},
        7: {'spot_type': 'normal', 'base_power_kw': 32.0, 'base_occ': 0.30},
    }

    @staticmethod
    def time_seed(dt=None):
        """按10分钟粒度的时间种子：10分钟内数据稳定，跨10分钟变化"""
        dt = dt or datetime.now()
        minute_bucket = (dt.minute // 10) * 10
        return dt.replace(minute=minute_bucket, second=0, microsecond=0)

    @staticmethod
    def time_factor(dt=None):
        """时段系数：早高峰/晚高峰高，夜间低，平滑过渡"""
        dt = dt or datetime.now()
        h = dt.hour + dt.minute / 60.0

        # 用正弦曲线模拟一天的流量变化
        # 早高峰 7:30-10:00 (峰值)，晚高峰 17:30-20:00 (峰值)
        # 午间小高峰 11:30-13:00，夜间低谷
        def gauss_peak(x, center, width):
            return math.exp(-((x - center) ** 2) / (2 * width ** 2))

        factor = (
            0.25                                    # 基础值
            + 0.75 * gauss_peak(h, 8.5, 1.2)        # 早高峰
            + 0.55 * gauss_peak(h, 12.2, 0.8)       # 午间
            + 0.85 * gauss_peak(h, 18.5, 1.3)       # 晚高峰
        )
        return factor  # 0.25 ~ 1.5

    @staticmethod
    def floor_occupancy(floor, dt=None):
        """某楼层占用数据：返回 (occupied, total, rate, state)
        state: 空闲 / 较满 / 饱和"""
        from models import ParkingSpot
        dt = dt or datetime.now()
        total = ParkingSpot.query.filter_by(floor=floor).count()
        if total == 0:
            return 0, 0, 0.0, '空闲'

        config = ConsistencyModel.FLOOR_CONFIG.get(floor, ConsistencyModel.FLOOR_CONFIG[2])
        # 占用率 = 基础占用 + 时段系数影响，封顶 0.98
        rate = config['base_occ'] + ConsistencyModel.time_factor(dt) * 0.25
        rate = min(max(rate, 0.05), 0.98)

        occupied = int(total * rate)
        # 按时间种子加小幅抖动，保持10分钟稳定
        seed = int(ConsistencyModel.time_seed(dt).timestamp()) + floor * 1000
        rnd = random.Random(seed)
        occupied += rnd.randint(-3, 3)
        occupied = min(max(occupied, 0), total)
        rate = occupied / total

        if rate < 0.5:
            state = '空闲'
        elif rate < 0.85:
            state = '较满'
        else:
            state = '饱和'
        return occupied, total, round(rate * 100, 1), state

    @staticmethod
    def overall_occupancy(dt=None):
        """全场占用汇总"""
        dt = dt or datetime.now()
        total_all = 0
        occupied_all = 0
        for f in range(1, 8):
            occ, tot, rate, state = ConsistencyModel.floor_occupancy(f, dt)
            total_all += tot
            occupied_all += occ
        rate_all = round(occupied_all / total_all * 100, 1) if total_all else 0
        return occupied_all, total_all, rate_all

    @staticmethod
    def floor_power_kw(floor, dt=None):
        """某楼层实时功率(kW)：与占用率正相关 + 基础设备负载"""
        dt = dt or datetime.now()
        config = ConsistencyModel.FLOOR_CONFIG.get(floor, ConsistencyModel.FLOOR_CONFIG[2])
        occ, total, rate, state = ConsistencyModel.floor_occupancy(floor, dt)

        # 功率 = 基础负载 + 占用率 * 楼层功率系数
        power = config['base_power_kw'] * (0.5 + rate / 100.0)
        # 时间种子抖动
        seed = int(ConsistencyModel.time_seed(dt).timestamp()) + floor * 500
        rnd = random.Random(seed)
        power += rnd.uniform(-3, 3)
        return round(max(power, 5.0), 1)

    @staticmethod
    def hourly_traffic(dt=None):
        """当前小时的进出车辆（与占用率一致）
        夜间极低(≈5辆/时)，早高峰可达80+，用系数平方放大差距"""
        dt = dt or datetime.now()
        factor = ConsistencyModel.time_factor(dt)
        f2 = factor * factor
        entries = min(88, int(2 + f2 * 80))
        exits = min(75, int(1 + f2 * 65))
        return entries, exits

    @staticmethod
    def energy_totals(dt=None):
        """今日能耗汇总：峰值/平均/总量
        总量 = 今日0点到现在的逐小时积分，随时间只增不减
        总量 = 各分类之和，保证数字严格对账"""
        dt = dt or datetime.now()
        powers = [ConsistencyModel.floor_power_kw(f, dt) for f in range(1, 8)]
        current_total = sum(powers)
        avg = round(current_total / 7, 1)
        peak = round(max(powers) * 1.4, 1)

        # 今日累计能耗：逐小时积分（0点到现在），随时间单调递增
        cat_energy = {'charging': 0.0, 'lighting': 0.0,
                      'electromechanical': 0.0, 'supporting': 0.0}
        for h in range(0, dt.hour + 1):
            t_hour = dt.replace(hour=h, minute=0, second=0, microsecond=0)
            p = [ConsistencyModel.floor_power_kw(f, t_hour) for f in range(1, 8)]
            total_h = sum(p)
            charging_h = ConsistencyModel.floor_power_kw(3, t_hour) + ConsistencyModel.floor_power_kw(4, t_hour)
            lighting_h = total_h * 0.22
            mech_h = total_h * 0.25
            supporting_h = max(total_h - charging_h - lighting_h - mech_h, 10)
            cat_energy['charging'] += charging_h * 0.55
            cat_energy['lighting'] += lighting_h * 0.55
            cat_energy['electromechanical'] += mech_h * 0.55
            cat_energy['supporting'] += supporting_h * 0.55

        # 四舍五入，总量 = 各分类之和（严格对账）
        cat_energy = {k: round(v) for k, v in cat_energy.items()}
        total_kwh = sum(cat_energy.values())

        # 当前设备分类功率（用于占比展示）
        charging_power = ConsistencyModel.floor_power_kw(3, dt) + ConsistencyModel.floor_power_kw(4, dt)
        lighting_power = current_total * 0.22
        mech_power = current_total * 0.25
        supporting = max(current_total - charging_power - lighting_power - mech_power, 10)

        return {
            'peak_power_kw': round(peak),
            'average_power_kw': round(avg),
            'total_energy_kwh': total_kwh,
            'current_total_kw': round(current_total),
            'floors': [{'floor': f'F{f}', 'power_kw': round(p, 1)}
                       for f, p in zip(range(1, 8), powers)],
            'categories': [
                {'category': 'charging', 'name': '充电系统',
                 'power_kw': round(charging_power),
                 'energy_kwh': cat_energy['charging']},
                {'category': 'lighting', 'name': '照明系统',
                 'power_kw': round(lighting_power),
                 'energy_kwh': cat_energy['lighting']},
                {'category': 'electromechanical', 'name': '机电系统',
                 'power_kw': round(mech_power),
                 'energy_kwh': cat_energy['electromechanical']},
                {'category': 'supporting', 'name': '配套系统',
                 'power_kw': round(supporting),
                 'energy_kwh': cat_energy['supporting']},
            ]
        }

    @staticmethod
    def dispatch_advice(dt=None):
        """智能调度建议：每层按自身数据生成差异化负荷描述+预测+执行建议
        占用数直接从DB实际状态取（occupied+violation），与楼层视图严格一致"""
        from models import ParkingSpot
        dt = dt or datetime.now()
        result = []
        for f in range(1, 8):
            tot = ParkingSpot.query.filter_by(floor=f).count()
            occ = ParkingSpot.query.filter_by(floor=f, status='occupied').count()
            vio = ParkingSpot.query.filter_by(floor=f, status='violation').count()
            flt = ParkingSpot.query.filter_by(floor=f, status='fault').count()
            # 在场车辆 = 占用 + 违规（违规车也算占位）
            cars = occ + vio
            rate = round(cars / tot * 100, 1) if tot else 0
            # 状态判定与楼层状态接口一致
            state = '空闲' if rate < 50 else ('较满' if rate < 85 else '饱和')
            available = tot - cars - flt
            # 趋势：用模型预测未来1小时（模型用于趋势，实际数用于展示）
            future = ConsistencyModel.floor_occupancy(f, dt + timedelta(hours=1))
            future_rate = future[2]
            future_occ, _, _, future_state = future
            if future_rate > rate + 3:
                trend = '上升'
            elif future_rate < rate - 3:
                trend = '下降'
            else:
                trend = '平稳'

            config = ConsistencyModel.FLOOR_CONFIG.get(f, ConsistencyModel.FLOOR_CONFIG[2])
            floor_desc = {
                1: '专用车辆层', 3: '充电车位层', 4: '充电车位层',
            }.get(f, '普通车位层')

            # 负荷描述：结合楼层定位与剩余车位
            load_desc = (f'{f}F{floor_desc}：当前占用{cars}/{tot}，剩余{available}个车位，'
                         f'占用率{rate}%')
            if f in (3, 4):
                load_desc += '，充电桩层'
            if f == 1:
                load_desc += '，含应急车位'

            # 预测：差异化描述（基于趋势+未来状态）
            if future_state != state:
                prediction = (f'未来1小时占用率将{future_state}'
                              f'（{rate}%→{future_rate}%），预计'
                              f'{"新增" if future_occ > cars else "减少"}'
                              f'{abs(future_occ - cars)}辆车')
            elif trend == '上升':
                prediction = (f'占用率缓慢上升（{rate}%→{future_rate}%），'
                              f'预计新增{future_occ - cars}辆车')
            elif trend == '下降':
                prediction = (f'占用率缓慢下降（{rate}%→{future_rate}%），'
                              f'预计减少{cars - future_occ}辆车')
            else:
                prediction = f'占用率保持平稳（{rate}%→{future_rate}%）'

            # 执行建议：按楼层类型+状态差异化
            if state == '饱和':
                if f in (3, 4):
                    method = '关闭充电层引导，将非充电车辆分流至F5-F7；通知充电完成车辆及时驶离'
                else:
                    method = '入口引导屏提示本层已满，引导车辆分流至相邻空闲楼层'
            elif state == '较满':
                if f in (3, 4):
                    method = '引导屏显示剩余充电位数量；对充满车辆发送挪车提醒释放车位'
                elif f == 1:
                    method = '专用层仅对授权车辆开放，引导临时车辆前往上层停放'
                else:
                    method = f'引导屏显示本层剩余{available}位；建议提前将车辆导向{f+1 if f < 7 else 6}F'
            elif trend == '上升':
                if f in (3, 4):
                    method = '预开启充电层通风散热，增派运维人员巡查充电桩'
                else:
                    method = '预开启该层照明，坡道口安排引导员准备疏导'
            else:
                if f in (3, 4):
                    method = f'充电层负荷正常，空闲充电桩{available}台可承接错峰充电'
                else:
                    method = f'车位充裕（剩余{available}位），维持常规运营即可'

            result.append({
                'floor': f,
                'floor_desc': floor_desc,
                'state': state,
                'rate': rate,
                'occupied': cars,          # 实际在场车辆（占用+违规）
                'available': available,
                'trend': trend,
                'load_desc': load_desc,
                'prediction': prediction,
                'advice': method,
            })
        return result

    @staticmethod
    def energy_advice(dt=None):
        """能耗AI建议：基于功率结构"""
        dt = dt or datetime.now()
        totals = ConsistencyModel.energy_totals(dt)
        advice = []
        charging = totals['categories'][0]['power_kw']
        if charging > 120:
            advice.append('充电系统负荷较高，建议引导部分车辆错峰充电')
        if ConsistencyModel.time_factor(dt) < 0.4:
            advice.append('当前为低谷时段，可安排设备维护与检修')
        if totals['current_total_kw'] > totals['average_power_kw'] * 1.3:
            advice.append('当前总功率超平均30%，检查是否有设备异常耗电')
        if not advice:
            advice.append('能耗状态正常，各系统运行平稳')
        return advice


# 便捷函数
def floor_occupancy(floor, dt=None):
    return ConsistencyModel.floor_occupancy(floor, dt)

def overall_occupancy(dt=None):
    return ConsistencyModel.overall_occupancy(dt)

def floor_power_kw(floor, dt=None):
    return ConsistencyModel.floor_power_kw(floor, dt)

def energy_totals(dt=None):
    return ConsistencyModel.energy_totals(dt)

def dispatch_advice(dt=None):
    return ConsistencyModel.dispatch_advice(dt)

def energy_advice(dt=None):
    return ConsistencyModel.energy_advice(dt)

def time_factor(dt=None):
    return ConsistencyModel.time_factor(dt)
