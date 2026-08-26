"""数据分析服务 — 高峰统计、周转率、占用率、异常检测"""
from models import db, ParkingRecord, ParkingSpot, PeakHourStat, HolidayConfig, Vehicle
from datetime import datetime, timedelta
from sqlalchemy import func


class AnalysisService:

    # 高峰时段定义
    MORNING_PEAK = (7, 10)     # 早高峰 7:30-10:00
    EVENING_PEAK = (17, 20)    # 晚高峰 17:30-20:00

    @staticmethod
    def get_peak_hours(date=None):
        """获取高峰时段统计数据"""
        if date is None:
            date = datetime.now().date()

        stats = PeakHourStat.query.filter_by(date=date).order_by(
            PeakHourStat.hour
        ).all()

        if not stats:
            # 从 ParkingRecord 实时计算
            stats = AnalysisService._calc_peak_hours(date)

        return {
            'date': date.strftime('%Y-%m-%d'),
            'morning_peak': {'start': '7:30', 'end': '10:00', 'pct_daily': 25},
            'evening_peak': {'start': '17:30', 'end': '20:00', 'pct_daily': 28},
            'hourly': [s.to_dict() for s in stats]
        }

    @staticmethod
    def _calc_peak_hours(date):
        """从停车记录实时计算每小时的进场/出场统计"""
        results = []
        for h in range(24):
            hour_start = datetime.combine(date, datetime.min.time().replace(hour=h))
            hour_end = hour_start + timedelta(hours=1)

            entry_count = ParkingRecord.query.filter(
                ParkingRecord.entry_time >= hour_start,
                ParkingRecord.entry_time < hour_end
            ).count()

            exit_count = ParkingRecord.query.filter(
                ParkingRecord.exit_time >= hour_start,
                ParkingRecord.exit_time < hour_end
            ).count()

            # 判断是否高峰
            is_peak = False
            peak_type = 'none'
            if AnalysisService.MORNING_PEAK[0] <= h < AnalysisService.MORNING_PEAK[1]:
                is_peak = True
                peak_type = 'morning'
            elif AnalysisService.EVENING_PEAK[0] <= h < AnalysisService.EVENING_PEAK[1]:
                is_peak = True
                peak_type = 'evening'

            # 占用率估算
            total_spots = ParkingSpot.query.count()
            occupied = ParkingSpot.query.filter_by(status='occupied').count()
            occupancy = round(occupied / total_spots * 100, 1) if total_spots > 0 else 0

            stat = PeakHourStat(
                date=date, hour=h,
                entry_count=entry_count,
                exit_count=exit_count,
                is_peak=is_peak,
                peak_type=peak_type,
                occupancy_rate=occupancy
            )
            db.session.add(stat)
            results.append(stat)

        db.session.commit()
        return results

    @staticmethod
    def get_turnover_rate(floor=None, date=None):
        """车位周转率：单位时间内每个车位被使用的次数"""
        if date is None:
            date = datetime.now().date()
        day_start = datetime.combine(date, datetime.min.time())
        day_end = day_start + timedelta(days=1)

        query = ParkingRecord.query.filter(
            ParkingRecord.entry_time >= day_start,
            ParkingRecord.entry_time < day_end
        )
        if floor:
            query = query.filter_by(floor=floor)

        total_records = query.count()
        query2 = ParkingSpot.query
        if floor:
            query2 = query2.filter_by(floor=floor)
        total_spots = query2.count()

        turnover = round(total_records / total_spots, 2) if total_spots > 0 else 0

        return {
            'date': date.strftime('%Y-%m-%d'),
            'floor': floor or '全部',
            'total_spots': total_spots,
            'total_records': total_records,
            'turnover_rate': turnover
        }

    @staticmethod
    def get_occupancy_trend(hours=24):
        """当前占用率及各层分布"""
        floors_data = []
        floors = db.session.query(ParkingSpot.floor).distinct().all()

        for (f,) in floors:
            total = ParkingSpot.query.filter_by(floor=f).count()
            occupied = ParkingSpot.query.filter_by(floor=f, status='occupied').count()
            floors_data.append({
                'floor': f,
                'total': total,
                'occupied': occupied,
                'idle': total - occupied,
                'occupancy_rate': round(occupied / total * 100, 1) if total > 0 else 0
            })

        total_all = ParkingSpot.query.count()
        occupied_all = ParkingSpot.query.filter_by(status='occupied').count()

        # 饱和度告警
        overall_rate = round(occupied_all / total_all * 100, 1) if total_all > 0 else 0
        alert = None
        if overall_rate > 95:
            alert = '停车场已饱和，建议关闭入口'
        elif overall_rate > 85:
            alert = '停车场接近饱和，建议引导至其他停车场'

        return {
            'overall': {
                'total': total_all,
                'occupied': occupied_all,
                'rate': overall_rate,
                'alert': alert
            },
            'floors': floors_data
        }

    @staticmethod
    def detect_anomaly_spots():
        """异常检测：同一车位连续高频率被同一车牌使用"""
        # 检测同一车位最近40次记录是否全是同一车牌
        anomalies = []
        spots = ParkingSpot.query.all()

        for spot in spots:
            records = ParkingRecord.query.filter_by(
                spot_id=spot.id
            ).order_by(ParkingRecord.entry_time.desc()).limit(40).all()

            if len(records) >= 40:
                plates = set(r.plate_number for r in records)
                if len(plates) == 1:
                    anomalies.append({
                        'spot_id': spot.id,
                        'spot_code': spot.spot_code,
                        'plate_number': list(plates)[0],
                        'reason': '连续40次同一车牌，疑似私占车位',
                        'record_count': len(records)
                    })

        return {'anomaly_count': len(anomalies), 'anomalies': anomalies}

    @staticmethod
    def compare_workday_holiday(date=None):
        """工作日/节假日流量对比"""
        if date is None:
            date = datetime.now().date()

        # 判断当天是工作日还是节假日
        is_holiday = date.weekday() >= 5  # 周六日
        holiday_config = HolidayConfig.query.filter_by(date=date).first()
        if holiday_config:
            is_holiday = holiday_config.holiday_type in ('holiday', 'special_event')

        day_type = 'holiday' if is_holiday else 'workday'
        day_start = datetime.combine(date, datetime.min.time())
        day_end = day_start + timedelta(days=1)

        day_records = ParkingRecord.query.filter(
            ParkingRecord.entry_time >= day_start,
            ParkingRecord.entry_time < day_end
        ).count()

        # 工作日基准（取最近工作日数据）
        workday_avg = AnalysisService._get_workday_avg()

        return {
            'date': date.strftime('%Y-%m-%d'),
            'day_type': day_type,
            'is_holiday': is_holiday,
            'today_flow': day_records,
            'workday_avg_flow': workday_avg,
            'diff_pct': round(
                (day_records - workday_avg) / workday_avg * 100, 1
            ) if workday_avg > 0 else 0
        }

    @staticmethod
    def _get_workday_avg():
        """获取最近5个工作日的平均流量"""
        now = datetime.now()
        workday_count = 0
        total_flow = 0
        d = now.date() - timedelta(days=1)

        while workday_count < 5:
            if d.weekday() < 5:  # 工作日
                day_start = datetime.combine(d, datetime.min.time())
                day_end = day_start + timedelta(days=1)
                count = ParkingRecord.query.filter(
                    ParkingRecord.entry_time >= day_start,
                    ParkingRecord.entry_time < day_end
                ).count()
                total_flow += count
                workday_count += 1
            d -= timedelta(days=1)

        return round(total_flow / 5, 1) if workday_count > 0 else 0

    @staticmethod
    def get_weather_impact():
        """天气影响分析（基于已有的记录做统计分析）"""
        # 统计不同天气下的流量
        stats = db.session.query(
            PeakHourStat.weather,
            func.sum(PeakHourStat.entry_count),
            func.avg(PeakHourStat.occupancy_rate)
        ).group_by(PeakHourStat.weather).all()

        return [{
            'weather': s[0] or '未知',
            'total_entry': s[1],
            'avg_occupancy': round(s[2], 1)
        } for s in stats]

    @staticmethod
    def get_device_health():
        """设备健康概览"""
        from models import Device
        devices = Device.query.all()

        total = len(devices)
        online = sum(1 for d in devices if d.status == 'online')
        offline = sum(1 for d in devices if d.status == 'offline')
        fault = sum(1 for d in devices if d.status == 'fault')

        # 地锁超过15000次升降需更换
        high_wear = [d.to_dict() for d in devices
                     if d.open_count and d.open_count > 15000]

        # 15分钟无心跳告警
        fifteen_min_ago = datetime.now() - timedelta(minutes=15)
        no_heartbeat = [d.to_dict() for d in devices
                        if d.last_heartbeat and d.last_heartbeat < fifteen_min_ago]

        return {
            'total': total,
            'online': online,
            'offline': offline,
            'fault': fault,
            'health_rate': round(online / total * 100, 1) if total > 0 else 0,
            'high_wear_devices': high_wear,
            'no_heartbeat_devices': no_heartbeat
        }
