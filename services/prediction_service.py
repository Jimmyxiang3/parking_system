"""数据预测服务 — 到达时间、停车时长、车位偏好、高峰流量"""
from models import db, Vehicle, ParkingRecord, UserProfile, ParkingSpot
from datetime import datetime, timedelta
from sqlalchemy import func, extract


class PredictionService:

    @staticmethod
    def predict_arrival_time(plate_number):
        """预测用户到达时间（基于近30天数据）"""
        thirty_days_ago = datetime.now() - timedelta(days=30)

        records = ParkingRecord.query.filter(
            ParkingRecord.plate_number == plate_number,
            ParkingRecord.entry_time >= thirty_days_ago
        ).all()

        if not records:
            return {'error': '数据不足，无法预测（需要至少30天历史）'}

        # 统计每个小时的入场次数
        hour_counts = {}
        for r in records:
            h = r.entry_time.hour
            hour_counts[h] = hour_counts.get(h, 0) + 1

        # 找到最高频时段
        best_hour = max(hour_counts, key=hour_counts.get)
        best_count = hour_counts[best_hour]
        total = len(records)
        probability = round(best_count / total * 100, 1)

        return {
            'plate_number': plate_number,
            'predicted_hour': best_hour,
            'predicted_time_range': f'{best_hour}:00 - {best_hour + 1}:00',
            'probability_pct': probability,
            'based_on_days': 30,
            'sample_size': total
        }

    @staticmethod
    def predict_parking_duration(plate_number):
        """预测停车时长"""
        profile = UserProfile.query.filter_by(plate_number=plate_number).first()

        if not profile:
            return {'error': '用户画像不存在'}

        # 高频用户 (< 20次/月) 通常 < 2小时
        if profile.monthly_parking_count >= 20:
            predicted = min(profile.avg_duration, 120)
            confidence = '高'
        elif profile.monthly_parking_count >= 10:
            predicted = profile.avg_duration
            confidence = '中'
        elif profile.total_parking_count > 0:
            predicted = profile.avg_duration
            confidence = '低'
        else:
            # 默认工作日平均 2.5 小时
            now = datetime.now()
            if now.weekday() < 5:
                predicted = 150  # 工作日
            else:
                predicted = 120  # 周末
            confidence = '极低（无历史数据）'

        return {
            'plate_number': plate_number,
            'predicted_duration_minutes': round(predicted, 1),
            'confidence': confidence,
            'avg_duration': profile.avg_duration,
            'monthly_count': profile.monthly_parking_count
        }

    @staticmethod
    def predict_spot_preference(plate_number):
        """预测车位偏好（简单决策树逻辑）"""
        profile = UserProfile.query.filter_by(plate_number=plate_number).first()
        vehicle = Vehicle.query.filter_by(plate_number=plate_number).first()

        if not profile:
            return {'error': '用户画像不存在'}

        reasons = []
        target_floor = None
        target_zone = None
        target_type = None

        # 规则1：高频用户 → 偏好楼层
        if profile.preferred_floor and profile.total_parking_count >= 10:
            target_floor = profile.preferred_floor
            reasons.append(f'历史偏好楼层: {target_floor}F')

        # 规则2：长期用户 + 高层偏好 → 高层黄金位置
        if profile.user_type == 'long_term' and profile.preferred_floor:
            if profile.preferred_floor >= 3:
                target_floor = profile.preferred_floor
                reasons.append('长期用户高层偏好')

        # 规则3：电车 → 充电桩楼层
        if vehicle and vehicle.is_electric:
            target_type = 'charging'
            reasons.append('电车需要充电车位')

        # 规则4：大型车 → 大车位
        if vehicle and vehicle.vehicle_size == 'large':
            target_floor = 1  # 大型车优先一楼
            reasons.append('大型车辆需一楼大车位')

        # 规则5：特殊车辆 → 紧急车位
        if vehicle and vehicle.vehicle_type == 'special':
            target_type = 'special'
            reasons.append('特殊车辆优先专用车位')

        # 查找匹配车位
        query = ParkingSpot.query.filter_by(status='idle')
        if target_floor:
            query = query.filter_by(floor=target_floor)
        if target_zone:
            query = query.filter_by(zone=target_zone)
        if target_type:
            if target_type == 'charging':
                query = query.filter_by(is_charging_spot=True)
            elif target_type == 'special':
                query = query.filter_by(is_special=True)

        recommended_spots = query.limit(3).all()

        return {
            'plate_number': plate_number,
            'predicted_floor': target_floor or profile.preferred_floor,
            'predicted_zone': target_zone or profile.preferred_zone,
            'predicted_type': target_type,
            'reasoning': reasons if reasons else ['无足够数据，使用默认策略'],
            'recommended_spots': [s.to_dict() for s in recommended_spots]
        }

    @staticmethod
    def predict_peak_flow(date=None, hour=None):
        """高峰流量预测"""
        if date is None:
            date = (datetime.now() + timedelta(days=1)).date()
        if hour is None:
            hour = datetime.now().hour

        # 取历史同类型日期（同星期几）的数据做简单预测
        # 取最近4周同一天的数据加权平均
        total_predicted = 0
        week_count = 0
        weights = [0.4, 0.3, 0.2, 0.1]  # 越近权重越高

        for i in range(1, 5):
            past_date = date - timedelta(weeks=i)
            stat = PeakHourStat.query.filter_by(
                date=past_date, hour=hour
            ).first()

            if stat:
                total_predicted += stat.entry_count * weights[i - 1]
                week_count += 1

        if week_count == 0:
            # 无历史数据，用工作日/节假日默认值
            is_workday = date.weekday() < 5
            total_predicted = 50 if is_workday else 30

        # 检查节假日影响
        from models import HolidayConfig
        holiday = HolidayConfig.query.filter_by(date=date).first()
        factor = holiday.peak_factor if holiday else 1.0
        total_predicted *= factor

        return {
            'date': date.strftime('%Y-%m-%d'),
            'hour': hour,
            'predicted_entries': round(total_predicted, 1),
            'is_peak': AnalysisService.MORNING_PEAK[0] <= hour < AnalysisService.MORNING_PEAK[1]
                       or AnalysisService.EVENING_PEAK[0] <= hour < AnalysisService.EVENING_PEAK[1],
            'holiday_factor': factor,
            'based_on_weeks': week_count
        }


# 延迟导入避免循环依赖
from services.analysis_service import AnalysisService
